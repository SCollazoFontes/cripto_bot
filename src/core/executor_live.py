# src/core/executor_live.py
"""
Ejecutor "live" minimalista para estrategias en modo paper/live.

Responsabilidades:
- Enviar órdenes al broker (`BaseBroker`) y esperar hasta estado terminal.
- Hacer `poll` ligero mediante `broker.refresh()` y `get_open_orders()`.
- Cancelar por timeout para evitar "órdenes zombi".
- Medir latencia señal→fill (ms) con timestamps del broker.

Diseño:
- Sin dependencias del resto de core/* para evitar ciclos.
- No gestiona PnL ni persistencia (eso seguirá en io/decisions_log).
- Tipado estricto y comportamiento determinista en tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import time

from brokers.base import (
    BaseBroker,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)


@dataclass
class LiveExecConfig:
    """Parámetros de espera/cancelación."""

    poll_interval_sec: float = 0.05  # intervalo de sondeo de estado
    max_wait_sec: float = 5.0  # timeout para llevar la orden a terminal (si no llega, cancelamos)


@dataclass
class LiveExecResult:
    """Resultado final de la ejecución."""

    order: Order
    latency_ms: float | None  # None si no hay fill
    canceled_by_timeout: bool


class LiveExecutor:
    """
    Ejecuta órdenes contra un `BaseBroker` y garantiza estados terminales.

    Patrón de uso:
        ex = LiveExecutor(broker, LiveExecConfig(...))
        res = ex.market_buy("BTCUSDT", qty=0.01)
        assert res.order.status in (OrderStatus.FILLED, OrderStatus.CANCELED, ...)
    """

    def __init__(self, broker: BaseBroker, config: LiveExecConfig | None = None) -> None:
        self._broker = broker
        self._cfg = config or LiveExecConfig()

    # --------------------
    # API de conveniencia
    # --------------------

    def market_buy(self, symbol: str, qty: float) -> LiveExecResult:
        req = OrderRequest(symbol=symbol, side=OrderSide.BUY, type=OrderType.MARKET, quantity=qty)
        return self._place_and_wait(req)

    def market_sell(self, symbol: str, qty: float) -> LiveExecResult:
        req = OrderRequest(symbol=symbol, side=OrderSide.SELL, type=OrderType.MARKET, quantity=qty)
        return self._place_and_wait(req)

    def limit_buy(
        self, symbol: str, qty: float, price: float, tif: TimeInForce = TimeInForce.GTC
    ) -> LiveExecResult:
        req = OrderRequest(
            symbol=symbol,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=qty,
            price=price,
            time_in_force=tif,
        )
        return self._place_and_wait(req)

    def limit_sell(
        self, symbol: str, qty: float, price: float, tif: TimeInForce = TimeInForce.GTC
    ) -> LiveExecResult:
        req = OrderRequest(
            symbol=symbol,
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            quantity=qty,
            price=price,
            time_in_force=tif,
        )
        return self._place_and_wait(req)

    # --------------------
    # Núcleo
    # --------------------

    def _place_and_wait(self, req: OrderRequest) -> LiveExecResult:
        """
        Envía la orden y espera hasta estado terminal.
        Si vence `max_wait_sec`, intenta `cancel_order` y devuelve el último estado observado.
        """
        order = self._broker.place_order(req)
        if order.status in _TERMINAL:
            return LiveExecResult(
                order=order, latency_ms=_latency_ms(order), canceled_by_timeout=False
            )

        deadline = time.monotonic() + self._cfg.max_wait_sec
        last_seen = order

        while time.monotonic() < deadline:
            # Le damos oportunidad al adapter de llenar resting orders
            self._broker.refresh()
            # Consultamos órdenes abiertas del símbolo y buscamos la nuestra
            open_orders = self._broker.get_open_orders(req.symbol)
            updated = _find_by_id(open_orders, order.order_id)
            if updated is not None:
                last_seen = updated
                if updated.status in _TERMINAL:
                    return LiveExecResult(
                        order=updated, latency_ms=_latency_ms(updated), canceled_by_timeout=False
                    )
            else:
                # Ya no está en "open"; puede que el broker la haya cerrado en FILLED/CANCELED.
                # Intentamos recuperar su último snapshot observado.
                if last_seen.status in _TERMINAL:
                    return LiveExecResult(
                        order=last_seen,
                        latency_ms=_latency_ms(last_seen),
                        canceled_by_timeout=False,
                    )
                # Si desapareció sin terminal, salimos del bucle para cancelar defensivamente
                break

            time.sleep(self._cfg.poll_interval_sec)

        # Timeout ⇒ intentamos cancelar y devolvemos el estado resultante
        try:
            # evitar pasar None como order_id
            oid = getattr(order, "order_id", None) or getattr(order, "id", None)
            if oid is None:
                out = last_seen
            else:
                out = self._broker.cancel_order(req.symbol, oid)
        except Exception:
            out = last_seen
        return LiveExecResult(order=out, latency_ms=_latency_ms(out), canceled_by_timeout=True)

    # --------------------
    # Utilidades
    # --------------------

    def sync_position(self, symbol: str) -> float:
        """Devuelve la posición neta (base) reportada por el broker para `symbol`."""
        return self._broker.get_position(symbol)


# --------------------
# Funciones auxiliares
# --------------------


_TERMINAL = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}


def _latency_ms(o: Order) -> float | None:
    """Latencia (ms) usando `submitted_ts` → `updated_ts` si terminó en FILLED; None en otro caso."""
    if o.status is OrderStatus.FILLED:
        return max(0.0, (o.updated_ts - o.submitted_ts) * 1000.0)
    return None


def _find_by_id(orders: Iterable[Order], order_id: str | int | None) -> Order | None:
    if order_id is None:
        return None
    for o in orders:
        # Algunos adapters usan 'order_id' o 'id'
        if getattr(o, "order_id", None) is not None and str(o.order_id) == str(order_id):
            return o
        if getattr(o, "id", None) is not None and str(o.id) == str(order_id):
            return o
    return None

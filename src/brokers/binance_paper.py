# src/brokers/binance_paper.py
"""
Broker PAPER estilo Binance: sin red, emula un exchange sencillo con:
- Libro “implícito” vía mid/price recibido desde fuera
- Validaciones mínimas: notional, step_size/tick_size si se facilitan
- Estados de orden: NEW -> PARTIALLY_FILLED -> FILLED / CANCELED
- Soporta TIF: GTC e IOC (cancelación de remanente en IOC)
- Fills con comisiones (fee_pct) y slippage (bps) aplicados a cada ejecución

Este broker está pensado para “paper live” (simular órdenes en vivo sin tocar
red) y tests/engine-like. No persiste a disco. Las órdenes se guardan en memoria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from brokers.base import (
    BaseBroker,
    BrokerError,
    Fill,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    SymbolFilters,
    TimeInForce,
)
from core.execution.costs import CostModel


def _mk_order_kw(**kwargs: Any):
    # narrow the mypy surface; runtime tries the constructor
    try:
        return Order(**kwargs)
    except Exception as err:
        raise BrokerError(f"Error creando Order: {err}") from err


def _mk_fill_kw(**kwargs: Any):
    try:
        return Fill(**kwargs)
    except Exception as err:
        raise BrokerError(f"Error creando Fill: {err}") from err


# ------------------------------------------------------------------------
# Config y tipos internos


@dataclass(frozen=True)
class _ExecCfg:
    # Porcentaje de comisión (p.ej., 0.0004 = 4 bps) aplicado sobre notional |price * |qty||
    fee_pct: float = 0.0
    # Porcentaje de deslizamiento (p.ej., 0.0005 = 5 bps). Sube/baja el precio efectivo según el lado.
    slip_pct: float = 0.0


@dataclass
class _O:
    symbol: str
    side: OrderSide
    type: OrderType | str | None
    price: float | None
    requested_qty: float
    filled_qty: float
    status: OrderStatus
    tif: TimeInForce | str
    submitted_ts: float
    updated_ts: float
    fills: list[Fill]
    reason: str | None = None
    client_order_id: str | None = None


@dataclass
class _F:
    price: float
    qty: float
    ts: float
    fee: float


_TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELED}


# ------------------------------------------------------------------------
# Broker


class BinancePaperBroker(BaseBroker):
    """
    Broker “de mentirijilla” a lo Binance que no se conecta a red. Mantiene
    saldo interno y un diccionario de órdenes. El matching es simplificado:
    - MARKET: ejecuta al precio recibido (mid) en el tick; con slippage y fee
    - LIMIT: ejecuta si se toca o supera el precio límite; con slippage/fee
    """

    def __init__(
        self,
        *,
        symbol_filters: dict[str, SymbolFilters] | None = None,
        exec_cfg: _ExecCfg | None = None,
        cost_model: CostModel | None = None,
    ) -> None:
        self._filters: dict[str, SymbolFilters] = symbol_filters or {}
        self._exec: _ExecCfg = exec_cfg or _ExecCfg()
        self._orders: dict[int, _O] = {}
        self._positions: dict[str, float] = {}
        self._usdt: float = 100.0
        self._next_id: int = 1
        # Modelo de costes opcional (slippage y fees realistas)
        self._cost_model: CostModel | None = cost_model
        # Último precio conocido por símbolo (para ejecutar MARKET inmediatamente)
        self._last_px: dict[str, float] = {}
        # Callback opcional para reportar fills (instrumentación de costes)
        # Firma: on_fill(details: dict) -> None
        from collections.abc import Callable

        self.on_fill: Callable[[dict], None] | None = None

    # ------------------------------------------------------------------ #
    # API obligatoria de BaseBroker

    def account_info(self) -> dict[str, object]:
        return {
            "cash": self._usdt,
            "positions": dict(self._positions),
        }

    # Compat wrappers
    def get_account(self) -> dict:
        """Devuelve estructura compatible con engine.py (_mark_to_equity_row)."""
        return {
            "balances": {
                "USDT": {
                    "free": self._usdt,
                    "locked": 0.0,
                }
            }
        }

    def get_symbol_filters(self, symbol: str) -> dict[str, Any]:
        # devolver dict genérico para coincidir con BaseBroker typing
        sf = self._filters.get(symbol, {})
        return dict(sf) if sf else {}

    def get_position(self, symbol: str) -> float:
        return float(self._positions.get(symbol, 0.0))

    def place_order(self, req: OrderRequest) -> Order:
        return self.submit_order(req)

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return self.open_orders(symbol)

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        outs: list[Order] = []
        for o in self._orders.values():
            if symbol and o.symbol != symbol:
                continue
            outs.append(self._to_order(o))
        return outs

    def submit_order(self, req: OrderRequest) -> Order:
        # Validación: rechazar dicts
        if isinstance(req, dict):
            raise TypeError(
                f"submit_order() requiere OrderRequest, recibió dict. "
                f"Use OrderRequest(symbol=..., side=..., order_type=..., quantity=...) "
                f"en lugar de dict. Dict recibido: {req}"
            )

        # normalizar side a OrderSide (no str)
        side_val: OrderSide
        try:
            side_val = OrderSide(str(req.side)) if not isinstance(req.side, OrderSide) else req.side
        except Exception:
            side_val = OrderSide.BUY

        tval = getattr(req, "type", None) or getattr(req, "order_type", None)
        try:
            tval = OrderType(str(tval)) if tval is not None else OrderType.MARKET
        except Exception:
            tval = OrderType.MARKET

        qty_val = getattr(req, "quantity", None)
        if qty_val is None:
            qty_val = getattr(req, "qty", None)
        if qty_val is None:
            qty_val = getattr(req, "requested_qty", None)
        if qty_val is None:
            qty_val = 0.0
        # garantizar float (evitar None)
        try:
            qty_val_f = float(qty_val)
        except Exception:
            qty_val_f = 0.0

        tif_val = getattr(req, "time_in_force", None) or getattr(req, "tif", None)
        try:
            tif_val = TimeInForce(str(tif_val)) if tif_val is not None else TimeInForce.GTC
        except Exception:
            tif_val = TimeInForce.GTC

        # validación local (usa el objeto req; _validate_req también usa aliases)
        self._validate_req(req)
        oid = self._next_id
        self._next_id += 1

        now = self._now()
        o = _O(
            symbol=req.symbol,
            side=side_val,  # ya normalizado a OrderSide
            type=tval,
            price=req.price,
            requested_qty=qty_val_f,
            filled_qty=0.0,
            status=OrderStatus.NEW,
            tif=tif_val,
            submitted_ts=now,
            updated_ts=now,
            fills=[],
            reason=None,
            client_order_id=req.client_order_id,
        )
        self._orders[oid] = o

        # Si es MARKET y tenemos un precio actual, ejecutar inmediatamente
        if tval is OrderType.MARKET and req.symbol in self._last_px:
            current_price = self._last_px[req.symbol]
            self._fill_market(o, current_price, now)

        return self._to_order(o, oid=oid)

    def cancel_order(self, symbol: str, order_id: str | int) -> Order:
        try:
            oid = int(order_id)
        except Exception as err:
            raise BrokerError(f"Order id inválido: {order_id}") from err
        if oid not in self._orders:
            raise BrokerError(f"Order {oid} no existe")
        o = self._orders[oid]
        if o.status in _TERMINAL:
            return self._to_order(o, oid=oid)
        o.status = OrderStatus.CANCELED
        o.updated_ts = self._now()
        return self._to_order(o, oid=oid)

    def fetch_order(self, symbol: str, order_id: str | int) -> Order:
        try:
            oid = int(order_id)
        except Exception as err:
            raise BrokerError(f"Order id inválido: {order_id}") from err
        if oid not in self._orders:
            raise BrokerError(f"Order {oid} no existe")
        return self._to_order(self._orders[oid], oid=oid)

    def name(self) -> str:
        return "binance_paper"

    # ----------------------------------------------------------------
    def on_tick(self, *, symbol: str, mid: float, ts: float) -> None:
        # Guardar el último precio para este símbolo
        self._last_px[symbol] = mid

        # Ejecuta/avanza cualquier orden OPEN del símbolo
        # (matching muy simple: MARKET al mid; LIMIT al cruzar precio)
        for _oid, o in list(self._orders.items()):
            if o.symbol != symbol or o.status in _TERMINAL:
                continue

            if o.type is OrderType.MARKET:
                self._fill_market(o, mid, ts)
            elif o.type is OrderType.LIMIT:
                if (o.side is OrderSide.BUY and mid <= (o.price or mid)) or (
                    o.side is OrderSide.SELL and mid >= (o.price or mid)
                ):
                    self._fill_limit(o, mid, ts)

            # IOC: cancelar remanente si queda algo tras el intento de fill
            if (
                o.tif is TimeInForce.IOC
                and o.filled_qty < o.requested_qty
                and o.status is OrderStatus.NEW
            ):
                o.status = OrderStatus.CANCELED
                o.updated_ts = ts

    # ------------------------------------------------------------------ #
    # Internos: fills & conversiones

    def _fill_market(self, o: _O, mid: float, ts: float) -> None:
        if o.status in _TERMINAL:
            return
        # Slippage y fee por ejecución (modelo simple: 1 fill por tick)
        px = self._effective_price(mid, o.side, role="taker")
        qty_left = o.requested_qty - o.filled_qty
        if qty_left <= 0:
            return
        fill_qty = qty_left
        fee = self._fee_amount(px, fill_qty, role="taker")

        o.fills.append(_mk_fill_kw(price=px, qty=fill_qty, timestamp=ts, commission=fee))
        o.filled_qty += fill_qty
        o.updated_ts = ts
        if o.filled_qty >= o.requested_qty:
            o.status = OrderStatus.FILLED

        self._apply_cash_position_effects(o.side, o.symbol, px, fill_qty, fee)

        # Instrumentación: emitir detalles del fill si hay callback
        if self.on_fill:
            try:
                side_s = "buy" if o.side is OrderSide.BUY else "sell"
                self.on_fill(
                    {
                        "timestamp": ts,
                        "symbol": o.symbol,
                        "side": side_s,
                        "role": "taker",
                        "mid_price": mid,
                        "effective_price": px,
                        "qty": fill_qty,
                        "fee": fee,
                        "type": "MARKET",
                    }
                )
            except Exception:
                pass

    def _fill_limit(self, o: _O, mid: float, ts: float) -> None:
        if o.status in _TERMINAL:
            return
        assert o.price is not None
        cross = (o.side is OrderSide.BUY and mid <= o.price) or (
            o.side is OrderSide.SELL and mid >= o.price
        )
        if not cross:
            return
        # Lógica de fill simple: todo de golpe al precio límite con slippage
        px = self._effective_price(o.price, o.side, role="maker")
        qty_left = o.requested_qty - o.filled_qty
        if qty_left <= 0:
            return
        fill_qty = qty_left
        fee = self._fee_amount(px, fill_qty, role="maker")

        o.fills.append(_mk_fill_kw(price=px, qty=fill_qty, timestamp=ts, commission=fee))
        o.filled_qty += fill_qty
        o.updated_ts = ts
        o.status = OrderStatus.FILLED
        self._apply_cash_position_effects(o.side, o.symbol, px, fill_qty, fee)

        # Instrumentación: emitir detalles del fill si hay callback
        if self.on_fill:
            try:
                side_s = "buy" if o.side is OrderSide.BUY else "sell"
                self.on_fill(
                    {
                        "timestamp": ts,
                        "symbol": o.symbol,
                        "side": side_s,
                        "role": "maker",
                        "mid_price": mid,
                        "effective_price": px,
                        "qty": fill_qty,
                        "fee": fee,
                        "type": "LIMIT",
                        "limit_price": float(o.price),
                    }
                )
            except Exception:
                pass

    # Conversión interna -> público
    def _to_order(self, o: _O, *, oid: int | None = None) -> Order:
        """
        Intenta construir Order con distintas firmas. Si no encaja, crea objeto simple.
        """
        # 1) Firma estilo (id, symbol, side, type, price, requested_qty, filled_qty, status,
        #     tif, ts, fills, reason, client_order_id)
        try:
            return _mk_order_kw(
                id=oid,
                symbol=o.symbol,
                side=o.side,
                type=o.type,
                price=o.price,
                requested_qty=o.requested_qty,
                filled_qty=o.filled_qty,
                status=o.status,
                tif=o.tif,
                ts=o.updated_ts,
                fills=list(o.fills),
                reason=o.reason,
                client_order_id=o.client_order_id,
            )
        except Exception:
            pass

        # 2) Firma estilo (order_id, symbol, side, order_type, price, qty/executed_qty, status, time_in_force,
        #                  timestamp, fills, reason, client_order_id)
        try:
            qty_field = "qty" if hasattr(o, "qty") else "filled_qty"
            return _mk_order_kw(
                order_id=oid,
                symbol=o.symbol,
                side=o.side,
                order_type=o.type,
                price=o.price,
                **{qty_field: o.filled_qty},
                status=o.status,
                time_in_force=o.tif,
                timestamp=o.updated_ts,
                fills=list(o.fills),
                reason=o.reason,
                client_order_id=o.client_order_id,
            )
        except Exception:
            pass

        # 3) Fallback: construimos un objeto Order mínimo por compatibilidad
        return _mk_order_kw(
            id=oid,
            symbol=o.symbol,
            side=o.side,
            type=o.type,
            price=o.price,
            requested_qty=o.requested_qty,
            filled_qty=o.filled_qty,
            status=o.status,
            tif=o.tif,
        )

    # ------------------------------------------------------------------ #
    # Validaciones y utilidades

    def _validate_req(self, req: OrderRequest) -> None:
        f = self._filters.get(req.symbol)
        if f:
            if req.price is not None and f.get("tick_size") is not None:
                self._enforce_tick_size(req.price, float(f["tick_size"]))
            if f.get("min_notional") is not None:
                qty_val = getattr(req, "quantity", None) or getattr(req, "qty", None) or 0.0
                # Solo validar notional si hay precio (LIMIT), no para MARKET sin precio
                if req.price is not None:
                    try:
                        notional = req.price * float(qty_val)
                    except Exception:
                        notional = 0.0
                    if notional < float(f["min_notional"]):
                        raise BrokerError(
                            f"minNotional violado: {notional:.8f} < {float(f['min_notional']):.8f}"
                        )

            if f.get("step_size") is not None:
                qty_val = getattr(req, "quantity", None) or getattr(req, "qty", None) or 0.0
                try:
                    self._enforce_step_size(float(qty_val), float(f["step_size"]))
                except Exception as err:
                    raise BrokerError(f"step_size inválido para quantity={qty_val}") from err

        if getattr(req, "type", None) == OrderType.LIMIT and req.price is None:
            raise BrokerError("Orden LIMIT sin price")

        if req.side not in (OrderSide.BUY, OrderSide.SELL):
            raise BrokerError(f"Side inválido: {req.side}")

    def _enforce_tick_size(self, price: float, tick: float) -> None:
        q = round(price / tick) * tick
        if abs(q - price) > 1e-12:
            raise BrokerError(f"tick_size {tick} violado: price={price}")

    def _enforce_step_size(self, qty: float, step: float) -> None:
        q = round(qty / step) * step
        if abs(q - qty) > 1e-12:
            raise BrokerError(f"step_size {step} violado: quantity={qty}")

    def _apply_slippage(self, px: float, side: OrderSide) -> float:
        """Aplica slippage al precio según la dirección."""
        if side is OrderSide.BUY:
            return px * (1.0 + self._exec.slip_pct)
        else:
            return px * (1.0 - self._exec.slip_pct)

    # --- CostModel helpers -------------------------------------------------
    def _effective_price(
        self, base_price: float, side: OrderSide, *, role: Literal["maker", "taker"]
    ) -> float:
        """Devuelve el precio efectivo aplicando el modelo de slippage si existe."""
        cm = self._cost_model
        if cm is None:
            return self._apply_slippage(base_price, side)
        side_s = "buy" if side is OrderSide.BUY else "sell"
        try:
            return float(cm.effective_price(base_price=base_price, side=side_s, role=role))
        except Exception:
            # Fallback robusto
            return self._apply_slippage(base_price, side)

    def _fee_amount(self, price: float, qty: float, *, role: Literal["maker", "taker"]) -> float:
        cm = self._cost_model
        notional = abs(price * qty)
        if cm is None:
            return notional * self._exec.fee_pct
        try:
            return float(cm.fee_amount(notional=notional, role=role))
        except Exception:
            return notional * self._exec.fee_pct

    @property
    def cost_model(self) -> CostModel | None:
        return self._cost_model

    def _now(self) -> float:
        """Retorna timestamp actual en segundos."""
        import time

        return time.time()

    def _apply_cash_position_effects(
        self,
        side: OrderSide,
        symbol: str,
        px: float,
        qty: float,
        fee: float,
    ) -> None:
        """Actualiza cash y posición tras un fill."""
        if side is OrderSide.BUY:
            self._usdt -= px * qty + fee
            self._positions[symbol] = self._positions.get(symbol, 0.0) + qty
        else:
            self._usdt += px * qty - fee
            self._positions[symbol] = self._positions.get(symbol, 0.0) - qty
        if abs(self._positions.get(symbol, 0.0)) < 1e-12:
            self._positions.pop(symbol, None)

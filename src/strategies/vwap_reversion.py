# src/strategies/vwap_reversion.py
"""
Estrategia: VWAP Reversion (intra/micro-barras)

Idea
----
Opera desvíos transitorios del precio respecto a un VWAP de ventana corta
utilizando bandas por z-score (entrada si |z| > z_entry; cierre si |z| < z_exit),
con protección por take-profit / stop-loss relativos.

Compatibilidad
--------------
- Usa el runtime común (`src.core.strategy_runtime`).
- Devuelve `OrderRequest` del paquete `src.strategies.base`.
- No depende de enums `Side/Decision` (usa literales 'BUY'/'SELL' y
  'OPEN_LONG'/'OPEN_SHORT'/'CLOSE'), para ser compatible con tu base actual.

Parámetros (por defecto entre paréntesis)
-----------------------------------------
- vwap_window (50):     tamaño de ventana para VWAP y z-score.
- z_entry (1.5):        umbral de entrada por z-score (|z| > z_entry).
- z_exit (0.5):         umbral de salida por vuelta a banda (|z| < z_exit).
- take_profit_pct (0.006): cierre por beneficio relativo al entry_price.
- stop_loss_pct (0.004):  cierre por pérdida relativa al entry_price.
- qty_frac (1.0):       fracción del equity en apertura (0..1).
- min_vol (1e-12):      volumen mínimo efectivo para VWAP (si no, usa 1.0).
- warmup (vwap_window): mínimo de barras antes de activar señales.

Entrada esperada (bar)
----------------------
dict con claves estándar: {'t','open','high','low','close','qty'/'volume','price','symbol'}.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from brokers.base import OrderRequest, OrderSide
from strategies.base import PositionState, Strategy, register_strategy


@register_strategy("vwap_reversion")
class VWAPReversionStrategy(Strategy):
    def __init__(self, params: dict | None = None):
        p = params or {}
        self.vwap_window: int = int(p.get("vwap_window", 50))
        self.z_entry: float = float(p.get("z_entry", 1.5))
        self.z_exit: float = float(p.get("z_exit", 0.5))
        self.take_profit_pct: float = float(p.get("take_profit_pct", 0.006))
        self.stop_loss_pct: float = float(p.get("stop_loss_pct", 0.004))
        self.qty_frac: float = float(p.get("qty_frac", 1.0))
        self.min_vol: float = float(p.get("min_vol", 1e-12))
        self.warmup: int = int(p.get("warmup", self.vwap_window))

        self._prices: deque[float] = deque(maxlen=self.vwap_window)
        self._vols: deque[float] = deque(maxlen=self.vwap_window)
        self._sum_pv = 0.0
        self._sum_v = 0.0
        self._sum_p = 0.0
        self._sum_p2 = 0.0
        self._n = 0

    # ---- utilidades internas ----
    def _push(self, price: float, vol: float) -> None:
        if len(self._prices) == self._prices.maxlen:
            old_p = self._prices[0]
            old_v = self._vols[0]
            self._sum_pv -= old_p * old_v
            self._sum_v -= old_v
            self._sum_p -= old_p
            self._sum_p2 -= old_p * old_p
        self._prices.append(price)
        self._vols.append(vol)
        self._sum_pv += price * vol
        self._sum_v += vol
        self._sum_p += price
        self._sum_p2 += price * price

    def _vwap(self) -> float | None:
        if self._sum_v <= self.min_vol:
            return None
        return self._sum_pv / self._sum_v

    def _mean_std(self) -> tuple[float | None, float | None]:
        m = len(self._prices)
        if m < 2:
            return None, None
        mean = self._sum_p / m
        var = max(0.0, (self._sum_p2 / m) - (mean * mean))
        std = var**0.5
        return mean, std if std > 0.0 else None

    def _zscore(self, price: float) -> float | None:
        mean, std = self._mean_std()
        if mean is None or std is None or std <= 0.0:
            return None
        return (price - mean) / std

    def _tp_sl_signal(self, price: float, state: PositionState) -> OrderRequest | None:
        if not state.has_position or state.avg_price <= 0.0:
            return None
        pnl = (price - state.avg_price) / state.avg_price
        if state.side == "SHORT":
            pnl = -pnl
        if pnl >= self.take_profit_pct:
            return OrderRequest(
                decision="CLOSE",
                side="SELL" if state.side == "LONG" else "BUY",
                qty=1.0,
                price=None,
                reason="take_profit",
                meta={"pnl": pnl},
            )
        if pnl <= -self.stop_loss_pct:
            return OrderRequest(
                decision="CLOSE",
                side="SELL" if state.side == "LONG" else "BUY",
                qty=1.0,
                price=None,
                reason="stop_loss",
                meta={"pnl": pnl},
            )
        return None

    # ---- callbacks de Strategy ----
    def on_start(self, context: dict) -> None:
        pass

    def on_bar(self, bar: dict, state: PositionState) -> OrderRequest | None:
        price = float(bar.get("close") or bar.get("price") or 0.0)
        vol = float(bar.get("qty") or bar.get("volume") or 1.0)
        symbol = bar.get("symbol")
        if vol < self.min_vol:
            vol = 1.0

        self._push(price, vol)
        self._n += 1

        if self._n < self.warmup:
            return None

        # TP/SL si hay posición
        tp_sl = self._tp_sl_signal(price, state)
        if tp_sl is not None:
            return tp_sl

        vwap = self._vwap()
        z = self._zscore(price)
        if vwap is None or z is None:
            return None

        if not state.has_position:
            if z <= -abs(self.z_entry):
                return OrderRequest(
                    decision="OPEN_LONG",
                    side="BUY",
                    qty=max(0.0, min(1.0, self.qty_frac)),
                    price=None,
                    reason="z_entry_long",
                    meta={"z": z, "vwap": vwap, "price": price},
                    symbol=symbol,
                )
            if z >= abs(self.z_entry):
                return OrderRequest(
                    decision="OPEN_SHORT",
                    side="SELL",
                    qty=max(0.0, min(1.0, self.qty_frac)),
                    price=None,
                    reason="z_entry_short",
                    meta={"z": z, "vwap": vwap, "price": price},
                    symbol=symbol,
                )
            return None

        if abs(z) <= abs(self.z_exit):
            return OrderRequest(
                decision="CLOSE",
                side="SELL" if state.side == "LONG" else "BUY",
                qty=1.0,
                price=None,
                reason="z_exit_close",
                meta={"z": z, "vwap": vwap, "price": price},
                symbol=symbol,
            )

        return None

    def on_end(self, context: dict) -> None:
        pass

    def on_bar_live(self, broker, executor, symbol: str, bar: dict[str, Any]) -> None:
        # guardia: evitar None en operaciones
        entry_price = self.position.entry_price or 0.0
        current_price = float(bar.get("close", 0.0))

        if entry_price > 0 and current_price / entry_price - 1 < -0.02:
            order = OrderRequest(
                symbol=symbol,
                side=OrderSide.SELL,
                qty=self.position.qty,
                reason="reversion_exit_long",
            )
            broker.submit_order(executor, order)
            return

        qty_position = self.position.qty if self.position.qty is not None else 0.1

        vwap = self._vwap()
        z = self._zscore(current_price)
        if vwap is None or z is None:
            return

        if not self.position.is_open:
            if z <= -abs(self.z_entry):
                order = OrderRequest(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    qty=qty_position,
                    reason="reversion_entry_long",
                )
                broker.submit_order(executor, order)
                return
            if z >= abs(self.z_entry):
                order = OrderRequest(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    qty=qty_position,
                    reason="reversion_entry_short",
                )
                broker.submit_order(executor, order)
                return
        else:
            if abs(z) <= abs(self.z_exit):
                order = OrderRequest(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    qty=self.position.qty,
                    reason="reversion_exit_short",
                )
                broker.submit_order(executor, order)
                return

        # Fallback en caso de error
        order = OrderRequest(
            symbol=str(symbol) if symbol else "UNKNOWN",
            side=OrderSide.SELL,
            qty=0.1,
            reason="reversion_fallback",
        )
        broker.submit_order(executor, order)

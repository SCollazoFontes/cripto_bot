# src/bars/composite.py
"""
CompositeBarBuilder: combina múltiples reglas de cierre (tick, volumen, dólar, imbalance).

Objetivo
--------
- Permitir que las micro-velas se cierren según una política configurable:
  * policy="any" (OR): cerrar cuando CUALQUIER umbral activo se alcance → más rápido
  * policy="all" (AND): cerrar cuando TODOS los umbrales activos se alcancen → barras más "densas"

Reglas soportadas
-----------------
- tick_limit: número de trades
- qty_limit: suma de cantidades (∑ qty)
- value_limit: suma de valores (∑ price*qty)
- imbal_limit: desequilibrio acumulado en modo "qty" (∑ signo*qty) o "tick" (∑ signo*1)

Convención de is_buyer_maker (Binance):
- True  => buyer fue maker  => el taker fue vendedor => signo = -1
- False => buyer fue taker  => el taker fue comprador => signo = +1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from bars.base import Bar, BarBuilder, Trade

__all__ = ["CompositeBarBuilder"]


@dataclass
class CompositeBarBuilder(BarBuilder):
    # Umbrales (opcionales). Al menos uno debe estar definido.
    tick_limit: int | None = None
    qty_limit: float | None = None
    value_limit: float | None = None
    imbal_limit: float | None = None
    imbal_mode: Literal["qty", "tick"] = "qty"

    # Política de cierre: "any" (OR) o "all" (AND)
    policy: Literal["any", "all"] = "any"

    # Estado interno
    _buffer: list[Trade] = field(default_factory=list, init=False, repr=False)
    _tick_count: int = field(default=0, init=False, repr=False)
    _qty_sum: float = field(default=0.0, init=False, repr=False)
    _value_sum: float = field(default=0.0, init=False, repr=False)
    _imbalance: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            self.tick_limit is None
            and self.qty_limit is None
            and self.value_limit is None
            and self.imbal_limit is None
        ):
            raise ValueError("Debe especificarse al menos un umbral (tick/qty/value/imbal).")
        if self.tick_limit is not None and self.tick_limit < 1:
            raise ValueError("tick_limit debe ser >= 1 si se usa.")
        if self.qty_limit is not None and self.qty_limit <= 0:
            raise ValueError("qty_limit debe ser > 0 si se usa.")
        if self.value_limit is not None and self.value_limit <= 0:
            raise ValueError("value_limit debe ser > 0 si se usa.")
        if self.imbal_limit is not None and self.imbal_limit <= 0:
            raise ValueError("imbal_limit debe ser > 0 si se usa.")
        if self.imbal_mode not in ("qty", "tick"):
            raise ValueError("imbal_mode debe ser 'qty' o 'tick'.")
        if self.policy not in ("any", "all"):
            raise ValueError("policy debe ser 'any' o 'all'.")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def update(self, trade: Trade) -> Bar | None:
        self._buffer.append(trade)
        # Recuentos
        self._tick_count += 1
        self._qty_sum += trade.qty
        self._value_sum += trade.price * trade.qty

        sign = +1.0 if not trade.is_buyer_maker else -1.0
        incr = trade.qty if self.imbal_mode == "qty" else 1.0
        self._imbalance += sign * incr

        # Evaluar reglas activas
        conds: list[bool] = []
        if self.tick_limit is not None:
            conds.append(self._tick_count >= int(self.tick_limit))
        if self.qty_limit is not None:
            conds.append(self._qty_sum >= float(self.qty_limit))
        if self.value_limit is not None:
            conds.append(self._value_sum >= float(self.value_limit))
        if self.imbal_limit is not None:
            conds.append(abs(self._imbalance) >= float(self.imbal_limit))

        should_close = any(conds) if self.policy == "any" else all(conds)
        if should_close:
            bar = self._build_bar(self._buffer)
            self.reset()
            return bar
        return None

    def reset(self) -> None:
        self._buffer.clear()
        self._tick_count = 0
        self._qty_sum = 0.0
        self._value_sum = 0.0
        self._imbalance = 0.0

    def get_current_trades(self) -> list[Trade]:
        return list(self._buffer)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    @staticmethod
    def _build_bar(trades: list[Trade]) -> Bar:
        if not trades:
            raise ValueError("No hay trades para construir la barra.")
        first = trades[0]
        last = trades[-1]
        prices = [t.price for t in trades]
        volume = sum(t.qty for t in trades)
        dval = sum(t.price * t.qty for t in trades)
        return Bar(
            open=first.price,
            high=max(prices),
            low=min(prices),
            close=last.price,
            volume=volume,
            start_time=first.timestamp,
            end_time=last.timestamp,
            trade_count=len(trades),
            dollar_value=dval,
        )

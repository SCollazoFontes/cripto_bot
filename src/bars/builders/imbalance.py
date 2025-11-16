# src/bars/imbalance.py

"""
Micro-velas por desequilibrio comprador/vendedor.

Regla
-----
Se cierra cuando el desequilibrio acumulado alcanza `imbal_limit`.
El desequilibrio puede definirse por:
- modo "qty": ∑(signo * qty), donde signo = +1 si taker es comprador, -1 si vendedor
- modo "tick": ∑(signo * 1) por trade (ignora qty)

Convención de `is_buyer_maker` (Binance):
- True  => buyer fue maker  => el taker fue vendedor => signo = -1
- False => buyer fue taker  => el taker fue comprador => signo = +1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from bars.base import Bar, BarBuilder, Trade

__all__ = ["ImbalanceBarBuilder"]


@dataclass
class ImbalanceBarBuilder(BarBuilder):
    """
    Construye micro-velas por desequilibrio acumulado.

    Parámetros
    ----------
    imbal_limit : float
        Umbral absoluto de desequilibrio para cerrar la barra. Debe ser > 0.
    mode : Literal["qty", "tick"]
        "qty" usa ∑(signo * qty). "tick" usa ∑(signo * 1).
    """

    imbal_limit: float
    mode: Literal["qty", "tick"] = "qty"
    _buffer: list[Trade] = field(default_factory=list, init=False, repr=False)
    _imbalance: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.imbal_limit <= 0:
            raise ValueError("imbal_limit debe ser > 0.")
        if self.mode not in ("qty", "tick"):
            raise ValueError('mode debe ser "qty" o "tick".')

    def update(self, trade: Trade) -> Bar | None:
        """Incorpora un trade y cierra si |desequilibrio| >= imbal_limit."""
        self._buffer.append(trade)

        sign = +1.0 if not trade.is_buyer_maker else -1.0
        incr = trade.qty if self.mode == "qty" else 1.0
        self._imbalance += sign * incr

        if abs(self._imbalance) >= float(self.imbal_limit):
            bar = self._build_bar(self._buffer)
            self.reset()
            return bar

        return None

    def reset(self) -> None:
        """Vacía estado interno para la siguiente barra."""
        self._buffer.clear()
        self._imbalance = 0.0

    def get_current_trades(self) -> list[Trade]:
        """Devuelve copia del buffer actual."""
        return list(self._buffer)

    @staticmethod
    def _build_bar(trades: list[Trade]) -> Bar:
        """Construye microvela OHLCV con tiempos y recuento."""
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

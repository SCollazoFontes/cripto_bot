# python -m tools.run_stream --symbol btcusdt --rule volume_qty --limit 0.5

"""
Micro-velas por valor negociado (price * qty).

Regla
-----
Se cierra una barra cuando la suma de valores (∑ price * qty) alcanza
`value_limit`. No partimos trades: si el último trade hace superar el
umbral, se incluye entero y luego se cierra.

Notas
-----
- Asumimos que `price` ya está en la divisa cotizada del par. En BTCUSDT,
  el valor es en USDT. No se hace conversión FX adicional.

Uso típico
----------
    builder = DollarBarBuilder(value_limit=10_000.0)
    for trade in stream:
        bar = builder.update(trade)
        if bar:
            do_something(bar)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .base import Bar, BarBuilder, Trade

__all__ = ["DollarBarBuilder"]


@dataclass
class DollarBarBuilder(BarBuilder):
    """
    Construye micro-velas por valor negociado acumulado (∑ price * qty).

    Parámetros
    ----------
    value_limit : float
        Umbral de valor para cerrar una barra. Debe ser > 0.

    Atributos
    ---------
    _buffer : List[Trade]
        Trades acumulados de la barra en construcción.
    _value_sum : float
        Valor acumulado de la barra activa.
    """

    value_limit: float
    _buffer: List[Trade] = field(default_factory=list, init=False, repr=False)
    _value_sum: float = field(default=0.0, init=False, repr=False)

    # ---------------------------------------------------------------------
    # Validación de construcción
    # ---------------------------------------------------------------------
    def __post_init__(self) -> None:
        if not isinstance(self.value_limit, (int, float)):
            raise TypeError("value_limit debe ser numérico (int o float).")
        if self.value_limit <= 0:
            raise ValueError("value_limit debe ser > 0.")

    # ---------------------------------------------------------------------
    # API pública (BarBuilder)
    # ---------------------------------------------------------------------
    def update(self, trade: Trade) -> Optional[Bar]:
        """
        Incorpora un trade. Si ∑ (price * qty) >= value_limit, cierra la barra.
        """
        self._buffer.append(trade)
        self._value_sum += trade.price * trade.qty

        if self._value_sum >= float(self.value_limit):
            bar = self._build_bar(self._buffer)
            self.reset()
            return bar

        return None

    def reset(self) -> None:
        """Vacía buffer y valor acumulado para la siguiente barra."""
        self._buffer.clear()
        self._value_sum = 0.0

    def get_current_trades(self) -> List[Trade]:
        """Devuelve una copia del buffer para evitar mutaciones externas."""
        return list(self._buffer)

    # ---------------------------------------------------------------------
    # Helpers internos
    # ---------------------------------------------------------------------
    @staticmethod
    def _build_bar(trades: List[Trade]) -> Bar:
        """Construye la microvela OHLCV a partir de la lista de trades."""
        if not trades:
            raise ValueError("No hay trades para construir la barra.")

        first = trades[0]
        last = trades[-1]
        prices = [t.price for t in trades]
        volume = sum(t.qty for t in trades)

        return Bar(
            open=first.price,
            high=max(prices),
            low=min(prices),
            close=last.price,
            volume=volume,
            start_time=first.timestamp,
            end_time=last.timestamp,
            trade_count=len(trades),
        )

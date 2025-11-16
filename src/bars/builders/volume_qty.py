# src/bars/volume_qty.py

"""
Micro-velas por volumen acumulado (cantidad de unidades).

Regla
-----
Se cierra una barra cuando la suma de cantidades (∑ qty) alcanza `qty_limit`.
Por simplicidad, este builder no parte trades. Si el último trade supera
el umbral, se incluye entero y luego se cierra. Para ejecución exacta al
límite sería necesario "split de trade" (TODO si se requiere).

Motivación
----------
Las "volume bars" normalizan por actividad real de negociación (cantidad).
Aportan más barras cuando hay mayor participación y menos cuando el mercado
está lento, reduciendo el sesgo temporal de las velas cronológicas.

"""

from __future__ import annotations

from dataclasses import dataclass, field

from bars.base import Bar, BarBuilder, Trade

__all__ = ["VolumeQtyBarBuilder"]


@dataclass
class VolumeQtyBarBuilder(BarBuilder):
    """
    Construye micro-velas por volumen acumulado (∑ qty).

    Parámetros
    ----------
    qty_limit : float
        Volumen objetivo para cerrar una barra. Debe ser > 0.

    Atributos
    ---------
    _buffer : List[Trade]
        Trades acumulados de la barra en construcción.
    _qty_sum : float
        Volumen acumulado de la barra activa.
    """

    qty_limit: float
    _buffer: list[Trade] = field(default_factory=list, init=False, repr=False)
    _qty_sum: float = field(default=0.0, init=False, repr=False)

    # ---------------------------------------------------------------------
    # Validación de construcción
    # ---------------------------------------------------------------------
    def __post_init__(self) -> None:
        if not isinstance(self.qty_limit, (int, float)):
            raise TypeError("qty_limit debe ser numérico (int o float).")
        if self.qty_limit <= 0:
            raise ValueError("qty_limit debe ser > 0.")

    # ---------------------------------------------------------------------
    # API pública (BarBuilder)
    # ---------------------------------------------------------------------
    def update(self, trade: Trade) -> Bar | None:
        """
        Incorpora un trade. Si ∑ qty >= qty_limit, cierra y devuelve la barra.

        Nota
        ----
        Este builder **no** parte trades. Si el trade hace que ∑ qty supere
        el límite, se incluye completo y luego se cierra.
        """
        self._buffer.append(trade)
        self._qty_sum += trade.qty

        if self._qty_sum >= float(self.qty_limit):
            bar = self._build_bar(self._buffer)
            self.reset()
            return bar

        return None

    def reset(self) -> None:
        """Vacía buffer y volumen acumulado para la siguiente barra."""
        self._buffer.clear()
        self._qty_sum = 0.0

    def get_current_trades(self) -> list[Trade]:
        """Devuelve una copia del buffer para evitar mutaciones externas."""
        return list(self._buffer)

    # ---------------------------------------------------------------------
    # Helpers internos
    # ---------------------------------------------------------------------
    @staticmethod
    def _build_bar(trades: list[Trade]) -> Bar:
        """Construye la microvela OHLCV a partir de la lista de trades."""
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

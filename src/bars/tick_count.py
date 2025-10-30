# scr/bars/tick_count.py
"""
Implementación concreta de micro-velas por recuento de trades (tick bars).

Regla de cierre
---------------
Se cierra una barra cuando el número de trades acumulados alcanza `tick_limit`.
Cada trade incrementa el contador en 1, con independencia de su tamaño/volumen.

Motivación
----------
Las "tick bars" normalizan el eje horizontal por actividad (número de
transacciones) en vez de por tiempo. En mercados con ráfagas de actividad,
producen más barras cuando hay más información y menos cuando el mercado está
lento, reduciendo el "time deformation bias" típico de las velas temporales.

Uso típico
----------
    builder = TickCountBarBuilder(tick_limit=100)
    for trade in stream:
        closed = builder.update(trade)
        if closed:
            # procesar la microvela cerrada
            do_something(closed)

Notas de implementación
-----------------------
- Se apoya en las estructuras base definidas en `bars/base.py`:
  - `Trade`: trade normalizado (precio, qty, timestamp, is_buyer_maker).
  - `Bar`: microvela con OHLC, volumen, tiempos y recuento.
  - `BarBuilder`: interfaz abstracta.
- `update` devuelve `Bar` solo cuando se cierra; en caso contrario, `None`.
- El estado interno se vacía tras cada cierre mediante `reset`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .base import Bar, BarBuilder, Trade

__all__ = ["TickCountBarBuilder"]


@dataclass
class TickCountBarBuilder(BarBuilder):
    """
    Creador incremental de micro-velas por recuento de trades.

    Parameters
    ----------
    tick_limit : int
        Número de trades necesarios para cerrar una barra. Debe ser >= 1.

    Attributes
    ----------
    _buffer : List[Trade]
        Trades acumulados para la barra en construcción.
    _count : int
        Número de trades actuales en el buffer (por claridad y velocidad).
    """

    tick_limit: int
    _buffer: List[Trade] = field(default_factory=list, init=False, repr=False)
    _count: int = field(default=0, init=False, repr=False)

    # -------------------------------------------------------------------------
    # Validaciones de construcción
    # -------------------------------------------------------------------------
    def __post_init__(self) -> None:
        if not isinstance(self.tick_limit, int):
            raise TypeError("tick_limit debe ser un entero.")
        if self.tick_limit < 1:
            raise ValueError("tick_limit debe ser >= 1.")

    # -------------------------------------------------------------------------
    # API pública (BarBuilder)
    # -------------------------------------------------------------------------
    def update(self, trade: Trade) -> Optional[Bar]:
        """
        Incorpora un trade y, si se alcanzó el límite, cierra y devuelve la barra.

        Estrategia:
        1) Añadir el trade al buffer y aumentar el contador.
        2) Si `self._count == self.tick_limit` → construir `Bar`, limpiar estado y devolverla.
        3) En caso contrario → retornar `None`.

        Parameters
        ----------
        trade : Trade
            Trade normalizado recibido del productor (WS/REST) del exchange.

        Returns
        -------
        Optional[Bar]
            La barra cerrada al alcanzar el umbral de trades, o `None` si aún no se cerró.
        """
        # 1) Acumular
        self._buffer.append(trade)
        self._count += 1

        # 2) ¿Se alcanzó el límite?
        if self._count >= self.tick_limit:
            bar = self._build_bar(self._buffer)
            self.reset()  # 3) Limpiar estado para la siguiente microvela
            return bar

        # Aún no cerramos
        return None

    def reset(self) -> None:
        """Reinicia el estado interno (buffer y contador) tras cerrar una barra."""
        self._buffer.clear()
        self._count = 0

    def get_current_trades(self) -> List[Trade]:
        """
        Devuelve una **copia** del buffer actual por seguridad (evita mutaciones externas).

        Returns
        -------
        List[Trade]
            Copia superficial de los trades acumulados en la barra activa.
        """
        return list(self._buffer)

    # -------------------------------------------------------------------------
    # Helpers internos
    # -------------------------------------------------------------------------
    @staticmethod
    def _build_bar(trades: List[Trade]) -> Bar:
        """
        Construye la microvela OHLCV a partir de los trades acumulados.

        Reglas:
        - open  = precio del primer trade
        - close = precio del último trade
        - high  = máx(precio)
        - low   = mín(precio)
        - volume = suma de qty
        - start_time = timestamp del primer trade
        - end_time   = timestamp del último trade
        - trade_count = len(trades)

        Parameters
        ----------
        trades : List[Trade]
            Lista de trades a agregar. Debe tener al menos un elemento.

        Returns
        -------
        Bar
            Microvela resultante.
        """
        if not trades:
            # Esto no debería ocurrir dado el flujo de `update`, pero es defensivo.
            raise ValueError("No hay trades para construir la barra.")

        # Apertura y cierre
        first = trades[0]
        last = trades[-1]

        # OHLC y volumen
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

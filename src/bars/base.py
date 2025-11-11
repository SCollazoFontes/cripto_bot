# scr/bars/base.py
"""
Módulo base para la construcción de micro-velas ("bars").

Define las interfaces principales:
- Trade: representa un trade individual recibido del exchange.
- Bar: representa una microvela (agregación mínima).
- BarBuilder: interfaz abstracta para construir barras con reglas
  (tick, volumen, dólar, imbalance).

Cada implementación concreta (por ejemplo, TickCountBarBuilder) deberá
heredar de BarBuilder y definir la lógica de cierre de una barra.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

__all__ = ["Trade", "Bar", "BarBuilder"]


# ============================================================
# Trade
# ============================================================


@dataclass
class Trade:
    """
    Trade individual recibido del exchange.

    Atributos
    ---------
    price : float
        Precio de ejecución del trade.
    qty : float
        Cantidad negociada en el trade.
    timestamp : datetime
        Hora del trade (UTC).
    is_buyer_maker : bool
        True si el comprador fue el maker (lado pasivo).
    """

    price: float
    qty: float
    timestamp: datetime
    is_buyer_maker: bool


# ============================================================
# Bar
# ============================================================


@dataclass
class Bar:
    """
    Microvela agregada a partir de uno o más trades.

    Atributos
    ---------
    open : float
        Precio de apertura.
    high : float
        Precio máximo.
    low : float
        Precio mínimo.
    close : float
        Precio de cierre.
    volume : float
        Volumen total negociado.
    start_time : datetime
        Marca temporal del primer trade (UTC).
    end_time : datetime
        Marca temporal del último trade (UTC).
    trade_count : int
        Número total de trades incluidos.
    dollar_value : Optional[float]
        Suma( price * qty ) de la barra
    """

    open: float
    high: float
    low: float
    close: float
    volume: float
    start_time: datetime
    end_time: datetime
    trade_count: int
    dollar_value: float | None = None


# ============================================================
# BarBuilder
# ============================================================


class BarBuilder(ABC):
    """
    Interfaz abstracta para construir microvelas incrementalmente.

    Flujo típico
    ------------
    builder = TickCountBarBuilder(tick_limit=100)
    for trade in stream:
        bar = builder.update(trade)
        if bar:
            yield bar
    """

    @abstractmethod
    def update(self, trade: Trade) -> Bar | None:
        """
        Actualiza el estado con un trade. Si se cumple la regla de cierre,
        devuelve la nueva Bar.

        Parámetros
        ----------
        trade : Trade
            Trade normalizado.

        Retorna
        -------
        Optional[Bar]
            Barra cerrada si se cumple la regla; None en caso contrario.
        """
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        """Reinicia el estado interno del builder tras cerrar una barra."""
        raise NotImplementedError

    @abstractmethod
    def get_current_trades(self) -> list[Trade]:
        """
        Devuelve la lista de trades acumulados de la barra activa.

        Retorna
        -------
        List[Trade]
            Copia de los trades pendientes de agregación.
        """
        raise NotImplementedError

"""
Módulo base para la construcción de micro-velas ("bars").

Define las interfaces principales:
- Trade: representa un trade individual recibido del exchange.
- Bar: representa una microvela (agregación mínima).
- BarBuilder: interfaz abstracta para la construcción incremental de barras
  según una regla (tick, volumen, dólar, imbalance...).

Cada implementación concreta (por ejemplo, TickCountBarBuilder)
deberá heredar de BarBuilder y definir la lógica de cuándo se cierra una barra.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

# ============================================================
# Trade
# ============================================================


@dataclass
class Trade:
    """
    Representa un trade individual recibido del exchange.

    Attributes
    ----------
    price : float
        Precio al que se ejecutó el trade.
    qty : float
        Cantidad (volumen) del trade.
    timestamp : datetime
        Momento en que se ejecutó el trade (normalizado a UTC).
    is_buyer_maker : bool
        True si el trade fue ejecutado por el "maker" (lado pasivo del libro).
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
    Representa una microvela agregada a partir de uno o más trades.

    Attributes
    ----------
    open : float
        Precio de apertura.
    high : float
        Precio máximo.
    low : float
        Precio mínimo.
    close : float
        Precio de cierre.
    volume : float
        Volumen total negociado durante la barra.
    start_time : datetime
        Marca temporal del primer trade incluido.
    end_time : datetime
        Marca temporal del último trade incluido.
    trade_count : int
        Número total de trades agregados en la barra.
    """

    open: float
    high: float
    low: float
    close: float
    volume: float
    start_time: datetime
    end_time: datetime
    trade_count: int


# ============================================================
# BarBuilder
# ============================================================


class BarBuilder(ABC):
    """
    Interfaz abstracta para la construcción incremental de microvelas.

    Cada clase hija definirá una regla específica de cierre, como:
    - Número fijo de trades (`TickCountBarBuilder`)
    - Volumen acumulado (`VolumeBarBuilder`)
    - Imbalance de compra/venta (`ImbalanceBarBuilder`)
    - Dólar negociado (`DollarBarBuilder`)

    El flujo típico de uso es:
        builder = TickCountBarBuilder(tick_limit=100)
        for trade in stream:
            bar = builder.update(trade)
            if bar:
                yield bar
    """

    @abstractmethod
    def update(self, trade: Trade) -> Optional[Bar]:
        """
        Actualiza el estado interno con un nuevo trade y,
        si se cumple la condición de cierre, devuelve una nueva Bar.

        Parameters
        ----------
        trade : Trade
            Nuevo trade recibido desde el exchange.

        Returns
        -------
        Optional[Bar]
            La barra cerrada si se cumple la regla de cierre, None en caso contrario.
        """
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        """
        Reinicia el estado interno del builder después de cerrar una barra.
        """
        raise NotImplementedError

    @abstractmethod
    def get_current_trades(self) -> List[Trade]:
        """
        Devuelve la lista de trades acumulados actualmente en la barra activa.

        Returns
        -------
        List[Trade]
            Lista de trades pendientes de agregación.
        """
        raise NotImplementedError

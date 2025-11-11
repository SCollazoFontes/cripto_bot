# src/core/types.py
"""
Tipos y estructuras comunes para el *runtime* y los *brokers*.
Pensado para ser estable frente a pequeñas variaciones entre módulos y tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

# ------------------------------ Literales ---------------------------------

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]
OrderStatus = Literal["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"]

TimeInForce = Literal["GTC", "IOC", "FOK"]

# ------------------------------ TypedDicts --------------------------------


class SymbolFilters(TypedDict, total=False):
    """
    Filtros por símbolo. Todas las claves son opcionales para ser tolerantes:
    - min_qty: cantidad mínima por orden.
    - step_size: escalón de cantidad.
    - min_notional: valor mínimo (qty * price).
    - tick_size: escalón de precio.
    """

    min_qty: float
    step_size: float
    min_notional: float
    tick_size: float


class AccountInfo(TypedDict, total=False):
    cash: float
    equity: float
    # compatibilidad con brokers que expongan más campos
    positions: dict[str, Any]


class TradeRow(TypedDict, total=False):
    t: float  # epoch seconds (o ms, depende del writer)
    side: str | None
    price: float
    qty: float
    cash: float
    equity: float
    fee: float
    reason: str


class EquityRow(TypedDict, total=False):
    t: float
    price: float
    qty: float
    cash: float
    equity: float


class DecisionRow(TypedDict, total=False):
    t: float
    decision: str
    meta: dict[str, Any]


# ------------------------------ Dataclasses -------------------------------


@dataclass
class Fill:
    """
    Relleno de una orden.
    Se incluyen alias de campos para ser tolerantes con código existente.
    """

    price: float
    qty: float
    commission: float = 0.0
    commission_asset: str = ""
    ts: float | None = None  # alias genérico de timestamp
    timestamp: float | None = None  # algunos tests usan 'timestamp'


@dataclass
class Order:
    """
    Modelo de orden con varios alias para máxima compatibilidad.
    """

    # Identificadores (se aceptan ambos nombres)
    id: str | None = None
    order_id: str | None = None

    symbol: str = ""
    side: OrderSide | None = None
    type: OrderType | None = None  # alias: 'order_type' en algunos módulos

    # Precio/cantidades solicitadas y ejecutadas
    price: float | None = None
    requested_qty: float | None = None  # alias común
    qty: float | None = None  # algunos módulos usan 'qty'
    filled_qty: float = 0.0
    executed_qty: float = 0.0  # alias usado en algunos tests
    avg_price: float | None = None

    status: OrderStatus | None = None
    tif: TimeInForce | None = None
    time_in_force: TimeInForce | None = None  # alias

    # Tiempos y *client id*
    ts: float | None = None
    timestamp: float | None = None  # alias
    submitted_ts: float | None = None
    updated_ts: float | None = None
    client_order_id: str | None = None

    # Fills y motivo
    fills: list[Fill] = field(default_factory=list)
    reason: str = ""

    def effective_id(self) -> str | None:
        return self.id or self.order_id

    def effective_qty(self) -> float:
        if self.executed_qty:
            return self.executed_qty
        if self.filled_qty:
            return self.filled_qty
        if self.qty:
            return float(self.qty)
        if self.requested_qty:
            return float(self.requested_qty)
        return 0.0


@dataclass
class OrderRequest:
    """
    Petición de orden tolerante a distintos nombres de campo.
    Campos *oficiales* en este proyecto: symbol, side, order_type, qty, price, tif.
    También se aceptan alias: type, quantity, time_in_force.
    """

    symbol: str
    side: OrderSide
    order_type: OrderType = "MARKET"
    qty: float = 0.0
    price: float | None = None
    tif: TimeInForce | None = None

    # Alias para compatibilidad
    type: OrderType | None = None
    quantity: float | None = None
    time_in_force: TimeInForce | None = None

    # Campos extra usados en estrategias/rastreos
    reason: str = ""
    decision: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    client_order_id: str | None = None


@dataclass
class Account:
    cash: float = 0.0
    qty: float = 0.0
    avg_price: float = 0.0

    def equity(self, last_price: float) -> float:
        return float(self.cash + self.qty * last_price)


@dataclass
class PositionState:
    """
    Snapshot de la posición de estrategia con helpers para compatibilidad.
    """

    side: OrderSide | None = None
    qty: float = 0.0
    avg_price: float = 0.0

    @property
    def has_position(self) -> bool:
        return self.qty > 0.0

    @property
    def side_mult(self) -> int:
        if self.side == "BUY":
            return 1
        if self.side == "SELL":
            return -1
        return 0


__all__ = [
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "TimeInForce",
    "SymbolFilters",
    "AccountInfo",
    "TradeRow",
    "EquityRow",
    "DecisionRow",
    "Fill",
    "Order",
    "OrderRequest",
    "Account",
    "PositionState",
]

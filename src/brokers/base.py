# src/brokers/base.py
"""
Interfaz mínima y utilidades comunes para brokers (paper/live).

Este módulo define:
- Protocolo `BaseBroker` que deben implementar los adapters (p. ej., Binance paper/live).
- Tipos/enums para órdenes y fills.
- Errores específicos de broker para manejar flujos robustos.
- Funciones puras de normalización (precio/cantidad) según filtros del símbolo:
  tickSize, stepSize, minNotional, límites de precio/cantidad.

Diseño:
- Mantener este archivo **sin dependencias** del resto de `core/*` para evitar ciclos.
- Tipado estricto (mypy) y estilo limpio (ruff/black).
- Timestamps en segundos (float, época UNIX) para simplicidad cross-broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypedDict, runtime_checkable

# =========================
# Enums y tipos de dominio
# =========================


class OrderSide(str, Enum):
    """Lado de la orden normalizado."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Tipo de orden normalizado."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(str, Enum):
    """Política temporal de ejecución (usadas en LIMIT)."""

    GTC = "GTC"  # Good-Til-Cancel
    IOC = "IOC"  # Immediate-Or-Cancel
    FOK = "FOK"  # Fill-Or-Kill


class OrderStatus(str, Enum):
    """Estados de orden normalizados."""

    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class Fill:
    """Ejecución parcial o total de una orden."""

    price: float
    qty: float
    commission: float = 0.0
    commission_asset: str = "USDT"
    timestamp: float = 0.0


@dataclass
class Order:
    """Estado de una orden."""

    id: str | None = None
    order_id: str | None = None  # alias que algunos adapters usan
    symbol: str = ""
    side: OrderSide | None = None
    type: OrderType | None = None
    order_type: OrderType | None = None  # alias
    price: float | None = None
    requested_qty: float | None = None
    qty: float = 0.0
    filled_qty: float = 0.0
    executed_qty: float | None = None  # alias usado en algunos módulos
    avg_price: float | None = None
    status: OrderStatus = OrderStatus.NEW
    tif: TimeInForce | None = None
    time_in_force: TimeInForce | None = None  # alias
    submitted_ts: float = 0.0
    updated_ts: float = 0.0
    fills: list[Fill] = field(default_factory=list)
    reason: str = ""
    client_order_id: str | None = None


class OrderRejected(Exception):
    """Error para rechazos por validación/local."""

    pass


class BrokerError(Exception):
    """Error general de broker/adaptor."""

    pass


class SymbolFilters(TypedDict, total=False):
    """
    Filtros normalizados del símbolo (se incluyen keys alternativas para compatibilidad).
    - price_tick/tick_size: float (múltiplo de precios)
    - qty_step/step_size: float (múltiplo de cantidad)
    - min_qty: float (cantidad mínima)
    - min_notional: float (importe mínimo)
    - price_min / price_max: límites de precio (si aplica)
    """

    price_tick: float
    tick_size: float
    qty_step: float
    step_size: float
    min_qty: float
    min_notional: float
    price_min: float
    price_max: float


@dataclass
class AccountInfo:
    """Información básica de cuenta."""

    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0
    balances: dict[str, float] = field(default_factory=dict)


@dataclass
class OrderRequest:
    """
    Petición de orden normalizada. Incluye aliases compatibles con código existente:
      - 'type'  -> order_type
      - 'quantity' -> qty
      - 'time_in_force' -> tif
    """

    symbol: str
    side: OrderSide | str
    qty: float = 0.0
    price: float | None = None
    order_type: OrderType | str = OrderType.MARKET
    tif: TimeInForce | str | None = None
    reason: str = ""
    client_order_id: str | None = None
    ts: float = 0.0

    # Aliases (para compatibilidad con código antiguo)
    type: OrderType | str | None = None
    quantity: float | None = None
    time_in_force: TimeInForce | str | None = None

    def __post_init__(self) -> None:
        # Sincronizar aliases a campos canónicos
        if self.type is not None:
            self.order_type = (
                OrderType(self.type) if not isinstance(self.type, OrderType) else self.type
            )
        if self.quantity is not None:
            self.qty = float(self.quantity)
        if self.time_in_force is not None:
            self.tif = (
                TimeInForce(self.time_in_force)
                if not isinstance(self.time_in_force, TimeInForce)
                else self.time_in_force
            )


# =========================
# Protocolo mínimo de broker
# =========================


@runtime_checkable
class BaseBroker(Protocol):
    """
    Contrato mínimo para adapters de broker (paper/live).
    Se exponen nombres compatibles con el código del repo: place_order, get_open_orders, etc.
    """

    # --- Identidad y reloj ---
    def name(self) -> str: ...

    def server_time(self) -> float: ...

    # --- Información de cuenta y mercado ---
    def get_account(self) -> dict[str, Any]: ...

    def get_symbol_filters(self, symbol: str) -> dict[str, Any]: ...

    # --- Posición y órdenes ---
    def get_position(self, symbol: str) -> float: ...

    def open_orders(self, symbol: str | None = None) -> list[Order]: ...

    def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...

    def submit_order(self, req: OrderRequest) -> Order: ...

    def place_order(self, req: OrderRequest) -> Order: ...

    def fetch_order(self, symbol: str, order_id: str | int) -> Order: ...

    def cancel_order(self, symbol: str, order_id: str | int) -> Order: ...

    # --- Sincronización ligera ---
    def refresh(self) -> None: ...


# =========================
# Utilidades varias
# =========================


def clamp(x: float, lo: float, hi: float) -> float:
    """Limita x al rango [lo, hi]."""
    return max(lo, min(hi, x))


def to_notional(price: float, qty: float) -> float:
    """Convierte precio y cantidad a nocional (USDT)."""
    return float(price) * float(qty)


def from_notional(price: float, notional: float) -> float:
    """Convierte nocional (USDT) a cantidad (base)."""
    if price == 0:
        return 0.0
    return float(notional) / float(price)


def near(a: float, b: float, eps: float = 1e-9) -> bool:
    """Comparación flotante segura."""
    return abs(float(a) - float(b)) <= float(eps)


def is_multiple(x: float, step: float, eps: float = 1e-9) -> bool:
    """Comprueba si x es múltiplo de step con tolerancia."""
    if step == 0:
        return True
    k = round(x / step)
    return near(x, k * step, eps)


def price_bounds_ok(price: float, filters: SymbolFilters) -> bool:
    """Comprueba límites de precio si existen."""
    pmin = filters.get("price_min")
    if pmin is not None and price < float(pmin):
        return False
    pmax = filters.get("price_max")
    if pmax is not None and price > float(pmax):
        return False
    return True

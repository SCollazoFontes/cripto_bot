# ============================================================
# src/core/broker.py — Interfaces de Broker y tipos de órdenes
# ------------------------------------------------------------
# Propósito:
#   - Definir un CONTRATO claro para cómo el motor (Engine) se
#     comunica con un "broker" (simulado o real).
#   - Evitar que Engine dependa de detalles de Portfolio.
#   - Unificar tipos (OrderRequest, ExecutionReport, PositionView).
#
# Próximo paso:
#   - Implementar un broker simulado en `broker_sim.py` que cumpla
#     este contrato usando `Portfolio` por debajo.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, Optional, Protocol, runtime_checkable


# -----------------------------
# Tipos básicos del broker
# -----------------------------
class Side(Enum):
    BUY = auto()
    SELL = auto()


@dataclass(frozen=True)
class OrderRequest:
    """
    Orden MARKET simplificada: ejecuta qty contra un price_ref (mid/last)
    al que se aplicará slippage en el broker simulado.
    """

    symbol: str
    side: Side
    qty: float
    price_ref: float
    ts_ms: Optional[int] = None


@dataclass(frozen=True)
class ExecutionReport:
    """
    Resultado de una ejecución MARKET.
    - notional incluye qty * exec_price (sin restar fees).
    - fee se descuenta del cash en la divisa de liquidación (p.ej., USDT).
    """

    order_id: int
    symbol: str
    side: Side
    qty: float
    exec_price: float
    notional: float
    fee: float
    ts_ms: Optional[int] = None


@dataclass(frozen=True)
class PositionView:
    """
    Vista de posición neutral al broker (no expone internals del Portfolio).
    """

    symbol: str
    qty: float
    avg_price: float
    realized_pnl: float
    fees_paid: float


# -----------------------------
# Interfaz del Broker
# -----------------------------
@runtime_checkable
class Broker(Protocol):
    """
    Contrato mínimo que debe cumplir cualquier broker (sim/live):
      - Ejecutar órdenes MARKET.
      - Exponer estado: cash, posiciones y equity (con marks opcionales).
      - Ciclo de vida: flush/shutdown opcional para recursos.
    """

    def submit_market(self, order: OrderRequest) -> ExecutionReport:
        """
        Ejecuta una orden MARKET contra el origen de liquidez del broker
        (simulado o real). Debe aplicar su propia lógica de slippage/fees.
        """
        ...

    def cash(self) -> float:
        """Efectivo disponible (divisa de liquidación, p.ej., USDT)."""
        ...

    def positions(self) -> Dict[str, PositionView]:
        """
        Posiciones abiertas por símbolo (qty >= 0 si el broker no permite cortos).
        """
        ...

    def equity(self, marks: Optional[Dict[str, float]] = None) -> float:
        """
        Equity total: cash + MTM de posiciones usando `marks` si se aportan.
        """
        ...

    def flush(self) -> None:
        """
        Punto de sincronización/volcado (p.ej., escribir trades en disco).
        Opcional: puede ser no-op.
        """
        ...

    def shutdown(self) -> None:
        """
        Cierre ordenado del broker (liberar recursos, cerrar archivos/sockets).
        Opcional: puede ser no-op.
        """
        ...

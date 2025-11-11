# src/core/portfolio.py
"""
Portfolio: módulo que lleva el control del valor de la cartera
durante simulaciones o ejecución en vivo.

Gestiona:
- Efectivo disponible y posición (qty, precio medio, valor).
- Actualización tras cada trade ejecutado.
- Registro de equity y métricas básicas de riesgo.
- Compatibilidad con SimBroker y estrategias discrecionales.

Diseñado para funcionar dentro de src/core/engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
@dataclass
class PortfolioConfig:
    """Configuración inicial del portafolio."""

    cash: float = 10_000.0
    allow_short: bool = False
    fees_bps: float = 2.5
    slip_bps: float = 1.0


# --------------------------------------------------------------------------- #
# Portafolio simulado
# --------------------------------------------------------------------------- #
class Portfolio:
    """
    Controla el estado de la cartera: efectivo, posición, y equity.
    """

    def __init__(self, cfg: PortfolioConfig) -> None:
        self.cfg = cfg
        self.cash: float = cfg.cash
        self.position_qty: float = 0.0
        self.position_price: float = 0.0
        self.realized_pnl: float = 0.0
        self.fees_paid: float = 0.0
        self.last_price: float | None = None
        logger.debug("Portfolio iniciado con cash=%.2f", cfg.cash)

    # ------------------------------------------------------------------ #
    def update_from_trade(
        self,
        side: str,
        qty: float,
        price: float,
        fee: float,
    ) -> None:
        """
        Actualiza posición y cash tras una operación ejecutada.

        Args:
            side: BUY o SELL.
            qty: cantidad ejecutada (>0).
            price: precio de ejecución.
            fee: comisión aplicada.
        """
        if qty <= 0.0:
            return

        delta = qty if side.upper() == "BUY" else -qty

        prev_qty = self.position_qty
        prev_price = self.position_price
        new_qty = prev_qty + delta

        if prev_qty != 0 and (prev_qty > 0 > new_qty or prev_qty < 0 < new_qty):
            # Cruza el cero → cierre parcial o total
            closed_qty = -prev_qty
            pnl = closed_qty * (price - prev_price)
            self.realized_pnl += pnl
            self.position_qty = new_qty
            self.position_price = price
        elif new_qty == 0:
            pnl = delta * (price - prev_price)
            self.realized_pnl += pnl
            self.position_qty = 0.0
            self.position_price = 0.0
        else:
            # Promedio ponderado simple
            if prev_qty == 0:
                self.position_price = price
            else:
                self.position_price = (prev_qty * prev_price + delta * price) / new_qty
            self.position_qty = new_qty

        # Cash y fees
        self.cash -= price * delta + fee
        self.fees_paid += fee
        self.last_price = price

        logger.debug(
            "Trade %s qty=%.6f @ %.2f | cash=%.2f pos=%.6f avg=%.2f pnl=%.2f",
            side,
            qty,
            price,
            self.cash,
            self.position_qty,
            self.position_price,
            self.realized_pnl,
        )

    # ------------------------------------------------------------------ #
    def equity(self, mark_price: float | None = None) -> float:
        """Calcula equity actual (cash + mark-to-market)."""
        price = mark_price or self.last_price or self.position_price
        mtm = self.position_qty * price if price is not None else 0.0
        return float(self.cash + mtm)

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, float]:
        """Devuelve snapshot numérico completo de la cartera."""
        eq = self.equity()
        return {
            "cash": float(self.cash),
            "position_qty": float(self.position_qty),
            "position_price": float(self.position_price),
            "realized_pnl": float(self.realized_pnl),
            "fees_paid": float(self.fees_paid),
            "equity": float(eq),
        }

    # ------------------------------------------------------------------ #
    def summary(self) -> dict[str, float]:
        """Resumen abreviado para logs o reportes."""
        snap = self.snapshot()
        summary: dict[str, float] = {
            "cash": snap["cash"],
            "equity": snap["equity"],
        }
        return summary

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        """Reinicia todos los valores."""
        self.cash = self.cfg.cash
        self.position_qty = 0.0
        self.position_price = 0.0
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.last_price = None
        logger.debug("Portfolio reseteado a estado inicial.")

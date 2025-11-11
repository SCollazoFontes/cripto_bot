"""
SimBroker: Broker simulado para backtests o paper trading.

Ejecuta órdenes de mercado con precio instantáneo, aplicando slippage y comisiones.
Usa un Portfolio interno para seguimiento de posición y equity.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from core.broker import Broker
from core.portfolio import Portfolio, PortfolioConfig

logger = logging.getLogger(__name__)


@dataclass
class SimBrokerConfig:
    """Parámetros de configuración para el broker simulado."""

    starting_cash: float = 10_000.0
    fees_bps: float = 2.5
    slip_bps: float = 1.0
    allow_short: bool = False


class SimBroker(Broker):
    """
    Broker simulado. Ejecuta órdenes instantáneamente con slippage y fees.
    Internamente mantiene un Portfolio para calcular posición, equity y PnL.
    """

    def __init__(self, cfg: SimBrokerConfig) -> None:
        self.cfg = cfg
        self._portfolio = Portfolio(
            PortfolioConfig(
                cash=cfg.starting_cash,
                fees_bps=cfg.fees_bps,
                slip_bps=cfg.slip_bps,
                allow_short=cfg.allow_short,
            )
        )
        self._last_price: float | None = None

    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Ejecuta una orden de mercado simulada. Aplica slippage y comisiones.
        Retorna un dict con detalles del trade.
        """
        side = side.upper()
        if qty <= 0.0:
            return None

        if side == "SELL" and self._portfolio.position_qty < qty and not self.allow_short:
            logger.warning(
                "Short no permitido: venta %.4f con posición %.4f",
                qty,
                self._portfolio.position_qty,
            )
            return None

        slip = self.cfg.slip_bps / 10_000
        exec_price = price * (1 + slip) if side == "BUY" else price * (1 - slip)
        exec_price = float(exec_price)

        fee = self.cfg.fees_bps / 10_000 * exec_price * qty

        self._portfolio.update_from_trade(side=side, qty=qty, price=exec_price, fee=fee)
        self._last_price = exec_price

        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "exec_price": exec_price,
            "fee": fee,
            "reason": reason,
        }

    def equity(self, mark_price: float | None = None) -> float:
        """
        Equity = cash + posición marcada al precio actual.
        Si no se da precio, usa el último conocido.
        """
        price = mark_price or self._last_price or 0.0
        return self._portfolio.equity(mark_price=price)

    @property
    def cash(self) -> float:
        """Efectivo disponible."""
        return self._portfolio.cash

    @property
    def position_qty(self) -> float:
        """Posición actual (positiva = long, negativa = short)."""
        return self._portfolio.position_qty

    @property
    def allow_short(self) -> bool:
        """Indica si se permite abrir posiciones cortas."""
        return self.cfg.allow_short

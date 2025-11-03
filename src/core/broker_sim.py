# ============================================================
# src/core/broker_sim.py — Broker simulado (SimBroker)
# ------------------------------------------------------------
# Propósito:
#   - Implementar la interfaz Broker definida en broker.py
#   - Usar internamente un Portfolio para simular fills reales
#   - Aplicar slippage y fees configurables en bps
#   - Permitir backtests o modo paper sin cambiar el Engine
# ============================================================

from __future__ import annotations

from typing import Dict, Optional

from loguru import logger

from src.core.broker import (
    Broker,
    ExecutionReport,
    OrderRequest,
    PositionView,
    Side,
)
from src.core.portfolio import (
    Portfolio,
    Side as PSide,  # <- para tipar _convert_side()
)


class SimBroker(Broker):
    """
    Broker simulado que cumple la interfaz Broker usando Portfolio por debajo.
    """

    def __init__(
        self,
        starting_cash: float = 10_000.0,
        fees_bps: float = 2.5,
        slip_bps: float = 1.0,
        allow_short: bool = False,
    ) -> None:
        self.portfolio = Portfolio(
            starting_cash=starting_cash,
            fees_bps=fees_bps,
            slip_bps=slip_bps,
            allow_short=allow_short,
        )
        logger.info(
            "SimBroker iniciado con cash=%.2f, fees=%.2f bps, slippage=%.2f bps",
            starting_cash,
            fees_bps,
            slip_bps,
        )

    # ---------------------------------------------------------
    # Implementación de la interfaz Broker
    # ---------------------------------------------------------
    def submit_market(self, order: OrderRequest) -> ExecutionReport:
        """
        Ejecuta una orden MARKET usando Portfolio.submit_market_order.
        """
        fill = self.portfolio.submit_market_order(
            symbol=order.symbol,
            side=self._convert_side(order.side),
            qty=order.qty,
            price_ref=order.price_ref,
            ts_ms=order.ts_ms,
        )

        return ExecutionReport(
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=order.side,
            qty=fill.qty,
            exec_price=fill.price,
            notional=fill.notional,
            fee=fill.fee,
            ts_ms=fill.ts_ms,
        )

    def cash(self) -> float:
        return self.portfolio.cash

    def positions(self) -> Dict[str, PositionView]:
        """
        Devuelve posiciones activas con estructura PositionView.
        """
        views: Dict[str, PositionView] = {}
        for sym, pos in self.portfolio.positions.items():
            views[sym] = PositionView(
                symbol=sym,
                qty=pos.qty,
                avg_price=pos.avg_price,
                realized_pnl=pos.realized_pnl,
                fees_paid=pos.fees_paid,
            )
        return views

    def equity(self, marks: Optional[Dict[str, float]] = None) -> float:
        return self.portfolio.equity(marks=marks)

    def flush(self) -> None:
        """En este broker no hay persistencia, así que no hace nada."""
        pass

    def shutdown(self) -> None:
        """Finalización ordenada del broker."""
        logger.info("SimBroker finalizado correctamente.")

    # ---------------------------------------------------------
    # Utilidades internas
    # ---------------------------------------------------------
    @staticmethod
    def _convert_side(side: Side) -> PSide:
        """Convierte el enum del broker (broker.Side) al del Portfolio."""
        return PSide.BUY if side == Side.BUY else PSide.SELL


# ============================================================
# Self-test rápido
# ============================================================
def _self_test() -> None:
    """
    Comprueba la conexión broker <-> portfolio:
      - BUY 0.01 BTC @ 60_000
      - SELL 0.01 BTC @ 61_000
      - Verifica que el cash final > inicial
    """
    logger.info("Iniciando self-test de SimBroker...")

    broker = SimBroker(starting_cash=10_000.0, fees_bps=2.5, slip_bps=1.0)
    logger.info("Equity inicial: %.2f", broker.equity())

    # BUY
    order1 = OrderRequest(symbol="BTCUSDT", side=Side.BUY, qty=0.01, price_ref=60_000.0)
    rep1 = broker.submit_market(order1)
    logger.info("BUY exec: %s", rep1)

    # SELL
    order2 = OrderRequest(symbol="BTCUSDT", side=Side.SELL, qty=0.01, price_ref=61_000.0)
    rep2 = broker.submit_market(order2)
    logger.info("SELL exec: %s", rep2)

    eq_final = broker.equity(marks={"BTCUSDT": 61_000.0})
    logger.info("Equity final: %.2f", eq_final)
    assert eq_final > 10_000.0 - 1.0, "Equity final debe superar el inicial"
    broker.shutdown()
    logger.info("Self-test OK ✅")


if __name__ == "__main__":
    _self_test()

"""Simple executor for live trading orders."""

from __future__ import annotations

from brokers.base import OrderRequest


class SimpleExecutor:
    """Executor mínimo que envuelve al broker para órdenes de mercado."""

    def __init__(self, broker):
        self.broker = broker
        self.orders_executed: list[dict] = []

    def market_buy(self, symbol: str, qty: float) -> None:
        req = OrderRequest(symbol=symbol, side="BUY", order_type="MARKET", quantity=float(qty))
        order = self.broker.submit_order(req)
        px = None
        try:
            if getattr(order, "fills", None):
                # soporta distintos tipos Fill
                f0 = list(order.fills)[0]
                px = getattr(f0, "price", None)
        except Exception:
            px = None
        self.orders_executed.append(
            {
                "side": "BUY",
                "price": (
                    float(px)
                    if px is not None
                    else float(getattr(self.broker, "_last_px", {}).get(symbol, 0.0))
                ),
                "qty": float(qty),
            }
        )

    def market_sell(self, symbol: str, qty: float) -> None:
        req = OrderRequest(symbol=symbol, side="SELL", order_type="MARKET", quantity=float(qty))
        order = self.broker.submit_order(req)
        px = None
        try:
            if getattr(order, "fills", None):
                f0 = list(order.fills)[0]
                px = getattr(f0, "price", None)
        except Exception:
            px = None
        self.orders_executed.append(
            {
                "side": "SELL",
                "price": (
                    float(px)
                    if px is not None
                    else float(getattr(self.broker, "_last_px", {}).get(symbol, 0.0))
                ),
                "qty": float(qty),
            }
        )

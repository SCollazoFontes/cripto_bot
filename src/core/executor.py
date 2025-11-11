"""
Ejecución de órdenes de mercado sobre una cuenta mínima.
"""

from __future__ import annotations

from core.costs import _apply_fees, _apply_slippage
from core.types import Account, TradeRow


def exec_market(
    account: Account, *, side: str, price: float, qty: float, t: int, reason: str
) -> TradeRow:
    """
    Ejecuta una orden de mercado (BUY/SELL) aplicando slippage/fees
    sobre una cuenta mínima (cash, qty, avg_price) y devuelve TradeRow.

    - side: "BUY" o "SELL" (case-insensitive)
    - price: precio de referencia
    - qty: cantidad base a ejecutar (> 0)
    - t: timestamp/int del evento
    - reason: texto corto con el motivo
    """
    side_u = side.upper()
    assert side_u in ("BUY", "SELL"), f"side inválido: {side}"

    px = _apply_slippage(price, side_u)
    fee = _apply_fees(px * qty)

    if side_u == "BUY":
        cost = px * qty + fee
        if account.cash < cost:
            raise ValueError("cash insuficiente para BUY")
        # update cash & position
        account.cash -= cost
        # nuevo avg_price ponderado
        if account.qty + qty > 0:
            account.avg_price = (account.avg_price * account.qty + px * qty) / (account.qty + qty)
        account.qty += qty
    else:  # SELL
        if account.qty < qty:
            raise ValueError("qty insuficiente para SELL")
        proceeds = px * qty - fee
        account.cash += proceeds
        account.qty -= qty
        # si cerramos completamente, avg_price a 0
        if account.qty == 0:
            account.avg_price = 0.0

    equity = account.equity(px)
    return TradeRow(
        t=t, side=side_u, price=px, qty=qty, cash=account.cash, equity=equity, reason=reason
    )


# Wrapper retro-compatibilidad (si el runner lo usa)
def _exec_market(
    account: Account, *, side: str, price: float, qty: float, t: int, reason: str
) -> TradeRow:
    return exec_market(account, side=side, price=price, qty=qty, t=t, reason=reason)

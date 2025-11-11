# src/core/costs.py
from __future__ import annotations

from typing import Literal

Side = Literal["buy", "sell"]

"""
Cálculo de costes de ejecución.

Responsabilidad
---------------
- Cálculo de comisiones (fees) y slippage (en bps o absolutos).
- Helpers puros para aplicar fees/slippage a precios y estimar costes totales.

API pública
-----------
- apply_fees / apply_slippage : funciones de alto nivel (abs/bps, buy/sell).
- _apply_fees / _apply_slippage / _est_costs : wrappers retro-compatibles
  usados por el runner hasta completar la migración.

Notas
-----
- Mantener funciones puras y deterministas (sin efectos secundarios).
- No formatear logs ni tocar disco.
"""


# ==============================
# Helpers
# ==============================
def _rate_from_bps_or_rate(*, bps: float | None, rate: float | None) -> float:
    """Devuelve un ratio (0.0–1.0) a partir de 'bps' o 'rate'. 'rate' tiene prioridad."""
    if rate is not None:
        if rate < 0:
            raise ValueError("rate no puede ser negativo.")
        return rate
    if bps is not None:
        if bps < 0:
            raise ValueError("bps no puede ser negativo.")
        return bps / 10_000.0
    return 0.0


def _ensure_non_negative(value: float, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} no puede ser negativo.")


def _norm_side(side: str | Side) -> Side:
    """Normaliza 'BUY'/'SELL' a 'buy'/'sell'."""
    s = str(side).lower()
    if s not in ("buy", "sell"):
        raise ValueError("side debe ser 'buy' o 'sell'.")
    return s  # type: ignore[return-value]


# ==============================
# API pública (preferible en nuevo código)
# ==============================
def apply_fees(
    notional: float,
    *,
    fee_bps: float | None = None,
    fee_rate: float | None = None,
    min_fee: float = 0.0,
) -> tuple[float, float]:
    """
    Aplica comisiones a un 'notional' y devuelve (neto, fee_pagada).

    Nota: en nuevo código usa esta firma. Los wrappers legacy abajo
    devuelven SOLO la fee, para respetar el comportamiento antiguo.
    """
    _ensure_non_negative(notional, "notional")
    _ensure_non_negative(min_fee, "min_fee")
    rate = _rate_from_bps_or_rate(bps=fee_bps, rate=fee_rate)
    fee = max(notional * rate, min_fee) if rate > 0 or min_fee > 0 else 0.0
    neto = notional - fee
    return neto, fee


def apply_slippage(
    price: float,
    side: Side | str,
    *,
    slippage_bps: float | None = None,
    slippage_rate: float | None = None,
) -> float:
    """Aplica slippage a un precio y devuelve el precio efectivo (acepta BUY/SELL)."""
    _ensure_non_negative(price, "price")
    rate = _rate_from_bps_or_rate(bps=slippage_bps, rate=slippage_rate)
    s = _norm_side(side)
    if s == "buy":
        return price * (1.0 + rate)
    else:  # "sell"
        return price * (1.0 - rate)


def estimate_costs(
    *,
    notional: float,
    side: Side | str,
    fee_bps: float | None = None,
    fee_rate: float | None = None,
    slippage_bps: float | None = None,
    slippage_rate: float | None = None,
) -> dict:
    """Devuelve desglose de costes absolutos y en bps."""
    _ensure_non_negative(notional, "notional")
    _ = _norm_side(side)  # reservado para futuros usos

    fee_r = _rate_from_bps_or_rate(bps=fee_bps, rate=fee_rate)
    slip_r = _rate_from_bps_or_rate(bps=slippage_bps, rate=slippage_rate)
    fee_amount = notional * fee_r
    slippage_amount = notional * slip_r
    total_cost_abs = fee_amount + slippage_amount
    total_cost_bps = (total_cost_abs / notional) * 10_000 if notional else 0.0
    return {
        "fee_amount": fee_amount,
        "slippage_amount": slippage_amount,
        "total_cost_abs": total_cost_abs,
        "total_cost_bps": total_cost_bps,
    }


# ==========================================
# Wrappers retro-compatibles (runner/tools actuales)
# ==========================================
def _apply_fees(
    notional: float,
    *,
    fee_bps: float | None = None,
    fee_rate: float | None = None,
    min_fee: float = 0.0,
    # ---- alias legacy ----
    bps: float | None = None,
    rate: float | None = None,
) -> float:
    """
    Legacy: devuelve SOLO la comisión (float).

    Compatibilidad:
      - acepta 'bps'/'rate' (legacy) o 'fee_bps'/'fee_rate' (nuevo).
      - 'fee_rate' tiene prioridad sobre 'rate'; 'fee_bps' sobre 'bps'.
    """
    eff_fee_rate = fee_rate if fee_rate is not None else rate
    eff_fee_bps = fee_bps if fee_bps is not None else bps
    _neto, fee = apply_fees(notional, fee_bps=eff_fee_bps, fee_rate=eff_fee_rate, min_fee=min_fee)
    return fee


def _apply_slippage(
    price: float,
    side: Side | str,
    *,
    slippage_bps: float | None = None,
    slippage_rate: float | None = None,
    # ---- alias legacy ----
    bps: float | None = None,
    rate: float | None = None,
) -> float:
    """
    Legacy: firma compatible y tolera side en mayúsculas.
    """
    eff_slip_rate = slippage_rate if slippage_rate is not None else rate
    eff_slip_bps = slippage_bps if slippage_bps is not None else bps
    return apply_slippage(
        price, _norm_side(side), slippage_bps=eff_slip_bps, slippage_rate=eff_slip_rate
    )


def _est_costs(
    notional: float,
    side: Side | str = "buy",  # opcional por compatibilidad
    *,
    # preferidos
    fee_bps: float | None = None,
    fee_rate: float | None = None,
    slippage_bps: float | None = None,
    slippage_rate: float | None = None,
    # alias legacy
    fees_bps: float | None = None,  # alias de fee_bps
    slip_bps: float | None = None,  # alias de slippage_bps
) -> tuple[float, float, float]:
    """
    Legacy: devuelve una tupla (fee_amount, slippage_amount, total_cost_abs).

    Compatibilidad:
      - acepta 'fees_bps' (alias de 'fee_bps') y 'slip_bps' (alias de 'slippage_bps').
      - 'side' es opcional; por defecto 'buy'.
    """
    eff_fee_bps = fee_bps if fee_bps is not None else fees_bps
    eff_slip_bps = slippage_bps if slippage_bps is not None else slip_bps
    d = estimate_costs(
        notional=notional,
        side=_norm_side(side),
        fee_bps=eff_fee_bps,
        fee_rate=fee_rate,
        slippage_bps=eff_slip_bps,
        slippage_rate=slippage_rate,
    )
    return d["fee_amount"], d["slippage_amount"], d["total_cost_abs"]

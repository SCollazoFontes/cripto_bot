# src/core/costs.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

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
# Modelos configurables (realistas)
# ==============================


class SpreadProvider(Protocol):
    def __call__(self, symbol: str | None = None) -> float | None:
        """Devuelve spread (ask - bid) actual o None si desconocido."""


@dataclass
class SlippageModel:
    """
    Modelo de slippage configurable.

    mode:
      - "fixed_bps": usa `fixed_bps` (mismo para buy/sell)
      - "spread_frac": desplaza una fracción del spread (e.g., 0.5 = mitad del spread)
      - "custom": delega en `custom_rate_fn` (devuelve rate en [0, +inf))
    """

    mode: Literal["fixed_bps", "spread_frac", "custom"] = "fixed_bps"
    fixed_bps: float = 0.0
    spread_frac: float = 0.0
    custom_rate_fn: Callable[[Side, float | None, str | None], float] | None = None

    def slippage_rate(self, side: Side, spread: float | None, symbol: str | None = None) -> float:
        if self.mode == "fixed_bps":
            return max(0.0, self.fixed_bps / 10_000.0)
        if self.mode == "spread_frac":
            if spread is None or self.spread_frac <= 0.0:
                return 0.0
            # rate relativo = (frac * spread) / mid  (aprox; el caller aplica sobre precio)
            # Simplificamos: devolvemos un rate efectivo en función del spread como proporción del precio.
            # El caller debe pasar el precio base (mid o limit), por lo que aplicar (1±rate) es válido.
            return max(0.0, self.spread_frac * (spread))  # el caller normaliza vs precio base
        if self.mode == "custom" and self.custom_rate_fn is not None:
            return max(0.0, float(self.custom_rate_fn(side, spread, symbol)))
        return 0.0


@dataclass
class CostModel:
    """
    Modelo de costes realista con maker/taker y slippage configurable.

    - maker_fee_rate/taker_fee_rate: comisiones como proporción (0.001 = 10 bps)
    - maker_slip/taker_slip: slippage model por rol
    - spread_provider: callable opcional para obtener spread (ask-bid)
    - symbol: contexto opcional (permite spreads por símbolo)
    """

    maker_fee_rate: float = 0.001  # 10 bps por defecto
    taker_fee_rate: float = 0.001  # 10 bps por defecto
    maker_slip: SlippageModel = dataclass(init=False)  # type: ignore[assignment]
    taker_slip: SlippageModel = dataclass(init=False)  # type: ignore[assignment]
    spread_provider: SpreadProvider | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        # Inicializamos defaults mutables correctamente
        if not isinstance(getattr(self, "maker_slip", None), SlippageModel):
            object.__setattr__(self, "maker_slip", SlippageModel(mode="fixed_bps", fixed_bps=0.0))
        if not isinstance(getattr(self, "taker_slip", None), SlippageModel):
            object.__setattr__(self, "taker_slip", SlippageModel(mode="fixed_bps", fixed_bps=5.0))

    def fee_amount(self, *, notional: float, role: Literal["maker", "taker"]) -> float:
        _ensure_non_negative(notional, "notional")
        rate = self.maker_fee_rate if role == "maker" else self.taker_fee_rate
        return max(0.0, notional * max(0.0, rate))

    def effective_price(
        self,
        *,
        base_price: float,
        side: Side | str,
        role: Literal["maker", "taker"],
    ) -> float:
        """Aplica slippage al precio base según rol y lado."""
        _ensure_non_negative(base_price, "base_price")
        s = _norm_side(side)
        spread = self.spread_provider(self.symbol) if self.spread_provider else None
        slip_model = self.maker_slip if role == "maker" else self.taker_slip
        rate = slip_model.slippage_rate(s, spread, self.symbol)
        # Si mode=spread_frac y devolvimos un valor absoluto, conviértelo a rate sobre el precio
        if slip_model.mode == "spread_frac" and spread is not None:
            rate = rate / max(base_price, 1e-12)
        if s == "buy":
            return base_price * (1.0 + rate)
        else:
            return base_price * (1.0 - rate)


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

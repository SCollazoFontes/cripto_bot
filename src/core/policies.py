# src/core/policies.py
from __future__ import annotations

from typing import Literal

Side = Literal["buy", "sell"]

"""
Políticas de ejecución (guards).

Responsabilidad
---------------
- Decidir si una orden *debería* ejecutarse en función de recursos
  (cash/posición), mínimos operativos y relación edge vs costes.

API pública
-----------
- guard_allows_exec(...) -> (ok, reason, extras)
- _guard_allows_exec(...) -> (ok, reason)  # wrapper legacy

Notas
-----
- 'reason' es un tag corto y estable, apto para logs/analítica.
- edge/costes en bps o absolutos (conversión interna).
"""


# ============================== Helpers =====================================
def _norm_side(side: str | Side) -> Side:
    """Normaliza 'BUY'/'SELL' a 'buy'/'sell' con validación."""
    s = str(side).lower()
    if s not in ("buy", "sell"):
        raise ValueError("side debe ser 'buy' o 'sell'.")
    return s  # type: ignore[return-value]


def _bps_from_abs(amount_abs: float, notional: float) -> float:
    if notional <= 0:
        return 0.0
    return (float(amount_abs) / float(notional)) * 10_000.0


def _safe_ge(a: float, b: float, eps: float = 1e-12) -> bool:
    """Compara a >= b con tolerancia numérica."""
    return (a - b) >= -eps


# =============================== API pública =================================
def guard_allows_exec(
    *,
    side: Side | str,
    notional: float,
    price: float,
    cash: float,
    position_qty: float,
    # Señal / ventaja esperada:
    edge_bps: float | None = None,
    edge_abs: float | None = None,
    # Costes esperados:
    total_costs_bps: float | None = None,
    total_costs_abs: float | None = None,
    # Política:
    min_edge_multiplier: float = 1.0,
    min_qty: float = 0.0,
) -> tuple[bool, str, dict[str, float]]:
    """
    Decide si se puede ejecutar una orden y por qué.

    Parámetros
    ----------
    side : {"buy", "sell"} o "BUY"/"SELL"
    notional : float
        Importe bruto (price * qty_intended).
    price : float
        Precio efectivo esperado de la orden.
    cash : float
        Efectivo disponible.
    position_qty : float
        Cantidad disponible para vender.
    edge_bps / edge_abs : float | None
        Ventaja esperada en bps (preferido) o en absoluto (se convierte a bps).
    total_costs_bps / total_costs_abs : float | None
        Costes totales esperados (fees+slippage) en bps o absolutos.
    min_edge_multiplier : float
        Múltiplo de costes exigido al edge (p.ej. 1.5 -> edge >= 1.5 * costes).
    min_qty : float
        Cantidad mínima operable (en unidades del activo).

    Retorna
    -------
    (ok, reason, extras)
      ok : bool
      reason : str   # tag estable para logs ("ok", "cash_insuficiente", ...)
      extras : dict  # valores útiles para debug/logs: qty, edge_bps_eff, costs_bps_eff
    """
    s = _norm_side(side)

    # Validaciones básicas
    if price <= 0:
        return False, "precio_no_valido", {}
    if notional <= 0:
        return False, "notional_no_valido", {}

    qty = float(notional) / float(price)
    if qty < float(min_qty):
        return False, "qty_inferior_minima", {"qty": qty}

    # Recursos (cash/posición)
    if s == "buy":
        # si se da total_costs_abs, se exige cash para cubrirlos también
        need_cash = notional + (total_costs_abs or 0.0)
        if not _safe_ge(cash, need_cash):
            return False, "cash_insuficiente", {"need_cash": need_cash, "cash": cash}
    else:  # "sell"
        if not _safe_ge(position_qty, qty):
            return False, "posicion_insuficiente", {"need_qty": qty, "pos_qty": position_qty}

    # Edge/costes en bps
    edge_bps_eff = edge_bps
    if edge_bps_eff is None and edge_abs is not None:
        edge_bps_eff = _bps_from_abs(edge_abs, notional)

    costs_bps_eff = total_costs_bps
    if costs_bps_eff is None and total_costs_abs is not None:
        costs_bps_eff = _bps_from_abs(total_costs_abs, notional)

    # Si no hay info de edge o costes, no bloqueamos por este criterio
    if edge_bps_eff is not None and costs_bps_eff is not None:
        threshold = float(min_edge_multiplier) * float(costs_bps_eff)
        if edge_bps_eff < threshold:
            return (
                False,
                "edge_insuficiente_vs_costes",
                {
                    "edge_bps_eff": float(edge_bps_eff),
                    "costs_bps_eff": float(costs_bps_eff),
                    "threshold_bps": float(threshold),
                    "qty": qty,
                },
            )

    return (
        True,
        "ok",
        {"qty": qty, "edge_bps_eff": edge_bps_eff or 0.0, "costs_bps_eff": costs_bps_eff or 0.0},
    )


# =========================== Wrapper retro-compatible ========================
def _guard_allows_exec(
    *,
    side: Side | str,
    price: float,
    notional: float,
    cash: float,
    position_qty: float,
    # alias/nombres legacy
    min_edge_multiplier: float = 1.0,
    edge_bps: float | None = None,
    edge: float | None = None,  # alias de edge_bps
    total_costs_bps: float | None = None,
    total_costs: float | None = None,  # alias de total_costs_abs
    min_qty: float = 0.0,
) -> tuple[bool, str]:
    """
    Firma legacy: devuelve (ok, reason). Mantiene nombres usados por runners
    anteriores para permitir una migración incremental.

    - Acepta 'edge' (alias de edge_bps) y 'total_costs' (alias de total_costs_abs).
    - Tolera side en mayúsculas.
    """
    eff_edge_bps = edge_bps if edge_bps is not None else edge
    ok, reason, _extras = guard_allows_exec(
        side=_norm_side(side),
        notional=notional,
        price=price,
        cash=cash,
        position_qty=position_qty,
        edge_bps=eff_edge_bps,
        edge_abs=None,
        total_costs_bps=total_costs_bps,
        total_costs_abs=total_costs,
        min_edge_multiplier=min_edge_multiplier,
        min_qty=min_qty,
    )
    return ok, reason

"""
APIs unificadas para constructores de micro-velas (BarBuilder).

- Mantiene las implementaciones específicas (tick_count, volume_qty, dollar, imbalance)
- Ofrece una fábrica de alto nivel con una firma única: make(rule, limit, mode=None)

Ejemplos
--------
	from bars import make

	b1 = make("tick", limit=100)                  # TickCountBarBuilder(tick_limit=100)
	b2 = make("volume", limit=5.0)                # VolumeQtyBarBuilder(qty_limit=5.0)
	b3 = make("dollar", limit=1_000.0)            # DollarBarBuilder(value_limit=1_000)
	b4 = make("imbalance", limit=10, mode="qty")  # ImbalanceBarBuilder(imbal_limit=10)

Nota: También puedes seguir usando bars.registry.create(...) directamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from . import registry
from .base import BarBuilder

__all__ = [
    "make",
    "BuilderConfig",
    "available_builders",
    "registry",
]


def _norm_rule(rule: str) -> str:
    k = rule.strip().lower().replace("-", "_").replace(" ", "_")
    if k in {"tick", "ticks", "tick_count"}:
        return "tick_count"
    if k in {"volume", "volume_qty"}:
        return "volume_qty"
    if k in {"dollar", "value", "dollar_value"}:
        return "dollar"
    if k.startswith("imbalance"):
        return "imbalance"
    return k


@dataclass
class BuilderConfig:
    rule: Literal["tick_count", "volume_qty", "dollar", "imbalance", "tick", "volume", "value"]
    limit: float | int
    mode: Literal["qty", "tick"] | None = None  # solo para imbalance
    extra: dict[str, Any] | None = None  # extensiones futuras


def make(rule: str, *, limit: float | int, mode: str | None = None, **kwargs: Any) -> BarBuilder:
    """
    Fabrica un BarBuilder con una sola firma unificada.

    Params
    ------
    rule : str
            "tick"/"tick_count", "volume"/"volume_qty", "dollar"/"value", "imbalance".
    limit : float | int
            Umbral para cierre de barra: nº de trades, qty, valor o desequilibrio.
    mode : str | None
            Solo para imbalance: "qty" (por defecto) o "tick".
    kwargs : dict
            Parámetros adicionales específicos del builder (si aplican).
    """
    r = _norm_rule(rule)
    if r == "tick_count":
        return registry.create("tick_count", tick_limit=int(limit), **kwargs)
    if r == "volume_qty":
        return registry.create("volume_qty", qty_limit=float(limit), **kwargs)
    if r == "dollar":
        return registry.create("dollar", value_limit=float(limit), **kwargs)
    if r == "imbalance":
        m = (mode or "qty").lower()
        if m not in ("qty", "tick"):
            raise ValueError("mode para imbalance debe ser 'qty' o 'tick'")
        return registry.create("imbalance", imbal_limit=float(limit), mode=m, **kwargs)
    if r in ("composite", "multi"):
        # En composite no hay un único 'limit'; se pasan umbrales por kwargs:
        #   tick_limit, qty_limit, value_limit, imbal_limit, imbal_mode, policy
        return registry.create(r, **kwargs)
    available = ", ".join(available_builders())
    raise ValueError(f"Regla desconocida: {rule!r}. Disponibles: {available}")


def available_builders() -> list[str]:
    return registry.get_available_builders()

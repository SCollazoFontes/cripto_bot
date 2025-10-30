# bars/registry.py
"""
Fábrica y registro de constructores de micro-velas (BarBuilder).
"""

from __future__ import annotations

from typing import Any, Dict, List, Type

from .base import BarBuilder
from .dollar import DollarBarBuilder
from .imbalance import ImbalanceBarBuilder
from .tick_count import TickCountBarBuilder
from .volume_qty import VolumeQtyBarBuilder

__all__ = [
    "register_builder",
    "create_builder",
    "get_available_builders",
]

_REGISTRY: Dict[str, Type[BarBuilder]] = {}


def _normalize(name: str) -> str:
    key = name.strip().lower()
    for sep in ("-", " ", ".", "/"):
        key = key.replace(sep, "_")
    while "__" in key:
        key = key.replace("__", "_")
    return key


def register_builder(name: str, cls: Type[BarBuilder]) -> None:
    if not issubclass(cls, BarBuilder):
        raise TypeError(f"{cls!r} debe heredar de BarBuilder.")
    _REGISTRY[_normalize(name)] = cls


def create_builder(name: str, **kwargs: Any) -> BarBuilder:
    key = _normalize(name)
    cls = _REGISTRY.get(key)
    if cls is None:
        available = ", ".join(sorted(get_available_builders()))
        raise KeyError(f"Builder '{name}' no encontrado. Disponibles: {available}")
    return cls(**kwargs)


def get_available_builders() -> List[str]:
    return sorted(_REGISTRY.keys())


# Registro por defecto y aliases
register_builder("tick_count", TickCountBarBuilder)
register_builder("tick", TickCountBarBuilder)
register_builder("ticks", TickCountBarBuilder)

register_builder("volume_qty", VolumeQtyBarBuilder)
register_builder("volume", VolumeQtyBarBuilder)

register_builder("dollar", DollarBarBuilder)
register_builder("value", DollarBarBuilder)

register_builder("imbalance", ImbalanceBarBuilder)
register_builder("imbalance_qty", ImbalanceBarBuilder)
register_builder("imbalance_tick", ImbalanceBarBuilder)

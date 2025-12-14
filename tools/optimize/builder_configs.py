# tools/optimize/builder_configs.py
"""
Catálogo de configuraciones de micro-velas para experimentos.

Este módulo NO modifica el flujo actual: simplemente expone presets que el
optimizador puede usar si se le indica. El runner por defecto continúa usando
la configuración original (tick_limit=120, value_limit=50k, policy='any').
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BuilderConfig:
    name: str
    tick_limit: int | None = None
    value_limit: float | None = None
    qty_limit: float | None = None
    policy: Literal["any", "all"] = "any"

    def as_kwargs(self) -> dict[str, float | int | str]:
        kwargs: dict[str, float | int | str] = {"policy": self.policy}
        if self.tick_limit is not None:
            kwargs["tick_limit"] = self.tick_limit
        if self.value_limit is not None:
            kwargs["value_limit"] = self.value_limit
        if self.qty_limit is not None:
            kwargs["qty_limit"] = self.qty_limit
        return kwargs


DEFAULT_BUILDER = BuilderConfig(
    name="default_120ticks", tick_limit=120, value_limit=50_000.0, policy="any"
)

BUILDER_CATALOG: dict[str, BuilderConfig] = {
    DEFAULT_BUILDER.name: DEFAULT_BUILDER,
    "compact_60ticks": BuilderConfig(
        name="compact_60ticks", tick_limit=60, value_limit=25_000.0, policy="any"
    ),
    "dense_30ticks": BuilderConfig(
        name="dense_30ticks", tick_limit=30, value_limit=15_000.0, policy="all"
    ),
    "volume_variant": BuilderConfig(
        name="volume_variant", tick_limit=None, value_limit=30_000.0, qty_limit=3.0, policy="any"
    ),
    "wide_80ticks_any": BuilderConfig(
        name="wide_80ticks_any", tick_limit=80, value_limit=40_000.0, policy="any"
    ),
    "wide_80ticks_all": BuilderConfig(
        name="wide_80ticks_all", tick_limit=80, value_limit=40_000.0, policy="all"
    ),
    "qty_focus_any": BuilderConfig(
        name="qty_focus_any", tick_limit=50, value_limit=None, qty_limit=2.0, policy="any"
    ),
    "qty_focus_all": BuilderConfig(
        name="qty_focus_all", tick_limit=50, value_limit=None, qty_limit=2.0, policy="all"
    ),
    "hybrid_100ticks_any": BuilderConfig(
        name="hybrid_100ticks_any",
        tick_limit=100,
        value_limit=60_000.0,
        qty_limit=3.5,
        policy="any",
    ),
    "hybrid_100ticks_all": BuilderConfig(
        name="hybrid_100ticks_all",
        tick_limit=100,
        value_limit=60_000.0,
        qty_limit=3.5,
        policy="all",
    ),
}


def list_builders() -> list[str]:
    return list(BUILDER_CATALOG.keys())


def get_builder(name: str) -> BuilderConfig:
    if name not in BUILDER_CATALOG:
        raise KeyError(f"Builder desconocido: {name}")
    return BUILDER_CATALOG[name]

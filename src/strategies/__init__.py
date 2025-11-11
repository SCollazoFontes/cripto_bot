# src/strategies/__init__.py
"""
Registro automático de estrategias (auto-discovery).

Objetivo
--------
Asegurar que TODAS las estrategias definidas como módulos dentro de `src/strategies`
queden registradas sin necesidad de importarlas manualmente una a una.

Cómo funciona
-------------
- Importa primero cualquier import “estático” conocido (por retrocompatibilidad).
- Después hace *auto-discovery* dinámico: recorre el paquete `src.strategies`
  e importa todos los submódulos `.py` que no empiecen por '_' para que se
  ejecuten los decoradores `@register_strategy(...)`.

Notas
-----
- Si un módulo tiene errores de importación, se ignora pero se guarda la excepción
  en `__AUTOLOAD_ERRORS__` para poder diagnosticarlos desde el exterior.
"""

from __future__ import annotations

__all__ = ["__AUTOLOAD_ERRORS__"]

__AUTOLOAD_ERRORS__: dict[str, str] = {}

# --- Importes estáticos conocidos (opcional/retrocompatibilidad) ---
try:
    from . import momentum  # noqa: F401
except Exception as e:
    __AUTOLOAD_ERRORS__["momentum"] = repr(e)

try:
    from . import vol_breakout  # noqa: F401
except Exception as e:
    __AUTOLOAD_ERRORS__["vol_breakout"] = repr(e)


# --- Auto-discovery dinámico de todos los módulos en el paquete ---
def _auto_discover() -> None:
    import importlib
    import pkgutil

    from . import __path__ as _PKG_PATH

    for modinfo in pkgutil.iter_modules(_PKG_PATH):
        name = modinfo.name
        if name.startswith("_"):
            continue
        # Evita reimportar los ya importados arriba
        if name in ("momentum", "vol_breakout"):
            continue
        try:
            importlib.import_module(f"{__name__}.{name}")
        except Exception as e:  # guardamos error para diagnóstico
            __AUTOLOAD_ERRORS__[name] = repr(e)


_auto_discover()

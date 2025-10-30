# ============================================================
# src/core/config_loader.py — Cargador central de configuración
# ------------------------------------------------------------
# OBJETIVO:
#   Proveer una API simple y fiable para leer la configuración
#   del bot desde un archivo YAML (src/config/config.yaml) y
#   aplicar "overrides" desde variables de entorno (.env).
#
# ¿POR QUÉ ASÍ?
#   - Separación de concerns: la config vive fuera del código.
#   - Reproducibilidad: un YAML versionado + .env privado.
#   - Ergonomía: una sola función get_config() para todo el proyecto.
#
# CARACTERÍSTICAS:
#   - Cache interna (evita relecturas del archivo en cada import).
#   - Overrides vía .env (p.ej., USE_TESTNET, LOG_LEVEL, SYMBOL).
#   - Validación mínima del esquema (claves imprescindibles).
#   - Helpers para leer rutas y tipos (bool, float, etc.).
#
# USO BÁSICO:
#   from core.config_loader import get_config, reload_config
#   cfg = get_config()
#   symbol = cfg["trading"]["symbol"]
#
#   # Si editas el YAML en caliente y quieres recargar:
#   cfg = reload_config()
#
# NOTA:
#   Este módulo NO configura logs (evita dependencia circular).
#   Si quieres loggear, hazlo donde lo invoques (p.ej. en Engine).
# ============================================================

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional

from dotenv import load_dotenv
import yaml

# ------------------------------------------------------------
# Constantes y cache interna
# ------------------------------------------------------------
DEFAULT_CONFIG_PATH = Path("src/config/config.yaml")

# Cache global para evitar relecturas constantes.
# Se invalida llamando a reload_config().
_CONFIG_CACHE: Optional[Dict[str, Any]] = None


# ------------------------------------------------------------
# Utilidades internas de tipos / paths
# ------------------------------------------------------------
def _to_bool(value: Any, default: bool = False) -> bool:
    """
    Convierte una cadena/valor a booleano de forma robusta.
    Acepta: "true"/"false", "1"/"0", True/False, etc.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in {"1", "true", "t", "yes", "y", "on"}


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ensure_file_exists(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de configuración: {path.resolve()}")


def _deep_set(d: MutableMapping[str, Any], keys: Iterable[str], value: Any) -> None:
    """
    Asigna value en un diccionario anidado siguiendo la lista de 'keys'.
    Crea los nodos intermedios si no existen.
    """
    keys = list(keys)
    current = d
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value


# ------------------------------------------------------------
# Carga YAML + overrides desde .env
# ------------------------------------------------------------
def _load_yaml_config(path: Path) -> Dict[str, Any]:
    _ensure_file_exists(path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"El YAML debe mapear a dict en la raíz. Archivo: {path}")
    return data


def _apply_env_overrides(cfg: Dict[str, Any]) -> None:
    """
    Aplica overrides de variables de entorno (.env) sobre el dict `cfg`.
    Mantén este mapeo corto y explícito para evitar sorpresas.
    """
    # Cargar variables definidas en .env (si existe)
    load_dotenv(override=False)

    # Mapeo: ENV_VAR -> (ruta en config.yaml)
    ENV_TO_CFG: Dict[str, tuple[str, str]] = {
        # Entorno / logging
        "USE_TESTNET": ("environment", "use_testnet"),
        "MODE": ("environment", "mode"),
        "LOG_LEVEL": ("environment", "log_level"),
        # Trading
        "SYMBOL": ("trading", "symbol"),
        "CYCLE_DELAY": ("trading", "cycle_delay"),
        "TRADE_FEE_BPS": ("trading", "trade_fee_bps"),
        "SLIPPAGE_BPS": ("trading", "slippage_bps"),
        # Activos (útil si cambias base/quote dinámicamente)
        "BASE_ASSET": ("trading", "base_asset"),
        "QUOTE_ASSET": ("trading", "quote_asset"),
        # Fuente de datos (por si alternas proveedor)
        "DATA_SOURCE": ("data", "source"),
        "DATA_DIR": ("data", "dir"),
    }

    for env_var, path_keys in ENV_TO_CFG.items():
        if env_var not in os.environ:
            continue
        raw = os.getenv(env_var)

        # Importante para mypy: este valor puede ser bool | float | str
        value: Any

        # Conversión de tipos según clave destino
        if path_keys == ("environment", "use_testnet"):
            value = _to_bool(
                raw, default=bool(cfg.get("environment", {}).get("use_testnet", False))
            )
        elif path_keys == ("trading", "cycle_delay"):
            value = _to_float(raw, default=float(cfg.get("trading", {}).get("cycle_delay", 1.0)))
        elif path_keys in {("trading", "trade_fee_bps"), ("trading", "slippage_bps")}:
            # Mantener estas como float (bps) para evitar errores de tipos (mypy)
            default_key = path_keys[1]
            value = _to_float(raw, default=float(cfg.get("trading", {}).get(default_key, 0.0)))
        else:
            # Strings por defecto (incluye MODE, LOG_LEVEL, SYMBOL, BASE/QUOTE, DATA_SOURCE/DIR)
            value = raw

        _deep_set(cfg, path_keys, value)


# ------------------------------------------------------------
# Validación mínima del esquema (imprescindibles)
# ------------------------------------------------------------
def _validate_schema(cfg: Dict[str, Any]) -> None:
    """
    Valida que existan las secciones y claves mínimas.
    Lanza ValueError si falta algo crítico.
    """
    required_paths = [
        ("environment", "use_testnet"),
        ("environment", "mode"),
        ("environment", "log_level"),
        ("trading", "symbol"),
        ("trading", "cycle_delay"),
        ("trading", "trade_fee_bps"),
        ("trading", "slippage_bps"),
        ("strategy", "name"),
        ("data", "source"),
    ]

    missing: List[str] = []
    for path_keys in required_paths:
        node: Any = cfg
        ok = True
        for k in path_keys:
            if not isinstance(node, dict) or k not in node:
                ok = False
                break
            node = node[k]
        if not ok:
            missing.append(".".join(path_keys))

    if missing:
        raise ValueError(
            "Faltan claves imprescindibles en config.yaml (o tras overrides): " + ", ".join(missing)
        )


# ------------------------------------------------------------
# API pública
# ------------------------------------------------------------
def get_config(path: Optional[Path | str] = None, use_cache: bool = True) -> Dict[str, Any]:
    """
    Devuelve la configuración del bot como diccionario.
    - path: ruta alternativa al YAML (opcional).
    - use_cache: si True, reutiliza la última carga (más rápido).

    NOTA:
    - Si editas el YAML durante la ejecución y quieres forzar recarga,
      usa reload_config().
    """
    global _CONFIG_CACHE
    if use_cache and _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg = _load_yaml_config(cfg_path)
    _apply_env_overrides(cfg)
    _validate_schema(cfg)

    _CONFIG_CACHE = cfg
    return cfg


def reload_config(path: Optional[Path | str] = None) -> Dict[str, Any]:
    """
    Fuerza la recarga del YAML y re-aplica overrides del .env.
    Útil si cambias parámetros en caliente (p.ej., durante I+D).
    """
    global _CONFIG_CACHE
    _CONFIG_CACHE = None
    return get_config(path=path, use_cache=False)


# ------------------------------------------------------------
# Helpers opcionales de lectura (azúcar sintáctico)
# ------------------------------------------------------------
def get_nested(cfg: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """
    Acceso seguro a valores anidados: get_nested(cfg, "trading", "symbol")
    Devuelve `default` si no existe la ruta.
    """
    node: Any = cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


# ------------------------------------------------------------
# Modo prueba manual (útil si ejecutas: python src/core/config_loader.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        cfg = get_config()
        print("✅ Config cargada correctamente.")
        print(f"  - use_testnet   : {get_nested(cfg, 'environment', 'use_testnet')}")
        print(f"  - mode          : {get_nested(cfg, 'environment', 'mode')}")
        print(f"  - log_level     : {get_nested(cfg, 'environment', 'log_level')}")
        print(f"  - symbol        : {get_nested(cfg, 'trading', 'symbol')}")
        print(f"  - base_asset    : {get_nested(cfg, 'trading', 'base_asset')}")
        print(f"  - quote_asset   : {get_nested(cfg, 'trading', 'quote_asset')}")
        print(f"  - cycle_delay   : {get_nested(cfg, 'trading', 'cycle_delay')}")
        print(f"  - trade_fee_bps : {get_nested(cfg, 'trading', 'trade_fee_bps')}")
        print(f"  - slippage_bps  : {get_nested(cfg, 'trading', 'slippage_bps')}")
        print(f"  - data.source   : {get_nested(cfg, 'data', 'source')}")
        print(f"  - data.dir      : {get_nested(cfg, 'data', 'dir')}")
    except Exception as e:
        print("❌ Error cargando configuración:", e)

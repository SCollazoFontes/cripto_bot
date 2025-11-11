# src/core/strategy_runtime.py
"""
Utilidades de *runtime* para estrategias.

- Autocarga de módulos en `src.strategies` para poblar el registro (decoradores).
- Construcción de estrategias desde CLI (`--strategy`, `--params`).
- Resolución flexible si el registro está vacío:
    * "src.strategies.momentum:MomentumStrategy"
    * "momentum"  → intenta "src.strategies.momentum" y clase heurística.
- Fallback de configuración: si el __init__ no acepta kwargs, instancia vacía y
  aplica params vía `set_params`, `configure` o `update_params` (si existen).
- Helpers para runners: build_position_state, decide_order, map_decision_to_plain.
- **Extensión**: soporte de carga por ruta absoluta con `load_strategy(path)` para modo paper/live.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import pkgutil
import sys
from types import ModuleType
from typing import Any, Protocol, runtime_checkable

from strategies.base import (
    OrderRequest,
    PositionState,
    Strategy,
    get_strategy_class,
)

# ================================ Autocarga ================================

_AUTOLOADED: bool = False


def _autoload_strategies() -> None:
    """Importa dinámicamente submódulos de `src.strategies` (excepto base y __init__)."""
    global _AUTOLOADED
    if _AUTOLOADED:
        return
    try:
        pkg = importlib.import_module("src.strategies")
    except ModuleNotFoundError:
        _AUTOLOADED = True
        return
    pkg_path = getattr(pkg, "__path__", [])
    for modinfo in pkgutil.iter_modules(pkg_path):
        name = modinfo.name
        if name in {"base", "__init__"}:
            continue
        fqmn = f"src.strategies.{name}"
        try:
            importlib.import_module(fqmn)
        except Exception:
            continue
    _AUTOLOADED = True


# ============================= Parsing de CLI ==============================


def _parse_strategy_spec(spec: str) -> tuple[str, str | None]:
    """
    Acepta:
      - "momentum"                          -> (key="momentum", cls=None)
      - "momentum:MomentumStrategy"         -> (key="momentum", cls="MomentumStrategy")
      - "src.strategies.momentum:MomentumStrategy"
    """
    if ":" in spec:
        left, right = spec.split(":", 1)
        return left.strip(), (right.strip() or None)
    return spec.strip(), None


def _to_camel_case(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.replace("-", "_").split("_") if p)


def _import_class(module_name: str, class_name: str) -> type[Strategy]:
    mod = importlib.import_module(module_name)
    obj = getattr(mod, class_name)
    if not isinstance(obj, type) or not issubclass(obj, Strategy):
        raise TypeError(f"'{module_name}:{class_name}' no es subclase de Strategy.")
    return obj


def _find_strategy_class_in_module(
    module_name: str, preferred: str | None = None
) -> type[Strategy]:
    mod = importlib.import_module(module_name)
    if preferred and hasattr(mod, preferred):
        cand = getattr(mod, preferred)
        if isinstance(cand, type) and issubclass(cand, Strategy):
            return cand
    candidates: list[type[Strategy]] = []
    for attr in dir(mod):
        if attr.startswith("_"):
            continue
        obj = getattr(mod, attr)
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            candidates.append(obj)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise KeyError(f"No hay subclases de Strategy en '{module_name}'.")
    raise KeyError(
        f"Múltiples estrategias en '{module_name}': {[c.__name__ for c in candidates]}. Especifica 'modulo:Clase'."
    )


# ============================== Construcción ===============================


def _try_configure_post_init(instance: Strategy, params: dict[str, Any]) -> bool:
    """
    Intenta configurar la estrategia ya instanciada llamando a uno de:
      - set_params(**params) o set_params(params)
      - configure(**params) o configure(params)
      - update_params(**params) o update_params(params)
    Devuelve True si aplicó alguno.
    """
    for name in ("set_params", "configure", "update_params"):
        if hasattr(instance, name):
            fn = getattr(instance, name)
            try:
                fn(**params)
                return True
            except TypeError:
                try:
                    fn(params)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
    return False


def make_strategy_from_cli(spec: str, params_json: str | None = None) -> Strategy:
    """Construye e instancia una `Strategy` desde CLI con resolución flexible y fallback."""
    _autoload_strategies()
    key, cls_name = _parse_strategy_spec(spec)

    # 1) Intento por registro
    lookup = f"{key}:{cls_name}" if cls_name else key
    StrategyCls: type[Strategy]
    try:
        StrategyCls = get_strategy_class(lookup)
    except KeyError:
        # 2) "modulo:Clase" totalmente cualificado
        if "." in key and cls_name:
            StrategyCls = _import_class(key, cls_name)
        else:
            # 3) Clave corta → módulo heurístico y clase heurística
            module_guess = f"src.strategies.{key}"
            class_guess = f"{_to_camel_case(key)}Strategy" if not cls_name else cls_name
            StrategyCls = _find_strategy_class_in_module(module_guess, preferred=class_guess)

    # Params JSON
    if params_json is None or params_json.strip() == "":
        params: dict[str, Any] = {}
    else:
        try:
            parsed = json.loads(params_json)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"JSON inválido en --params: {params_json}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("--params debe ser un objeto JSON {clave: valor}.")
        params = parsed

    # Instancia
    try:
        return StrategyCls(**params)
    except TypeError as err:
        # Fallback: instanciar sin kwargs y configurar si es posible
        inst = StrategyCls()
        if params and not _try_configure_post_init(inst, params):
            raise TypeError(
                f"No se pudo instanciar '{StrategyCls.__name__}' con params={params} "
                "ni aplicar configuración post-init (set_params/configure/update_params)."
            ) from err
        return inst


# ============================ Helpers de runtime ============================


def build_position_state(
    *, qty: float, avg_price: float, equity: float, cash: float
) -> PositionState:
    """Construye un PositionState y añade equity/cash como atributos auxiliares."""
    side: str | None
    if qty > 0:
        side = "LONG"
    elif qty < 0:
        side = "SHORT"
    else:
        side = None
    pos_state = PositionState(
        side=("BUY" if str(side) == "BUY" else ("SELL" if side else None)),  # type: ignore[arg-type]
        qty=float(qty),
        entry_price=float(avg_price) if avg_price else None,
    )
    pos_state.equity = float(equity)
    pos_state.cash = float(cash)
    return pos_state


def decide_order(
    strategy: Strategy, bar: dict[str, Any], state: PositionState
) -> OrderRequest | None:
    """Sincroniza estado y delega en on_bar."""
    strategy.position = state
    # usar getattr para tolerancia con atributos opcionales
    decision = getattr(strategy, "position", None)
    if decision is not None:
        # lógica si existe...
        pass

    # Si la estrategia proporciona atributos opcionales, usarlos
    position_val = getattr(strategy, "position", None)
    if position_val is not None:
        pass

    # Crear order_req usando las variables locales disponibles en tu contexto actual
    # (ajusta symbol, side_converted, qty_val, reason_text según tu código real)
    # Este es un ejemplo; reemplaza con las variables reales de tu función.
    # order_req = OrderRequest(
    #     symbol=symbol,
    #     side=side_converted,
    #     qty=qty_val,
    #     reason=reason_text,
    # )
    # if decision_val is not None:
    #     order_req.decision = decision_val
    # if meta_val is not None:
    #     order_req.meta = meta_val

    return strategy.on_bar(bar)


def map_decision_to_plain(req: OrderRequest) -> dict[str, Any]:
    """Convierte OrderRequest a dict simple para runners."""
    return {
        "type": str(req.decision),
        "side": str(req.side),
        "qty_frac": float(req.qty),
        "price": None if req.price is None else float(req.price),
        "reason": req.reason or "",
        "meta": dict(req.meta or {}),
    }


# =========================== Depuración opcional ===========================


def list_registered_strategies() -> list[str]:
    """Lista las claves registradas si el base expone el registro; si no, []."""
    _autoload_strategies()
    try:
        base = importlib.import_module("src.strategies.base")
        reg = getattr(base, "_STRATEGY_REGISTRY", None) or getattr(base, "STRATEGY_REGISTRY", None)
        if isinstance(reg, dict):
            return list(reg.keys())
    except Exception:
        pass
    return []


# ============================ Carga desde ruta =============================


@runtime_checkable
class StrategyProtocol(Protocol):
    """Protocolo informal para validar instancias cargadas dinámicamente."""

    # def on_start(self, broker: Any, symbol: str) -> None: ...
    # def on_tick(self, broker: Any, symbol: str, now: float) -> None: ...
    # def on_stop(self, broker: Any, symbol: str) -> None: ...
    ...


def _load_module_from_path(path: str | Path) -> ModuleType:
    """Carga un módulo Python desde un path absoluto/relativo."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Estrategia no encontrada: {p}")
    mod_name = f"strategy_{p.stem}_{abs(hash(str(p))) & 0xFFFFFFFF:x}"
    spec = importlib.util.spec_from_file_location(mod_name, str(p))
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo crear el spec para: {p}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[call-arg]
    except Exception as exc:
        raise ImportError(f"Fallo importando la estrategia {p}: {exc}") from exc
    return module


def _extract_strategy_instance(mod: ModuleType) -> Any:
    """Busca Strategy(), build_strategy() o strategy en el módulo."""
    StrategyCls = getattr(mod, "Strategy", None)
    if StrategyCls is not None and callable(StrategyCls):
        try:
            return StrategyCls()
        except TypeError:
            pass
    build_fn = getattr(mod, "build_strategy", None)
    if build_fn is not None and callable(build_fn):
        return build_fn()
    inst = getattr(mod, "strategy", None)
    if inst is not None:
        return inst
    raise ValueError(
        "No se encontró una estrategia válida. Define una clase `Strategy`, "
        "una función `build_strategy()` o un objeto `strategy` instanciado."
    )


def load_strategy(spec: str, params_json: str | None = None) -> Strategy:
    """
    Carga/instancia una Strategy a partir de:
      - Ruta a fichero: ".../algo.py"  -> carga como módulo suelto
      - "modulo:Clase": "src.strategies.momentum:MomentumStrategy"
      - Clave corta registrada o módulo corto: "momentum"
    """
    _autoload_strategies()

    # 1) Ruta de fichero → usa el loader de path
    if any(tok in spec for tok in ("/", "\\", ".py")):
        # Esperamos que el .py use imports relativos (p.ej. ".base") → si es así,
        # es mejor preferir el modo módulo/registro. Este path loader queda para
        # estrategias standalone. Mantener por compatibilidad.
        return _load_strategy_from_path_like(spec, params_json)

    # 2) "modulo:Clase" totalmente cualificado → import directo
    if ":" in spec:
        module_name, class_name = _parse_strategy_spec(spec)
        if "." in module_name and class_name:
            StrategyCls = _import_class(module_name, class_name)
            params = {} if not params_json else _ensure_params_dict(params_json)
            try:
                return StrategyCls(**params)
            except TypeError:
                inst = StrategyCls()
                if params and not _try_configure_post_init(inst, params):
                    raise
                return inst

    # 3) Clave corta / módulo corto → registro + heurística
    return make_strategy_from_cli(spec, params_json)


# Helpers privados usados arriba (minimos, reusan tus utils existentes)
def _ensure_params_dict(params_json: str | None) -> dict[str, Any]:
    if params_json is None or not params_json.strip():
        return {}
    try:
        obj = json.loads(params_json)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"JSON inválido en --params: {params_json}") from exc
    if not isinstance(obj, dict):
        raise ValueError("--params debe ser un objeto JSON {clave: valor}.")
    return obj


def _load_strategy_from_path_like(path_like: str, params_json: str | None) -> Strategy:
    """Carga una estrategia desde una ruta (archivo .py o módulo)."""
    mod = _load_module_from_path(path_like)
    candidates: list[type[Strategy]] = []
    preferred = None

    try:
        stem = Path(path_like).stem
        preferred = f"{_to_camel_case(stem)}Strategy"
    except Exception:
        pass

    for attr in dir(mod):
        obj = getattr(mod, attr)
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            candidates.append(obj)

    StrategyCls: type[Strategy] | None = None
    if preferred:
        for c in candidates:
            if c.__name__ == preferred:
                StrategyCls = c
                break

    if not StrategyCls and candidates:
        StrategyCls = candidates[0]

    if not StrategyCls:
        raise KeyError(f"No se encontró subclase de Strategy en {path_like}")

    params = _ensure_params_dict(params_json)
    try:
        return StrategyCls(**params)
    except TypeError:
        inst = StrategyCls()
        if params and not _try_configure_post_init(inst, params):
            raise
        return inst

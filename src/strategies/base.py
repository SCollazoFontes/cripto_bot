# src/strategies/base.py
"""
Tipos y utilidades base para estrategias.

Incluye:
- Tipos de órdenes y fills.
- `PositionState` (estado mínimo de posición).
- Interfaz `Strategy` con dispatcher de on_bar:
    - Soporta tanto on_bar(broker, executor, symbol, bar) (live/paper)
      como on_bar(bar) (backtests simples).
- Registro global de estrategias: register_strategy / get_strategy_class / list_strategies.
- Helpers de órdenes y utilidades numéricas.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
import math
from typing import (
    Any,
    ClassVar,
    Literal,
)

# ------------------------------- Tipos -------------------------------------

OrderSide = Literal["BUY", "SELL"]


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    qty: float
    price: float | None = None
    reason: str = ""
    order_type: str = "MARKET"  # MARKET / LIMIT
    time_in_force: str | None = None  # GTC/IOC/FOK

    # Campos extra usados en estrategias/rastreos
    decision: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": float(self.qty),
            "price": None if self.price is None else float(self.price),
            "reason": self.reason,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "decision": self.decision,
            "meta": self.meta,
        }


@dataclass
class Fill:
    price: float
    qty: float
    ts: float | None = None


@dataclass
class Order:
    id: str
    request: OrderRequest
    status: str = "NEW"
    fills: Iterable[Fill] | None = None


@dataclass
class PositionState:
    """
    Estado mínimo de una posición para que otros módulos consulten/actualicen
    sin acoplarse a una implementación concreta.
    """

    symbol: str = ""
    side: OrderSide | None = None
    qty: float = 0.0
    entry_price: float | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    last_price: float | None = None

    @property
    def has_position(self) -> bool:
        return self.qty > 0.0

    @property
    def avg_price(self) -> float | None:
        return self.entry_price


# ----------------------------- Interfaz base -------------------------------


class Strategy:
    """
    Interfaz común para estrategias.

    Dispatcher de `on_bar`:
      - Si el engine llama con 4 args (broker, executor, symbol, bar),
        reenviamos a `on_bar_live(...)`.
      - Si llama con 1 arg (bar), reenviamos a `on_bar_bar(bar)` y
        devolvemos opcionalmente un OrderRequest (para backtests sencillos).
    """

    name: ClassVar[str] = "strategy"

    # ---------------------- Dispatcher compatible ----------------------
    def on_bar(self, *args, **kwargs):
        # live/paper: on_bar(broker, executor, symbol, bar)
        if len(args) == 4:
            broker, executor, symbol, bar = args
            return self.on_bar_live(broker, executor, symbol, bar)

        # backtest simple: on_bar(bar)
        if len(args) == 1 and isinstance(args[0], dict):
            return self.on_bar_bar(args[0])

        # Firma no soportada: no hacemos nada
        return None

    # -------------------- Firmas específicas a sobrescribir -----------------

    def on_bar_live(self, broker, executor, symbol: str, bar: dict[str, Any]) -> None:
        """
        Firma preferida para live/paper. Debe ejecutar órdenes vía `executor`.
        """
        return None  # por defecto no hace nada

    def on_bar_bar(self, bar: dict[str, Any]) -> OrderRequest | None:
        """
        Firma alternativa para backtests simples que devuelven OrderRequest.
        """
        return None  # por defecto no hace nada

    # Ciclo de vida ----------------------------------------------------------

    def on_start(self, context: dict[str, Any]) -> None:
        _ = context  # hook opcional

    def on_end(self, context: dict[str, Any]) -> None:
        _ = context  # hook opcional


# ------------------------------ Helpers de orden ---------------------------


def _open_long(symbol: str, qty: float, reason: str = "entry") -> OrderRequest:
    return OrderRequest(
        symbol=symbol, side="BUY", qty=float(qty), reason=reason, order_type="MARKET"
    )


def _close_long(symbol: str, qty: float, reason: str = "exit") -> OrderRequest:
    return OrderRequest(
        symbol=symbol, side="SELL", qty=float(qty), reason=reason, order_type="MARKET"
    )


def _open_short(symbol: str, qty: float, reason: str = "entry") -> OrderRequest:
    return OrderRequest(
        symbol=symbol, side="SELL", qty=float(qty), reason=reason, order_type="MARKET"
    )


def _close_short(symbol: str, qty: float, reason: str = "exit") -> OrderRequest:
    return OrderRequest(
        symbol=symbol, side="BUY", qty=float(qty), reason=reason, order_type="MARKET"
    )


# ------------------------------ Utilidades numéricas -----------------------


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def pct_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (float(b) - float(a)) / float(a)


def returns(series: Iterable[float]) -> Iterable[float]:
    it = iter(series)
    try:
        prev = float(next(it))
    except StopIteration:
        return []
    out = []
    for x in it:
        x = float(x)
        if prev != 0.0:
            out.append((x - prev) / prev)
        else:
            out.append(0.0)
        prev = x
    return out


def zscore(win: deque[float]) -> float:
    n = len(win)
    if n == 0:
        return 0.0
    m = sum(win) / n
    var = sum((x - m) ** 2 for x in win) / max(1, n - 1)
    sd = math.sqrt(var)
    if sd == 0.0:
        return 0.0
    return (win[-1] - m) / sd


def atr_like(
    highs: Iterable[float], lows: Iterable[float], closes: Iterable[float], n: int = 14
) -> float:
    hs = list(map(float, highs))
    ls = list(map(float, lows))
    cs = list(map(float, closes))
    if not hs or not ls or not cs:
        return 0.0
    trs = []
    for i in range(1, min(len(hs), len(ls), len(cs))):
        h, low, pc = hs[i], ls[i], cs[i - 1]
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    if not trs:
        return 0.0
    return sum(trs[-n:]) / min(n, len(trs))


def ema(prev: float | None, x: float, alpha: float) -> float:
    if prev is None:
        return float(x)
    a = float(clamp(alpha, 0.0, 1.0))
    return float(a * x + (1.0 - a) * prev)


def simple_moving_average(win: deque[float]) -> float:
    if not win:
        return 0.0
    return float(sum(win) / len(win))


def to_notional(qty: float, price: float) -> float:
    return float(qty) * float(price)


def from_notional(notional: float, price: float) -> float:
    if price == 0.0:
        return 0.0
    return float(notional) / float(price)


def units_bound(notional: float, price: float, max_units: float | None = None) -> float:
    units = from_notional(notional, price)
    if max_units is not None:
        units = min(units, float(max_units))
    return max(0.0, units)


# ------------------------------- Registro ---------------------------------

_REGISTRY: dict[str, type[Strategy]] = {}


def register_strategy(*args):
    """
    Registra una estrategia en el registro global.

    Usos soportados:
      - @register_strategy("nombre")
      - @register_strategy                    # usa cls.name o cls.__name__.lower()
      - register_strategy("nombre", Clase)    # llamada directa (compat)
    """

    def _register(name: str, cls: type[Strategy]) -> type[Strategy]:
        _REGISTRY[name] = cls
        return cls

    # Llamada directa: register_strategy("name", cls)
    if len(args) == 2 and isinstance(args[0], str) and callable(args[1]):
        name, cls = args  # type: ignore[misc]
        return _register(name, cls)

    # Decorador con nombre: @register_strategy("name")
    if len(args) == 1 and isinstance(args[0], str):
        name = args[0]

        def _decorator(cls: type[Strategy]) -> type[Strategy]:
            return _register(name, cls)

        return _decorator

    # Decorador sin paréntesis: @register_strategy
    if len(args) == 1 and callable(args[0]):
        cls = args[0]
        default_name = getattr(cls, "name", cls.__name__).lower()
        return _register(default_name, cls)  # type: ignore[arg-type]

    # Fallback: inferimos nombre en decorador
    def _decorator_infer(cls: type[Strategy]) -> type[Strategy]:
        name = getattr(cls, "name", cls.__name__).lower()
        return _register(name, cls)

    return _decorator_infer


def get_strategy_class(name: str) -> type[Strategy]:
    if name in _REGISTRY:
        return _REGISTRY[name]
    raise KeyError(f"Estrategia no registrada: {name}")


def list_strategies() -> dict[str, type[Strategy]]:
    return dict(_REGISTRY)


__all__ = [
    "OrderSide",
    "OrderRequest",
    "Fill",
    "Order",
    "PositionState",
    "Strategy",
    "register_strategy",
    "get_strategy_class",
    "list_strategies",
    "pct_change",
    "returns",
    "zscore",
    "atr_like",
    "ema",
    "clamp",
    "simple_moving_average",
    "to_notional",
    "from_notional",
    "units_bound",
    "_open_long",
    "_close_long",
    "_open_short",
    "_close_short",
]

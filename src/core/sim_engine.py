# src/core/sim_engine.py

"""
SimEngine: orquestador de backtests (feed → estrategia → broker → métricas → eventos).

✅ Principios:
- NO hace I/O a disco (eso queda para herramientas tipo reporter/CLI).
- Compatibilidad con estrategias tipo `on_bar(bar, ctx)` y tipo `on_price(...) -> Signal`.
- Callbacks/hook de eventos para enchufar reporter/dash sin tocar el loop.
- Métricas internas en vivo (equity, drawdown, exposición, recuento de trades).
- Política de sizing opcional y función de ejecución opcional (para adaptarse al broker real).

📦 Uso típico (desde tu CLI existente):
    from src.core.sim_engine import SimEngine, SimEngineConfig

    cfg = SimEngineConfig(
        symbol="BTCUSDT",
        ts_field="t_close",      # o 'end_time' si tu feed expone ISO8601 (el engine ahora lo convierte)
        price_field="close",
        exit_at_end=True,
    )
    engine = SimEngine(cfg)

    # Suscribe reporter/dash a eventos (opcional):
    engine.on("equity", on_equity_callback)
    engine.on("trade", on_trade_callback)
    engine.on("log", on_log_callback)

    # Ejecuta:
    engine.run(feed=csv_feed, strategy=my_strategy, broker=sim_broker)

Autor: Proyecto Criptos (2025-11-03)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

Number = Union[int, float]


# ----------------------------- #
#  Configuración y APIs públicas
# ----------------------------- #


@dataclass
class SimEngineConfig:
    """Parámetros del engine.

    Args:
        symbol: Símbolo que se está operando (p. ej. "BTCUSDT").
        ts_field: Nombre de la columna/clave con timestamp (ms/seg/ISO, el engine lo normaliza a ms).
        price_field: Nombre de la columna/clave de precio que se usará para mark-to-market y fills.
        exit_at_end: Si True, cierra la posición al final del feed al precio del último bar.
        sizing_policy: Callable opcional que transforma una señal de la estrategia en órdenes.
        interpret_signal: Callable opcional para normalizar/consolidar señales (p.ej. mapear strings a {+1,0,-1}).
        execute_fn: Callable opcional para ejecutar órdenes sobre el broker (si no se pasa, se autodetecta).
        max_bars: Límite duro de barras a procesar (útil en depuración).
    """

    symbol: str
    ts_field: str
    price_field: str
    exit_at_end: bool = True
    sizing_policy: Optional[Callable[[Any, "StrategyContext"], Optional[Dict[str, Any]]]] = None
    interpret_signal: Optional[Callable[[Any], Any]] = None
    execute_fn: Optional[Callable[["BrokerAdapter", str, Number, Number, int], Any]] = None
    max_bars: Optional[int] = None


# ----------------------------- #
#  Infraestructura de eventos
# ----------------------------- #


class EventBus:
    """Bus de eventos muy simple."""

    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    def on(self, event: str, fn: Callable[[Dict[str, Any]], None]) -> None:
        self._subs.setdefault(event, []).append(fn)

    def emit(self, event: str, payload: Dict[str, Any]) -> None:
        for fn in self._subs.get(event, []):
            try:
                fn(payload)
            except Exception:
                # No interrumpir el loop por un callback externo (reporter/dash).
                pass


# ----------------------------- #
#  Métricas internas en vivo
# ----------------------------- #


@dataclass
class LiveMetrics:
    """Acumuladores O(1) para métricas rápidas en tiempo real."""

    equity: float = 0.0
    eq_peak: float = 0.0
    drawdown: float = 0.0  # max drawdown actual (peak - equity)/peak
    trades: int = 0
    wins: int = 0
    last_trade_pnl: float = 0.0
    exposure: float = 0.0  # |posición * precio| / equity (si equity > 0)

    def update_equity(self, equity_now: float) -> None:
        self.equity = float(equity_now)
        if self.equity > self.eq_peak:
            self.eq_peak = self.equity
        if self.eq_peak > 0.0:
            self.drawdown = (self.eq_peak - self.equity) / self.eq_peak
        else:
            self.drawdown = 0.0

    def update_exposure(self, position_qty: float, mark_price: float) -> None:
        notional = abs(float(position_qty) * float(mark_price))
        self.exposure = (notional / self.equity) if self.equity > 0 else 0.0

    def register_trade(self, pnl: float) -> None:
        self.trades += 1
        self.last_trade_pnl = float(pnl)
        if pnl > 0:
            self.wins += 1


# ----------------------------- #
#  Adaptador de broker
# ----------------------------- #


class BrokerAdapter:
    """Adaptador blando sobre el broker.

    - Intenta usar métodos típicos: buy/sell/close_all/get_equity/position_qty/position_avg_price
    - Si los nombres difieren, usa `execute_fn` externo del config.
    """

    def __init__(self, broker: Any, cfg: SimEngineConfig, event_bus: EventBus) -> None:
        self._b = broker
        self._cfg = cfg
        self._events = event_bus

    # --- Interfaz de lectura mínima que el engine usará ---
    def equity(self) -> float:
        for name in ("get_equity", "equity", "equity_now", "nav"):
            if hasattr(self._b, name):
                attr = getattr(self._b, name)
                return float(attr() if callable(attr) else attr)
        raise AttributeError("Broker no expone 'equity' (get_equity/equity/nav).")

    def position_qty(self) -> float:
        for name in ("position_qty", "get_position_qty"):
            if hasattr(self._b, name):
                return float(getattr(self._b, name)())
        for name in ("position", "get_position"):
            if hasattr(self._b, name):
                pos = getattr(self._b, name)()
                if isinstance(pos, dict) and "qty" in pos:
                    return float(pos["qty"])
                if hasattr(pos, "qty"):
                    return float(pos.qty)
        return 0.0

    def position_avg_price(self) -> float:
        for name in ("position_avg_price", "get_position_avg_price"):
            if hasattr(self._b, name):
                return float(getattr(self._b, name)())
        for name in ("position", "get_position"):
            if hasattr(self._b, name):
                pos = getattr(self._b, name)()
                if isinstance(pos, dict) and "avg_price" in pos:
                    return float(pos["avg_price"])
                if hasattr(pos, "avg_price"):
                    return float(pos.avg_price)
        return 0.0

    # --- Ejecución (orden de mercado) ---
    def execute(self, side: str, qty: Number, price: Number, ts_ms: int) -> Any:
        """Ejecuta órden de mercado. Usa execute_fn si existe; si no, autodetecta."""
        side = side.upper()
        if self._cfg.execute_fn is not None:
            return self._cfg.execute_fn(self, side, qty, price, ts_ms)

        if side == "BUY":
            for name in ("buy", "market_buy", "execute_buy"):
                if hasattr(self._b, name):
                    return getattr(self._b, name)(
                        self._cfg.symbol, float(price), float(qty), int(ts_ms)
                    )
        elif side == "SELL":
            for name in ("sell", "market_sell", "execute_sell"):
                if hasattr(self._b, name):
                    return getattr(self._b, name)(
                        self._cfg.symbol, float(price), float(qty), int(ts_ms)
                    )
        raise AttributeError(
            "Broker no expone métodos BUY/SELL compatibles y no se pasó execute_fn."
        )

    def close_all(self, price: Number, ts_ms: int) -> Any:
        for name in ("close_all", "close_position", "flat"):
            if hasattr(self._b, name):
                return getattr(self._b, name)(self._cfg.symbol, float(price), int(ts_ms))
        qty = self.position_qty()
        if qty != 0:
            side = "SELL" if qty > 0 else "BUY"
            return self.execute(side, abs(qty), price, ts_ms)
        return None


# ----------------------------- #
#  Contexto para la estrategia
# ----------------------------- #


@dataclass
class StrategyContext:
    """Contexto que recibe la estrategia en cada paso."""

    step: int
    now_ts: int
    symbol: str
    last_price: float
    broker: BrokerAdapter
    events: EventBus
    state: Dict[str, Any] = field(default_factory=dict)

    # Helpers de logging
    def log(self, level: str, msg: str, **extra: Any) -> None:
        self.events.emit(
            "log",
            {
                "ts": self.now_ts,
                "level": level.upper(),
                "msg": msg,
                "step": self.step,
                "symbol": self.symbol,
                **extra,
            },
        )


# ----------------------------- #
#  Engine principal
# ----------------------------- #


class SimEngine:
    """Motor de simulación/orquestación."""

    def __init__(self, cfg: SimEngineConfig) -> None:
        self.cfg = cfg
        self.events = EventBus()
        self.metrics = LiveMetrics()
        self.state: Dict[str, Any] = {}

    # API para suscribirse a eventos
    def on(self, event: str, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Eventos: 'equity', 'trade', 'step', 'signal', 'log'."""
        self.events.on(event, fn)

    # Detección del modo de la estrategia
    @staticmethod
    def _has_on_bar(strategy: Any) -> bool:
        return hasattr(strategy, "on_bar") and callable(getattr(strategy, "on_bar"))

    @staticmethod
    def _has_on_price(strategy: Any) -> bool:
        return hasattr(strategy, "on_price") and callable(getattr(strategy, "on_price"))

    def run(self, feed: Iterable[Any], strategy: Any, broker: Any) -> None:
        """Ejecuta el loop principal.
        - Lee cada `bar` del feed (dict/obj).
        - Llama a la estrategia en modo `on_bar` o `on_price`.
        - Si hay señal y existe `sizing_policy`, la aplica y ejecuta.
        - Actualiza métricas y emite eventos.
        - Al final, si `exit_at_end`, cierra la posición.
        """
        adapter = BrokerAdapter(broker, self.cfg, self.events)
        has_on_bar = self._has_on_bar(strategy)
        has_on_price = self._has_on_price(strategy)
        if not (has_on_bar or has_on_price):
            raise TypeError(
                "La estrategia no implementa ni on_bar(bar, ctx) ni on_price(...)->Signal."
            )

        step = 0
        # Variables para recordar el último timestamp/precio procesados (para exit_at_end)
        last_now_ts: int = 0
        last_price: float = 0.0

        for bar in feed:
            if self.cfg.max_bars is not None and step >= int(self.cfg.max_bars):
                break

            now_ts, price = self._extract_ts_and_price(bar)
            last_now_ts, last_price = now_ts, price

            ctx = StrategyContext(
                step=step,
                now_ts=now_ts,
                symbol=self.cfg.symbol,
                last_price=price,
                broker=adapter,
                events=self.events,
                state=self.state,
            )

            # ---- Ejecución de la estrategia ----
            signal: Any = None
            if has_on_bar:
                try:
                    signal = strategy.on_bar(bar, ctx)
                except TypeError:
                    strategy.on_bar(bar, ctx)  # type: ignore[misc]
                    signal = None
            elif has_on_price:
                pos_qty = adapter.position_qty()
                pos_avg = adapter.position_avg_price()
                signal = strategy.on_price(price, now_ts, pos_qty, pos_avg)

            # ---- Señal → órdenes (opcional) ----
            if signal is not None:
                sig = self.cfg.interpret_signal(signal) if self.cfg.interpret_signal else signal
                self.events.emit(
                    "signal",
                    {
                        "ts": now_ts,
                        "step": step,
                        "signal": sig,
                        "price": price,
                        "symbol": self.cfg.symbol,
                    },
                )
                if self.cfg.sizing_policy is not None:
                    order = self.cfg.sizing_policy(sig, ctx)
                    if order:
                        self._execute_order_dict(adapter, order, price, now_ts)

            # ---- Métricas y eventos por paso ----
            eq = adapter.equity()
            self.metrics.update_equity(eq)
            self.metrics.update_exposure(adapter.position_qty(), price)
            self.events.emit(
                "equity",
                {
                    "ts": now_ts,
                    "step": step,
                    "equity": self.metrics.equity,
                    "drawdown": self.metrics.drawdown,
                    "exposure": self.metrics.exposure,
                    "price": price,
                },
            )
            self.events.emit(
                "step", {"ts": now_ts, "step": step, "price": price, "symbol": self.cfg.symbol}
            )

            step += 1

        # ---- Cierre al final si procede ----
        if self.cfg.exit_at_end and step > 0:
            qty = adapter.position_qty()
            if qty != 0:
                fill = adapter.close_all(last_price, last_now_ts)
                self._emit_trade(adapter, last_now_ts, last_price, fill, tag="exit_at_end")

        # Métrica final (best-effort)
        try:
            eq = adapter.equity()
            self.metrics.update_equity(eq)
        except Exception:
            pass

    # ------------------------- #
    #  Helpers internos del loop
    # ------------------------- #

    def _execute_order_dict(
        self,
        adapter: BrokerAdapter,
        order: Dict[str, Any],
        price: Number,
        ts_ms: int,
    ) -> None:
        """Normaliza pedidos y ejecuta con el broker.
        Formatos admitidos:
            {'side': 'BUY', 'qty': 0.1}
            {'qty_delta': +0.1}
            {'target_qty': 0.0}
        """
        qty_now = adapter.position_qty()

        if "target_qty" in order:
            target = float(order["target_qty"])
            delta = target - qty_now
            if abs(delta) > 0:
                side = "BUY" if delta > 0 else "SELL"
                fill = adapter.execute(side, abs(delta), price, ts_ms)
                self._emit_trade(adapter, ts_ms, float(price), fill, tag="target")
            return

        if "qty_delta" in order:
            delta = float(order["qty_delta"])
            if delta != 0:
                side = "BUY" if delta > 0 else "SELL"
                fill = adapter.execute(side, abs(delta), price, ts_ms)
                self._emit_trade(adapter, ts_ms, float(price), fill, tag="delta")
            return

        side = str(order.get("side", "")).upper()
        qty = float(order.get("qty", 0.0))
        if side in ("BUY", "SELL") and qty > 0:
            fill = adapter.execute(side, qty, price, ts_ms)
            self._emit_trade(adapter, ts_ms, float(price), fill, tag="explicit")
            return

        self.events.emit(
            "log",
            {
                "ts": ts_ms,
                "level": "WARNING",
                "msg": "Orden ignorada por formato no reconocido",
                "order": order,
            },
        )

    def _emit_trade(
        self,
        adapter: BrokerAdapter,
        ts_ms: int,
        price: float,
        broker_fill: Any,
        tag: str,
    ) -> None:
        """Emite evento 'trade' y actualiza contadores rápidos si hay PnL."""
        payload: Dict[str, Any] = {"ts": ts_ms, "price": price, "fill": broker_fill, "tag": tag}
        pnl = None
        if isinstance(broker_fill, dict) and "pnl" in broker_fill:
            try:
                pnl = float(broker_fill["pnl"])
            except Exception:
                pnl = None
        if pnl is not None:
            self.metrics.register_trade(pnl)
            payload["pnl"] = pnl
        self.events.emit("trade", payload)

    @staticmethod
    def _get_field(bar: Any, name: str) -> Any:
        """Lee 'name' de múltiples tipos de bar."""
        try:
            get = getattr(bar, "get", None)
            if callable(get):
                v = get(name, None)
                if v is not None:
                    return v
        except Exception:
            pass

        if isinstance(bar, dict):
            return bar.get(name)

        if hasattr(bar, name):
            try:
                return getattr(bar, name)
            except Exception:
                pass

        try:
            return bar[name]  # type: ignore[index]
        except Exception:
            pass

        try:
            d = getattr(bar, "__dict__", None)
            if isinstance(d, dict) and name in d:
                return d.get(name)
        except Exception:
            pass

        return None

    @staticmethod
    def _available_keys(bar: Any) -> List[str]:
        """Intenta listar las 'claves' disponibles del bar para diagnóstico."""
        keys: List[str] = []
        if isinstance(bar, dict):
            return [str(k) for k in bar.keys()]

        idx = getattr(bar, "index", None)
        if idx is not None:
            try:
                return [str(k) for k in list(idx)]
            except Exception:
                pass

        nf = getattr(bar, "_fields", None)
        if nf is not None:
            try:
                return [str(k) for k in list(nf)]
            except Exception:
                pass

        d = getattr(bar, "__dict__", None)
        if isinstance(d, dict):
            keys.extend([str(k) for k in d.keys()])

        try:
            attrs = dir(bar)
            for a in attrs:
                if a.startswith("_"):
                    continue
                try:
                    val = getattr(bar, a)
                except Exception:
                    continue
                if not callable(val):
                    keys.append(a)
        except Exception:
            pass

        seen = set()
        out: List[str] = []
        for k in keys:
            if k not in seen:
                out.append(k)
                seen.add(k)
        return out

    @staticmethod
    def _safe_int(x: Any) -> int:
        try:
            return int(x)
        except Exception:
            return 0

    @staticmethod
    def _to_millis(v: Any) -> Optional[int]:
        """Convierte un timestamp a milisegundos desde epoch, tolerando:
        - int/float (segundos o milisegundos)
        - str numérica ("1698950400", "1698950400000")
        - ISO8601: "2025-11-03T15:30:00Z", "2025-11-03 15:30:00+00:00", "2025-11-03 15:30:00.123456+00:00", etc.
        - pandas.Timestamp (usa .timestamp())
        Devuelve None si no puede convertir.
        """
        # numérico directo
        if isinstance(v, (int, float)):
            ts = int(v)
            if ts < 10**12:  # parece segundos → a ms
                ts *= 1000
            return ts

        # string
        if isinstance(v, str):
            s = v.strip()
            if s.isdigit():
                ts = int(s)
                if ts < 10**12:
                    ts *= 1000
                return ts
            try:
                f = float(s)
                ts = int(f)
                if ts < 10**12:
                    ts *= 1000
                return ts
            except Exception:
                pass
            # ISO8601 robusto
            from datetime import datetime, timezone

            # Normalizaciones:
            s2 = s.replace("Z", "+00:00")
            # Algunos parsers con %z no aceptan ':' en el offset → versión sin dos puntos
            s_no_colon_tz = s2
            if len(s2) >= 6 and (s2[-6] in ["+", "-"]) and s2[-3] == ":":
                # "YYYY-mm-dd ... +00:00" → "+0000"
                s_no_colon_tz = s2[:-3] + s2[-2:]
            # Intentos crecientes
            for cand in (s2, s_no_colon_tz):
                try:
                    dt = None
                    # 1) fromisoformat (acepta "+00:00")
                    try:
                        dt = datetime.fromisoformat(cand)
                    except Exception:
                        dt = None
                    # 2) strptime con microsegundos + tz
                    if dt is None:
                        try:
                            dt = datetime.strptime(cand, "%Y-%m-%d %H:%M:%S.%f%z")
                        except Exception:
                            dt = None
                    # 3) strptime sin microsegundos + tz
                    if dt is None:
                        try:
                            dt = datetime.strptime(cand, "%Y-%m-%d %H:%M:%S%z")
                        except Exception:
                            dt = None
                    # 4) sin tz → asumimos UTC
                    if dt is None:
                        try:
                            dt = datetime.fromisoformat(cand)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                        except Exception:
                            dt = None
                    if dt is not None:
                        return int(dt.timestamp() * 1000)
                except Exception:
                    continue
            return None

        # pandas.Timestamp o similares
        tsf = getattr(v, "timestamp", None)
        if callable(tsf):
            try:
                return int(tsf() * 1000)
            except Exception:
                return None

        return None

    def _extract_ts_and_price(self, bar: Any) -> tuple[int, float]:
        """
        Extrae (ts_ms, price) tolerando alias comunes si faltan los campos exactos.
        - ts candidates: cfg.ts_field, 't', 't_close', 't_open', 'start_time', 'end_time',
                         'timestamp', 'time', 'ts', 'time_ms', 'timestamp_ms'
        - price candidates: cfg.price_field, 'close', 'price', 'p', 'last', 'mark'
        Si falla, lanza KeyError incluyendo las 'keys' disponibles del bar.
        """
        ts_candidates = [
            self.cfg.ts_field,
            "t",
            "t_close",
            "t_open",
            "start_time",
            "end_time",
            "timestamp",
            "time",
            "ts",
            "time_ms",
            "timestamp_ms",
        ]
        price_candidates = [
            self.cfg.price_field,
            "close",
            "price",
            "p",
            "last",
            "mark",
        ]

        ts_val: Optional[int] = None
        for name in ts_candidates:
            if not name:
                continue
            v = self._get_field(bar, str(name))
            if v is None:
                continue
            ts_ms = self._to_millis(v)
            if ts_ms is not None:
                ts_val = ts_ms
                break

        price_val: Optional[float] = None
        for name in price_candidates:
            if not name:
                continue
            v = self._get_field(bar, str(name))
            if v is None:
                continue
            try:
                price_val = float(v)
                break
            except Exception:
                try:
                    price_val = float(str(v).replace(",", ".").strip())
                    break
                except Exception:
                    continue

        if ts_val is None or price_val is None:
            keys = self._available_keys(bar)
            if ts_val is None and price_val is None:
                raise KeyError(
                    f"El bar no contiene timestamp ni precio utilizable. "
                    f"Probar con ts={ts_candidates} y price={price_candidates}. "
                    f"Keys disponibles: {keys}"
                )
            elif ts_val is None:
                raise KeyError(
                    f"El bar no contiene un timestamp utilizable. "
                    f"Probar con: {ts_candidates}. Keys disponibles: {keys}"
                )
            else:
                raise KeyError(
                    f"El bar no contiene un precio utilizable. "
                    f"Probar con: {price_candidates}. Keys disponibles: {keys}"
                )

        return int(ts_val), float(price_val)

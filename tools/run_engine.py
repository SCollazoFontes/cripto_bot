# tools/run_engine.py

"""
Runner genérico (SimEngine) para ejecutar estrategias sobre un run reproducible.

Soporta dos estilos de estrategia:
A) on_bar(bar, ctx)  → la estrategia habla con el broker.
B) on_price(...)->Signal → la estrategia devuelve BUY/SELL/HOLD y el engine ejecuta.

Ejemplos:
  python -m tools.run_engine --run-dir runs/20251103T122556Z --strategy src.strategies.momentum --ts-field t_close --exit-at-end 1
  python -m tools.run_engine --run-dir runs/20251103T122556Z --strategy src.strategies.momentum:MomentumStrategy --ts-field t_close --exit-at-end 1
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib
import inspect
import json
import os
import sys
import time
from typing import Any, Dict, Iterable, Optional, Type

try:
    from src.data.feeds import CSVFeed
except Exception as e:
    print("[FATAL] No se pudo importar CSVFeed desde src.data.feeds:", e, file=sys.stderr)
    raise

try:
    from src.core.broker_sim import SimBroker
except Exception as e:
    print("[FATAL] No se pudo importar SimBroker desde src.core.broker_sim:", e, file=sys.stderr)
    raise

# Nuevo engine desacoplado
from src.core.sim_engine import SimEngine as CoreSimEngine, SimEngineConfig as CoreEngineConfig


# ---- config y contexto
@dataclass
class EngineConfig:
    run_dir: str
    data_csv: str
    equity_csv: str
    trades_csv: str
    ts_field: str = "t_close"
    price_field: str = "close"
    exit_at_end: bool = False
    symbol: str = "BTCUSDT"
    fees_bps: float = 2.5
    slip_bps: float = 1.0
    cash: float = 10_000.0
    allow_short: bool = False
    max_position_usd: float = 10_000.0
    min_notional_usd: float = 5.0


@dataclass
class EngineContext:
    cfg: EngineConfig
    feed: Any
    broker: Any
    step: int = 0
    now_ts: Optional[int] = None
    last_price: Optional[float] = None


# ---- import dinámico de estrategia
def import_strategy_class(strategy_path: str) -> Type | Any:
    if ":" in strategy_path:
        module_name, class_name = strategy_path.split(":", 1)
    else:
        module_name, class_name = strategy_path, None

    module = importlib.import_module(module_name)

    if hasattr(module, "STRATEGY_CLASS"):
        return getattr(module, "STRATEGY_CLASS")

    if class_name:
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if name.lower() == class_name.lower():
                return obj
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if class_name.lower() in name.lower():
                return obj

    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ == module.__name__ and (
            hasattr(obj, "on_bar") or hasattr(obj, "on_price")
        ):
            return obj

    if hasattr(module, "FACTORY") and callable(getattr(module, "FACTORY")):
        return getattr(module, "FACTORY")

    raise AttributeError(
        f"No se encontró una estrategia en '{strategy_path}'. Exporta STRATEGY_CLASS,"
        " una clase con on_bar/on_price o usa 'modulo:Clase'."
    )


# ---- reporter (equity/trades)
class Reporter:
    """
    Escribe equity.csv y trades.csv.

    Estandarizamos equity.csv a:
        t,price,qty,cash,equity
    con t = step + 1 (índice incremental de barra).
    """

    def __init__(self, equity_csv: str, trades_csv: str):
        self.equity_csv = equity_csv
        self.trades_csv = trades_csv

        # Cabecera SIEMPRE limpia al inicio del run (modo write)
        with open(self.equity_csv, "w", encoding="utf-8") as f:
            f.write("t,price,qty,cash,equity\n")

        # Cabecera mínima para trades (podremos ampliar más adelante)
        if not os.path.exists(self.trades_csv):
            with open(self.trades_csv, "w", encoding="utf-8") as f:
                f.write("t,side,qty,price,fees\n")

    # ---- helpers broker → valores
    @staticmethod
    def _get_cash(broker: Any) -> Optional[float]:
        # Prioridad: método -> atributo -> portfolio.atributo
        for attr in ("get_cash", "cash"):
            if hasattr(broker, attr):
                val = getattr(broker, attr)
                try:
                    return float(val() if callable(val) else val)
                except Exception:
                    pass
        portfolio = getattr(broker, "portfolio", None)
        if portfolio is not None:
            for attr in ("get_cash", "cash"):
                if hasattr(portfolio, attr):
                    val = getattr(portfolio, attr)
                    try:
                        return float(val() if callable(val) else val)
                    except Exception:
                        pass
        return None

    @staticmethod
    def _get_position_snapshot(broker: Any) -> Dict[str, Optional[float]]:
        """
        Devuelve {'size': qty, 'price': avg_price} con nombres tolerantes.
        """
        # Preferimos API moderna
        if hasattr(broker, "position_qty"):
            try:
                size = float(broker.position_qty())
            except Exception:
                size = None
        else:
            size = None

        if hasattr(broker, "position_avg_price"):
            try:
                avg = float(broker.position_avg_price())
            except Exception:
                avg = None
        else:
            avg = None

        # Aliases antiguos
        if size is None:
            for name in ("position_size", "pos_size", "size"):
                if hasattr(broker, name):
                    v = getattr(broker, name)
                    try:
                        size = float(v() if callable(v) else v)
                    except Exception:
                        size = None
                    break

        if avg is None:
            for name in ("position_price", "avg_price", "entry_price"):
                if hasattr(broker, name):
                    v = getattr(broker, name)
                    try:
                        avg = float(v() if callable(v) else v)
                    except Exception:
                        avg = None
                    break

        # Objeto posición
        pos = getattr(broker, "position", None)
        if pos is not None:
            if size is None:
                size = getattr(pos, "qty", getattr(pos, "size", None))
            if avg is None:
                avg = getattr(pos, "avg_price", None)

        return {"size": size, "price": avg}

    @staticmethod
    def _get_equity(broker: Any) -> Optional[float]:
        for attr in ("get_equity", "equity", "equity_now", "nav"):
            if hasattr(broker, attr):
                val = getattr(broker, attr)
                try:
                    return float(val() if callable(val) else val)
                except Exception:
                    pass
        return None

    # ---- escritura
    def on_bar(self, t: int, price: float, broker) -> None:
        """Escribe una línea en equity.csv con el formato estándar."""
        snap = self._get_position_snapshot(broker)
        qty = float(snap["size"] or 0.0)

        cash = self._get_cash(broker)
        cash = float(cash) if cash is not None else 0.0

        equity = self._get_equity(broker)
        if equity is None:
            # Fallback robusto: equity = cash + qty * price
            equity = cash + qty * float(price)

        with open(self.equity_csv, "a", encoding="utf-8") as f:
            f.write(f"{int(t)},{float(price):.8f},{qty:.12f},{cash:.8f},{float(equity):.8f}\n")

    def flush_trades(self, broker: Any):
        trades = getattr(broker, "trades", None)
        if trades is None:
            return
        with open(self.trades_csv, "w", encoding="utf-8") as f:
            f.write("t,side,qty,price,fees\n")
            for tr in _iter_trades(trades):
                f.write("{t},{side},{qty},{price},{fees}\n".format(**tr))


def _iter_trades(trades_obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(trades_obj, dict):
        trades_iter = trades_obj.values()
    else:
        trades_iter = trades_obj
    for tr in trades_iter:
        if isinstance(tr, dict):
            yield {
                "t": tr.get("t") or tr.get("timestamp") or tr.get("ts") or "",
                "side": tr.get("side") or tr.get("action") or "",
                "qty": tr.get("qty") or tr.get("quantity") or tr.get("size") or "",
                "price": tr.get("price") or tr.get("px") or "",
                "fees": tr.get("fees") or tr.get("fee") or "",
            }
        else:
            d = getattr(tr, "__dict__", {})
            yield {
                "t": d.get("t") or d.get("timestamp") or d.get("ts") or "",
                "side": d.get("side") or d.get("action") or "",
                "qty": d.get("qty") or d.get("quantity") or d.get("size") or "",
                "price": d.get("price") or d.get("px") or "",
                "fees": d.get("fees") or d.get("fee") or "",
            }


def _fmt(x: Any) -> str:
    try:
        if x is None:
            return ""
        return f"{float(x):.8f}"
    except Exception:
        return ""


# ---- helpers CSVFeed
def _make_csv_feed(path_csv: str, ts_field: str) -> Any:
    """
    Crea CSVFeed compatibilizando firmas:
      CSVFeed(path_csv, ts_field=...), CSVFeed(path_csv, ts_col=...),
      CSVFeed(path_csv, timestamp_field=...), CSVFeed(path_csv, ... sin ts),
      y posteriores setters/atributos.
    """
    ctor = CSVFeed
    try:
        sig = inspect.signature(ctor)
    except (TypeError, ValueError):
        sig = None

    # 1) probar kwargs comunes
    for kw in ("ts_field", "ts_col", "timestamp_field"):
        try:
            if sig and kw in sig.parameters:
                return ctor(path_csv, **{kw: ts_field}, validate=True)
        except TypeError:
            pass
        except Exception:
            pass

    # 2) probar firma (path_csv, ts_field) posicional
    try:
        if sig and len(sig.parameters) >= 2:
            return ctor(path_csv, ts_field, True)
    except Exception:
        pass

    # 3) probar solo path + validate
    try:
        if sig and "validate" in sig.parameters:
            feed = ctor(path_csv, validate=True)
        else:
            feed = ctor(path_csv)
    except Exception as e:
        print(f"[FATAL] No se pudo instanciar CSVFeed: {e}", file=sys.stderr)
        raise

    # 4) intentar configurar el ts_field después
    for setter in ("set_ts_field", "set_timestamp_field"):
        if hasattr(feed, setter):
            try:
                getattr(feed, setter)(ts_field)
                return feed
            except Exception:
                pass
    for attr in ("ts_field", "ts_col", "timestamp_field"):
        if hasattr(feed, attr):
            try:
                setattr(feed, attr, ts_field)
                return feed
            except Exception:
                pass

    return feed  # último recurso


# ---- helpers SimBroker (firma flexible)
def _make_sim_broker(cash: float, fees_bps: float, slip_bps: float, allow_short: bool) -> Any:
    """
    Intenta varias firmas conocidas de SimBroker y, si hace falta, configura
    por setters/atributos tras construir.
    Orden de intentos de construcción:
      1) SimBroker(cash, fees_bps, slip_bps, allow_short)
      2) SimBroker(fees_bps, slip_bps, allow_short)
      3) SimBroker(cash)
      4) SimBroker()
    Después, setea: cash, fees/slippage, allow_short por setters o atributos.
    """
    broker = None
    # 1) posicional completa
    try:
        broker = SimBroker(cash, fees_bps, slip_bps, allow_short)
    except TypeError:
        broker = None
    except Exception:
        broker = None

    # 2) (fees, slip, allow_short)
    if broker is None:
        try:
            broker = SimBroker(fees_bps, slip_bps, allow_short)
        except Exception:
            broker = None

    # 3) (cash)
    if broker is None:
        try:
            broker = SimBroker(cash)
        except Exception:
            broker = None

    # 4) ()
    if broker is None:
        broker = SimBroker()

    # ---- configurar CASH
    for method in ("set_cash", "set_balance", "deposit"):
        if hasattr(broker, method):
            try:
                getattr(broker, method)(float(cash))
                break
            except Exception:
                pass
    else:
        # portfolio.cash
        portfolio = getattr(broker, "portfolio", None)
        if portfolio is not None and hasattr(portfolio, "cash"):
            try:
                setattr(portfolio, "cash", float(cash))
            except Exception:
                pass
        elif hasattr(broker, "cash"):
            try:
                setattr(broker, "cash", float(cash))
            except Exception:
                pass

    # ---- configurar FEES
    for method in ("set_fees_bps", "set_fees"):
        if hasattr(broker, method):
            try:
                getattr(broker, method)(float(fees_bps))
                break
            except Exception:
                pass
    else:
        if hasattr(broker, "fees_bps"):
            try:
                setattr(broker, "fees_bps", float(fees_bps))
            except Exception:
                pass

    # ---- configurar SLIPPAGE
    for method in ("set_slip_bps", "set_slippage", "set_slippage_bps"):
        if hasattr(broker, method):
            try:
                getattr(broker, method)(float(slip_bps))
                break
            except Exception:
                pass
    else:
        for attr in ("slip_bps", "slippage_bps"):
            if hasattr(broker, attr):
                try:
                    setattr(broker, attr, float(slip_bps))
                    break
                except Exception:
                    pass

    # ---- configurar ALLOW_SHORT
    for method in ("set_allow_short",):
        if hasattr(broker, method):
            try:
                getattr(broker, method)(bool(allow_short))
                break
            except Exception:
                pass
    else:
        if hasattr(broker, "allow_short"):
            try:
                setattr(broker, "allow_short", bool(allow_short))
            except Exception:
                pass

    return broker


# ---- CLI
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Runner genérico (SimEngine) para estrategias arbitrarias."
    )
    p.add_argument("--run-dir", required=True, help="Directorio del run (debe contener data.csv).")
    p.add_argument(
        "--strategy", required=True, help="Ruta 'modulo:Clase' o solo 'modulo' para autodetección."
    )
    p.add_argument("--strategy-kwargs", default="{}", help="JSON con kwargs para la estrategia.")
    p.add_argument(
        "--ts-field", default="t_close", help="Nombre del campo timestamp en el CSV de barras."
    )
    p.add_argument("--price-field", default="close", help="Campo de precio a usar para PnL/equity.")
    p.add_argument(
        "--exit-at-end", type=int, default=0, help="1 para cerrar posiciones al final del run."
    )
    p.add_argument("--fees-bps", type=float, default=2.5, help="Comisiones (basis points).")
    p.add_argument("--slip-bps", type=float, default=1.0, help="Slippage (basis points).")
    p.add_argument("--cash", type=float, default=10_000.0, help="Efectivo inicial.")
    p.add_argument("--allow-short", type=int, default=0, help="1 para permitir cortos.")
    p.add_argument("--symbol", default="BTCUSDT", help="Símbolo informativo (solo logging).")
    p.add_argument(
        "--max-position-usd",
        type=float,
        default=10_000.0,
        help="Tamaño máximo de posición (notional USD).",
    )
    p.add_argument(
        "--min-notional-usd",
        type=float,
        default=5.0,
        help="Notional mínimo para ejecutar (evita polvo).",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    run_dir = args.run_dir
    data_csv = os.path.join(run_dir, "data.csv")
    equity_csv = os.path.join(run_dir, "equity.csv")
    trades_csv = os.path.join(run_dir, "trades.csv")

    if not os.path.exists(data_csv):
        print(
            f"[FATAL] No existe {data_csv}. Genera el run con tools/make_run_from_csv.py primero.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        strat_kwargs = json.loads(args.strategy_kwargs) if args.strategy_kwargs else {}
        if not isinstance(strat_kwargs, dict):
            raise ValueError("strategy-kwargs debe ser un JSON objeto (clave:valor)")
    except Exception as e:
        print(f"[FATAL] strategy-kwargs inválido: {e}", file=sys.stderr)
        sys.exit(2)

    StrategyThing = import_strategy_class(args.strategy)
    try:
        if inspect.isclass(StrategyThing):
            strategy_obj = _instantiate_class_with_kwargs(StrategyThing, strat_kwargs)
        elif callable(StrategyThing):
            strategy_obj = StrategyThing(**strat_kwargs)
        else:
            raise TypeError("StrategyThing no es clase ni función callable.")
    except Exception as e:
        print(f"[FATAL] No se pudo instanciar la estrategia: {e}", file=sys.stderr)
        sys.exit(3)

    cfg = EngineConfig(
        run_dir=run_dir,
        data_csv=data_csv,
        equity_csv=equity_csv,
        trades_csv=trades_csv,
        ts_field=args.ts_field,
        price_field=args.price_field,
        exit_at_end=bool(args.exit_at_end),
        symbol=args.symbol,
        fees_bps=float(args.fees_bps),
        slip_bps=float(args.slip_bps),
        cash=float(args.cash),
        allow_short=bool(args.allow_short),
        max_position_usd=float(args.max_position_usd),
        min_notional_usd=float(args.min_notional_usd),
    )

    # --- Orquestación con el CoreSimEngine desacoplado ---
    feed = _make_csv_feed(data_csv, args.ts_field)
    broker = _make_sim_broker(
        cash=float(args.cash),
        fees_bps=float(args.fees_bps),
        slip_bps=float(args.slip_bps),
        allow_short=bool(args.allow_short),
    )

    core_cfg = CoreEngineConfig(
        symbol=str(args.symbol),
        ts_field=str(args.ts_field),
        price_field=str(args.price_field),
        exit_at_end=bool(args.exit_at_end),
    )
    core_engine = CoreSimEngine(core_cfg)

    reporter = Reporter(equity_csv=equity_csv, trades_csv=trades_csv)

    # Contador de barras para resumen
    bars_counter = {"n": 0}

    def _on_equity(e):
        """Callback de actualización de equity por barra."""
        t = int(e["step"]) + 1  # índice incremental
        price = float(e["price"])
        reporter.on_bar(t, price, broker)
        bars_counter["n"] += 1

    def _on_log(e):
        lvl = e.get("level", "INFO")
        msg = e.get("msg", "")
        print(f"[{lvl}] step={e.get('step')} t={e.get('ts')} {msg}")

    core_engine.on("equity", _on_equity)
    core_engine.on("log", _on_log)

    print(
        f"[INFO] Runner → run_dir={cfg.run_dir}, symbol={cfg.symbol}, cash={cfg.cash:.2f}, "
        f"fees_bps={cfg.fees_bps:.4f}, slip_bps={cfg.slip_bps:.4f}, ts_field={cfg.ts_field}, "
        f"price_field={cfg.price_field}, exit_at_end={cfg.exit_at_end}"
    )

    t0 = time.time()
    core_engine.run(feed=feed, strategy=strategy_obj, broker=broker)
    dt = time.time() - t0

    reporter.flush_trades(broker)

    print(f"[INFO] Tiempo total: {dt:.3f}s | Bars procesadas: {bars_counter['n']}")
    print(f"[INFO] equity: {equity_csv}")
    print(f"[INFO] trades: {trades_csv}")


def _instantiate_class_with_kwargs(cls: Type, kwargs: Dict[str, Any]) -> Any:
    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError):
        return cls()
    try:
        sig.bind_partial(**kwargs)
        return cls(**kwargs)
    except TypeError:
        return cls()


if __name__ == "__main__":
    main()

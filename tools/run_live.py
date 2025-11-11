# tools/run_live.py
"""
Runner LIVE/SIM sencillo y robusto sobre CSV o (en futuro) binance.
- Soporta: --source csv, --csv <dir o fichero>, --run-dir, --symbol
- Flags de prueba: --enter-on-first-bar, --exit-at-end
- Costes: --fees-bps, --slip-bps
- Capital inicial: --cash (mapeado a SimBrokerConfig.starting_cash)

Dependencias del core (según tu repo actual):
- SimBroker / SimBrokerConfig en src.core.broker_sim
- Portfolio expuesto internamente por SimBroker (position_qty / cash / equity(mark_price=...))
- Estrategias: se intentan cargar vía get_strategy_class si existe; si no, fallback a los flags de prueba.
"""

import argparse
import csv
import json
import pathlib

# Se asume que PYTHONPATH incluye `${workspaceFolder}/src` (configurado en .vscode/settings.json)
# ---- Core broker/estrategias (según tu estructura actual) ----
from core.broker_sim import SimBroker, SimBrokerConfig  # noqa: E402
from core.strategy_runtime import (
    build_position_state,
)  # usa PositionState(entry_price=...)  # noqa: E402
from strategies.base import get_strategy_class  # registro de estrategias  # noqa: E402

# ------------------------------
# Utilidades de lectura de datos
# ------------------------------
TS_CANDIDATES = ["t", "ts", "timestamp", "time", "datetime", "ts_ms"]
PRICE_CANDS = ["close", "mid", "price", "last", "c"]


def _resolve_csv_path(csv_arg: str) -> pathlib.Path:
    p = pathlib.Path(csv_arg).expanduser().resolve()
    if p.is_dir():
        dc = p / "data.csv"
        if not dc.exists():
            raise FileNotFoundError(f"No existe {dc} (esperaba runs/<...>/data.csv)")
        return dc
    if p.is_file():
        return p
    raise FileNotFoundError(f"No encuentro CSV: {p}")


def _pick_col(header: list[str], wanted: str | None, cands: list[str]) -> str | None:
    if wanted and wanted in header:
        return wanted
    for k in cands:
        if k in header:
            return k
    return None


def _parse_ts(val: str) -> float:
    v = float(val)
    if v > 10_000_000_000:  # si viene en ms, pásalo a s
        v /= 1000.0
    return v


def load_bars_from_csv(
    data_csv: pathlib.Path,
    ts_col: str | None = None,
    price_col: str | None = None,
    synth_start_ms: int | None = None,
    synth_step_ms: int = 60_000,
) -> list[dict[str, float]]:
    """Lee barras del CSV (OHLC o al menos CLOSE). Si no hay ts, puede sintetizar."""
    bars: list[dict[str, float]] = []
    with data_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        if not header:
            raise ValueError("CSV sin cabecera.")

        ts_key = _pick_col(header, ts_col, TS_CANDIDATES)
        price_key = _pick_col(header, price_col, PRICE_CANDS)
        if price_key is None:
            raise ValueError(f"No encuentro columna de precio entre {PRICE_CANDS}. Header={header}")

        if ts_key is None and synth_start_ms is None:
            # Si no hay ts y no se pide sintetizar, genera contador 0,1,2... en segundos
            t = 0.0
            for row in reader:
                p = float(row[price_key])
                bars.append({"t": t, "close": p})
                t += synth_step_ms / 1000.0
        elif ts_key is None and synth_start_ms is not None:
            i = 0
            for row in reader:
                p = float(row[price_key])
                t = (synth_start_ms + i * synth_step_ms) / 1000.0
                bars.append({"t": t, "close": p})
                i += 1
        else:
            for row in reader:
                p = float(row[price_key])
                t = _parse_ts(row[ts_key])
                bars.append({"t": t, "close": p})

    if not bars:
        raise ValueError("CSV sin filas.")
    return bars


# --------------------------
# Métricas y guardado simple
# --------------------------
def save_equity_trades(
    run_dir: pathlib.Path,
    equity_rows: list[tuple[float, float, float, float, float]],
    trade_rows: list[tuple[float, str, float, float, float, float, str]],
) -> None:
    eqf = run_dir / "equity.csv"
    trf = run_dir / "trades.csv"
    with eqf.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "price", "qty", "cash", "equity"])
        w.writerows(equity_rows)
    with trf.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "side", "price", "qty", "cash", "equity", "reason"])
        w.writerows(trade_rows)


def save_summary(
    run_dir: pathlib.Path, equity_rows: list[tuple[float, float, float, float, float]]
) -> None:
    if not equity_rows:
        return
    eq0 = equity_rows[0][-1]
    eq1 = equity_rows[-1][-1]
    ret = (eq1 / eq0) - 1.0 if eq0 else 0.0
    summary = {
        "equity_init": eq0,
        "equity_final": eq1,
        "return_total": ret,
        "bars": len(equity_rows),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"summary.json escrito: {run_dir/'summary.json'}")


# --------------
# Estrategia real
# --------------
def maybe_load_strategy(name: str | None, params_json: str | None):
    if not name:
        return None
    try:
        cls = get_strategy_class(name)
    except Exception:
        return None
    try:
        params = json.loads(params_json) if params_json else {}
    except Exception:
        params = {}
    try:
        return cls(**params)
    except Exception:
        # Si la firma no coincide, devolvemos None y seguimos con flags simples
        return None


# ---- Ejecución principal ----
def run(args: argparse.Namespace) -> None:
    run_dir = pathlib.Path(args.run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    # Carga de datos
    if args.source.lower() != "csv":
        raise NotImplementedError("Por ahora este runner solo soporta --source csv")
    data_csv = _resolve_csv_path(args.csv)
    bars = load_bars_from_csv(
        data_csv,
        ts_col=args.ts_col,
        price_col=args.price_col,
        synth_start_ms=args.synthetic_base_ms,
        synth_step_ms=args.synthetic_ts_ms,
    )

    # Broker sim (mapeo correcto de --cash -> starting_cash)
    broker = SimBroker(
        SimBrokerConfig(
            starting_cash=args.cash,
            fees_bps=args.fees_bps,
            slip_bps=args.slip_bps,
        )
    )

    # Estrategia (si existe)
    strategy = maybe_load_strategy(args.strategy, args.params)

    equity_rows: list[tuple[float, float, float, float, float]] = []
    trade_rows: list[tuple[float, str, float, float, float, float, str]] = []

    # --- Loop principal ---
    n = len(bars)
    for i, b in enumerate(bars):
        t = float(b.get("t"))
        price = float(b.get("close"))

        # mark-to-market actual
        equity_now = broker.equity(mark_price=price)
        pos_qty = getattr(broker, "position_qty", 0.0)  # float
        cash_now = broker.cash  # propiedad

        # Guardado de equity por barra
        equity_rows.append((t, price, pos_qty, cash_now, equity_now))

        # Estado de posición para estrategia
        # avg_price: si el portfolio expone un promedio, úsalo; si no, usa price como fallback
        avg_price = price
        try:
            # intentar leer avg de coste si lo tienes en portfolio
            avg_price = float(broker._portfolio.avg_price)  # si existe en tu Portfolio
        except Exception:
            pass

        state = build_position_state(
            qty=pos_qty,
            avg_price=avg_price,  # build_position_state lo convierte en entry_price internamente (ajuste tuyo)
            equity=equity_now,
            cash=cash_now,
        )
        # Si tu build_position_state no añade has_position, mantenemos el flag aquí como apoyo
        if not hasattr(state, "has_position"):
            state.has_position = abs(pos_qty) > 0

        # Decisión
        order_side: str | None = None
        reason = ""
        size = 0.0

        first_bar = i == 0
        last_bar = i == n - 1

        if strategy is not None:
            # Intento muy conservador: si la estrategia tiene un método "decide" con firma simple
            try:
                act = strategy.decide(price=price, state=state)
                # se espera dict con side {"BUY","SELL","HOLD"} y size <= 1.0 (fracción)
                if isinstance(act, dict) and act.get("side") in ("BUY", "SELL"):
                    order_side = act["side"]
                    size = float(act.get("size", 1.0))
                    reason = act.get("reason", "strategy")
            except Exception:
                # si falla la API concreta, seguimos con flags simples
                pass

        # Fallback de prueba con flags simples
        if order_side is None:
            if first_bar and args.enter_on_first_bar and not state.has_position:
                order_side = "BUY"
                size = 1.0
                reason = "enter_on_first_bar"
            elif last_bar and args.exit_at_end and state.has_position:
                order_side = "SELL"
                size = 1.0
                reason = "exit_at_end"

        # Ejecutar orden si procede
        if order_side is not None and size > 0.0:
            qty = 0.0
            if order_side == "BUY":
                # comprar con todo el cash disponible (aprox) si size=1
                # puedes afinar aquí con un sizer real si lo deseas
                qty = (broker.cash / price) * size
            else:
                # vender posición actual (o su fracción)
                qty = abs(pos_qty) * size

            if qty > 0:
                broker.submit_order(
                    symbol=args.symbol,
                    side=order_side,
                    qty=qty,
                    price=price,
                    reason=reason,
                )
                # snapshot tras orden
                equity_post = broker.equity(mark_price=price)
                cash_post = broker.cash
                pos_post = broker.position_qty
                trade_rows.append((t, order_side, price, pos_post, cash_post, equity_post, reason))

    # Guardar resultados
    save_equity_trades(run_dir, equity_rows, trade_rows)
    save_summary(run_dir, equity_rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run live/sim desde CSV con broker sim.")
    p.add_argument("--run-dir", required=True, help="Carpeta donde guardar resultados")
    p.add_argument("--source", default="csv", choices=["csv"], help="Fuente de datos")
    p.add_argument(
        "--csv", required=True, help="Directorio del run (con data.csv) o un fichero CSV"
    )
    p.add_argument("--symbol", default="BTCUSDT", help="Símbolo")
    p.add_argument(
        "--strategy", default=None, help="Nombre de estrategia registrada (p. ej. momentum)"
    )
    p.add_argument("--params", default=None, help="JSON con parámetros de estrategia")
    p.add_argument("--fees-bps", type=float, default=0.0, help="Comisión (bps)")
    p.add_argument("--slip-bps", type=float, default=0.0, help="Slippage (bps)")
    p.add_argument("--cash", type=float, default=10_000.0, help="Capital inicial (USDT)")

    # Flags de prueba
    p.add_argument(
        "--enter-on-first-bar",
        action="store_true",
        help="Comprar en la primera barra si no hay posición",
    )
    p.add_argument("--exit-at-end", action="store_true", help="Vender al final si hay posición")

    # Opcionales para CSV
    p.add_argument(
        "--ts-col",
        default=None,
        help="Columna timestamp si existe (t/ts/timestamp/time/datetime/ts_ms)",
    )
    p.add_argument(
        "--price-col",
        default=None,
        help="Columna precio (por defecto intenta close/mid/price/last/c)",
    )
    p.add_argument(
        "--synthetic-base-ms", type=int, default=None, help="Epoch ms inicial si se sintetiza ts"
    )
    p.add_argument(
        "--synthetic-ts-ms",
        type=int,
        default=60_000,
        help="Paso en ms si se sintetiza ts (default 60s)",
    )

    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())

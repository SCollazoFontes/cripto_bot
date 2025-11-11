# tools/run_backtest.py
"""
Runner mínimo y auto-contenido para backtest "smoke test", tolerante a nombres de columnas
y capaz de sintetizar timestamps si el CSV no los trae.

- Detecta varias variantes de timestamp: t, ts, timestamp, time, datetime, ts_ms
- Detecta varias variantes de precio: close, mid, price, last, c
- Si no hay columna temporal, permite sintetizar con --synth-start-ms y --synth-step-ms
- Permite forzar columnas vía --ts-col y --price-col

Uso típico (para tu caso sin ts):
  PYTHONPATH="$(pwd)" python tools/run_backtest.py \
    --run-dir runs/XXXX \
    --enter-on-first-bar --exit-at-end \
    --price-col close \
    --synth-step-ms 60000   # 1 minuto por barra

Genera:
  <run_dir>/equity.csv  -> t, price, qty, cash, equity
  <run_dir>/trades.csv  -> t, side, price, qty, cash, equity, reason
"""

import argparse
import csv
import pathlib

TS_CANDIDATES = ["t", "ts", "timestamp", "time", "datetime", "ts_ms"]
PRICE_CANDIDATES = ["close", "mid", "price", "last", "c"]


def _pick_col(header: list[str], wanted: str | None, candidates: list[str]) -> str | None:
    """Devuelve el nombre de la columna si existe; si no hay candidata, None."""
    if wanted:
        return wanted if wanted in header else None
    for k in candidates:
        if k in header:
            return k
    return None


def _parse_ts(val: str) -> float:
    v = float(val)
    # Si viene en ms (muy grande), pásalo a segundos
    if v > 10_000_000_000:  # ~año 2286 en segundos
        v = v / 1000.0
    return v


def _read_bars(
    csv_path: pathlib.Path,
    ts_col: str | None,
    price_col: str | None,
    synth_start_ms: int,
    synth_step_ms: int,
) -> list[dict[str, float]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"No existe el CSV de datos: {csv_path}")

    bars: list[dict[str, float]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        if not header:
            raise ValueError("CSV sin cabecera.")

        ts_key = _pick_col(header, ts_col, TS_CANDIDATES)
        price_key = _pick_col(header, price_col, PRICE_CANDIDATES)
        if price_key is None:
            raise ValueError(
                f"No encuentro columna de precio. Header={header} | Prueba --price-col <nombre>"
            )

        if ts_key is None:
            # No hay timestamp en el CSV: sintetizamos (ms -> convertimos a segundos al guardar)
            i = 0
            for row in reader:
                p = float(row[price_key])
                t_sec = (synth_start_ms + i * synth_step_ms) / 1000.0
                bars.append({"t": t_sec, "close": p})
                i += 1
        else:
            for row in reader:
                t = _parse_ts(row[ts_key])
                p = float(row[price_key])
                bars.append({"t": t, "close": p})

    if not bars:
        raise ValueError("CSV sin filas.")
    return bars


def _write_equity(
    run_dir: pathlib.Path, rows: list[tuple[float, float, float, float, float]]
) -> None:
    out = run_dir / "equity.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "price", "qty", "cash", "equity"])
        w.writerows(rows)


def _write_trades(
    run_dir: pathlib.Path, rows: list[tuple[float, str, float, float, float, float, str]]
) -> None:
    out = run_dir / "trades.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "side", "price", "qty", "cash", "equity", "reason"])
        w.writerows(rows)


def run_backtest_smoketest(
    run_dir: str,
    enter_on_first_bar: bool,
    exit_at_end: bool,
    ts_col: str | None,
    price_col: str | None,
    synth_start_ms: int,
    synth_step_ms: int,
) -> None:
    run_dir_path = pathlib.Path(run_dir)
    data_csv = run_dir_path / "data.csv"
    bars = _read_bars(
        data_csv,
        ts_col=ts_col,
        price_col=price_col,
        synth_start_ms=synth_start_ms,
        synth_step_ms=synth_step_ms,
    )

    initial_cash = 10_000.0
    qty = 0.0
    cash = initial_cash

    equity_rows: list[tuple[float, float, float, float, float]] = []
    trade_rows: list[tuple[float, str, float, float, float, float, str]] = []

    # entrar en la primera vela si se pide
    if enter_on_first_bar:
        t0 = bars[0]["t"]
        p0 = bars[0]["close"]
        qty = cash / p0
        cash = 0.0
        equity = qty * p0 + cash
        trade_rows.append((t0, "BUY", p0, qty, cash, equity, "enter_on_first_bar"))

    # recorrer todas las velas registrando equity
    for b in bars:
        t = b["t"]
        p = b["close"]
        equity = qty * p + cash
        equity_rows.append((t, p, qty, cash, equity))

    # salir al final si se pide
    if exit_at_end and qty > 0:
        t_end = bars[-1]["t"]
        p_end = bars[-1]["close"]
        cash = qty * p_end
        qty = 0.0
        equity = cash
        trade_rows.append((t_end, "SELL", p_end, 0.0, cash, equity, "exit_at_end"))
        equity_rows.append((t_end, p_end, qty, cash, cash))

    _write_equity(run_dir_path, equity_rows)
    _write_trades(run_dir_path, trade_rows)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backtest mínimo (smoke test) tolerante y con timestamps sintéticos."
    )
    ap.add_argument("--run-dir", required=True, help="Carpeta del run (contiene data.csv)")
    ap.add_argument(
        "--enter-on-first-bar", action="store_true", help="Compra al inicio con todo el cash"
    )
    ap.add_argument("--exit-at-end", action="store_true", help="Vende al final toda la posición")
    ap.add_argument(
        "--ts-col",
        help="Nombre de la columna timestamp si existe (t/ts/timestamp/time/datetime/ts_ms)",
    )
    ap.add_argument(
        "--price-col",
        help="Nombre de la columna de precio (por defecto intenta close/mid/price/last/c)",
    )
    ap.add_argument(
        "--synth-start-ms",
        type=int,
        default=0,
        help="Epoch ms inicial si se sintetiza ts (default: 0)",
    )
    ap.add_argument(
        "--synth-step-ms",
        type=int,
        default=60000,
        help="Paso en ms entre velas si se sintetiza ts (default: 60000 = 1m)",
    )
    args = ap.parse_args()

    run_backtest_smoketest(
        run_dir=args.run_dir,
        enter_on_first_bar=bool(args.enter_on_first_bar),
        exit_at_end=bool(args.exit_at_end),
        ts_col=args.ts_col,
        price_col=args.price_col,
        synth_start_ms=args.synth_start_ms,
        synth_step_ms=args.synth_step_ms,
    )


if __name__ == "__main__":
    main()

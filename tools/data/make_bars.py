# tools/make_bars_v2.py
"""
Construcción de velas (v2) a partir de un CSV de captura de ticks (bookTicker).
- Entrada: CSV con columnas: ts,symbol,bid,ask[,mid]
- Salida:  uno o varios CSV con barras temporales (1s, 1m, ...)

Notas:
- No redondeamos la aritmética: trabajamos con floats y solo *formateamos* al escribir.
- Usa por defecto el precio 'mid' (si no existe, lo calcula como (bid+ask)/2).

Ejemplo:
PYTHONPATH="$(pwd)" python tools/make_bars_v2.py \
  --capture reports/diag/capture_BTCUSDT_YYYYMMDD_HHMMSS.csv \
  --symbol BTCUSDT --timeframes 1 60 --price-field mid
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from data.bars import build_time_bars


def _read_capture(path: Path, price_field: str = "mid") -> list[dict[str, Any]]:
    """
    Lee el CSV de captura y devuelve filas tipo:
    {'ts': float, 'bid': float, 'ask': float, 'mid': float}
    Si no hay columna 'mid' y price_field == 'mid', lo calcula.
    """
    rows: list[dict[str, Any]] = []
    with path.open("r", newline="") as f:
        r = csv.DictReader(f)
        # Normalizamos cabeceras esperadas
        has_bid = "bid" in r.fieldnames or "bidPrice" in r.fieldnames
        has_ask = "ask" in r.fieldnames or "askPrice" in r.fieldnames
        has_mid = "mid" in r.fieldnames

        for line in r:
            try:
                ts = float(line.get("ts"))
            except Exception:
                # Si viene en ms por error, lo convertimos a s
                try:
                    ts_ms = float(line.get("ts"))
                    ts = ts_ms / 1000.0
                except Exception:
                    continue

            # Lee bid/ask tolerando nombres alternativos
            def _get(name: str, alt: str, line=line):
                v = line.get(name)
                if v is None:
                    v = line.get(alt)
                return v

            bid_s = _get("bid", "bidPrice") if has_bid else None
            ask_s = _get("ask", "askPrice") if has_ask else None
            mid_s = line.get("mid") if has_mid else None

            bid = float(bid_s) if bid_s not in (None, "") else None
            ask = float(ask_s) if ask_s not in (None, "") else None

            if mid_s not in (None, ""):
                mid = float(mid_s)
            else:
                mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else None

            row: dict[str, Any] = {"ts": ts}
            if bid is not None:
                row["bid"] = bid
            if ask is not None:
                row["ask"] = ask
            if mid is not None:
                row["mid"] = mid

            # Si el price_field es 'mid' y no podemos obtenerlo, saltamos la fila
            if price_field == "mid" and ("mid" not in row):
                continue

            rows.append(row)

    return rows


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def _fmt_price(x: float) -> str:
    # Mantenemos 8 decimales (como venías viendo en tus CSVs de bars_v1)
    return f"{x:.8f}"


def _write_bars_csv(out_path: Path, symbol: str, bars) -> dict[str, int]:
    """
    Escribe CSV con cabecera: start_ts,end_ts,symbol,open,high,low,close,n_ticks
    Devuelve un pequeño resumen: recuento y posibles anomalías.
    """
    _ensure_dir(out_path)
    ohlc_bad = 0
    zero_ticks = 0

    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_ts", "end_ts", "symbol", "open", "high", "low", "close", "n_ticks"])
        for b in bars:
            # contadores de calidad
            if any(v is None for v in (b.open, b.high, b.low, b.close)):
                ohlc_bad += 1
            if b.n_ticks <= 0:
                zero_ticks += 1

            w.writerow(
                [
                    int(b.start_ts),
                    int(b.end_ts),
                    symbol,
                    _fmt_price(float(b.open)),
                    _fmt_price(float(b.high)),
                    _fmt_price(float(b.low)),
                    _fmt_price(float(b.close)),
                    int(b.n_ticks),
                ]
            )

    return {"count": len(bars), "ohlc_bad": ohlc_bad, "zero_ticks": zero_ticks}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Construye barras temporales (v2) desde una captura de bookTicker."
    )
    ap.add_argument(
        "--capture", required=True, help="Ruta al CSV de captura (ts,symbol,bid,ask[,mid])."
    )
    ap.add_argument(
        "--symbol", required=False, default="BTCUSDT", help="Símbolo a etiquetar en la salida."
    )
    ap.add_argument(
        "--timeframes",
        nargs="+",
        type=int,
        default=[1, 60],
        help="Lista de timeframes en segundos (p.ej. 1 60 300).",
    )
    ap.add_argument(
        "--price-field",
        default="mid",
        help="Campo de precio a usar ('mid' por defecto; también puede ser 'close' si aportas ese campo).",
    )
    ap.add_argument(
        "--out-prefix",
        default="reports/diag/bars_v2",
        help="Prefijo de salida (se generará <prefijo>_<timeframe>.csv).",
    )

    args = ap.parse_args()

    capture_path = Path(args.capture)
    if not capture_path.exists():
        raise SystemExit(f"❌ No existe el fichero de captura: {capture_path}")

    rows = _read_capture(capture_path, price_field=args.price_field)
    if not rows:
        raise SystemExit("❌ La captura no tiene filas válidas (¿faltan columnas ts/bid/ask/mid?).")

    print("=== Resumen entrada ===")
    print(f"Fichero: {capture_path}")
    print(f"Filas válidas: {len(rows)}")
    print(f"Symbol: {args.symbol}")
    print(f"Price field: {args.price_field}")
    print()

    for tf in args.timeframes:
        bars = build_time_bars(
            rows, timeframe_sec=tf, symbol=args.symbol, price_field=args.price_field
        )
        out_path = Path(f"{args.out_prefix}_{tf if tf != 60 else '1m' if tf==60 else tf}.csv")
        # si tf==60, usamos sufijo '1m' para comodidad visual
        if tf == 60:
            out_path = Path(f"{args.out_prefix}_1m.csv")
        summary = _write_bars_csv(out_path, args.symbol, bars)
        print(
            f"✅ Generadas {summary['count']} velas ({tf if tf != 60 else '1m'}) -> {out_path}  "
            f"| ohlc_bad={summary['ohlc_bad']} zero_ticks={summary['zero_ticks']}"
        )


if __name__ == "__main__":
    main()

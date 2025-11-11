# tools/validate_bars_v2_vs_klines.py
"""
Valida barras v2 (1m) contra klines oficiales de Binance testnet.

Limitación: Binance no expone klines de 1s por REST, así que validamos 1m.
Si pasas un CSV de 1s, este script agrupará por minuto tomando:
 open = open del primer segundo del minuto
 high = max de highs del minuto
 low  = min de lows del minuto
 close= close del último segundo del minuto

Salida:
- reports/diag/compare_v2_vs_klines.csv con filas:
    minute,start_ts,our_open,our_high,our_low,our_close,
    bin_open,bin_high,bin_low,bin_close
- Métricas agregadas por consola.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
from typing import Any
import urllib.parse
import urllib.request


def _read_bars(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", newline="") as f:
        r = csv.DictReader(f)
        needed = {"start_ts", "end_ts", "open", "high", "low", "close"}
        if not needed.issubset(set(r.fieldnames or [])):
            raise SystemExit(f"❌ El CSV no tiene las columnas requeridas: {sorted(list(needed))}")
        for line in r:
            rows.append(
                {
                    "start_ts": float(line["start_ts"]),
                    "end_ts": float(line["end_ts"]),
                    "open": float(line["open"]),
                    "high": float(line["high"]),
                    "low": float(line["low"]),
                    "close": float(line["close"]),
                }
            )
    return rows


def _group_1s_to_1m(bars_1s: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa barras 1s a 1m (OHLC clásico)."""
    if not bars_1s:
        return []

    # ordena por start_ts por si acaso
    bars_1s = sorted(bars_1s, key=lambda x: x["start_ts"])
    out: list[dict[str, Any]] = []

    cur_minute: int | None = None
    bucket: list[dict[str, Any]] = []

    def flush_bucket(bkt: list[dict[str, Any]], minute_start: int):
        if not bkt:
            return
        o = bkt[0]["open"]
        h = max(x["high"] for x in bkt)
        low = min(x["low"] for x in bkt)
        c = bkt[-1]["close"]
        out.append(
            {
                "start_ts": float(minute_start),
                "end_ts": float(minute_start + 59),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
            }
        )

    for b in bars_1s:
        minute = int(b["start_ts"] // 60 * 60)  # inicio de minuto en epoch(s)
        if cur_minute is None:
            cur_minute = minute
        if minute != cur_minute:
            flush_bucket(bucket, cur_minute)
            bucket = []
            cur_minute = minute
        bucket.append(b)

    flush_bucket(bucket, cur_minute)
    return out


def _http_json(url: str, timeout: float = 7.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_binance_klines(
    base: str, symbol: str, interval: str, start_ts: int, end_ts: int
) -> dict[int, tuple[float, float, float, float]]:
    """
    Descarga klines de Binance testnet (interval p.ej. '1m') entre start_ts y end_ts (epoch seconds).
    Devuelve dict de minute_start -> (open,high,low,close)
    """
    # Binance espera milisegundos
    start_ms = start_ts * 1000
    end_ms = (end_ts + 59) * 1000  # incluimos el minuto final completo
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
    )
    url = f"{base}/api/v3/klines?{params}"
    data = _http_json(url)

    out: dict[int, tuple[float, float, float, float]] = {}
    for k in data:
        # Formato: [ open_time, open, high, low, close, volume, close_time, ...]
        open_time_ms = int(k[0])
        minute_start = open_time_ms // 1000  # epoch seconds alineado a minuto
    o = float(k[1])
    h = float(k[2])
    low = float(k[3])
    c = float(k[4])
    out[int(minute_start)] = (o, h, low, c)
    return out


def _compute_metrics(pairs: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Devuelve (abs_mean, abs_max, rel_mean, rel_max) ignorando divisiones por cero."""
    if not pairs:
        return (math.nan, math.nan, math.nan, math.nan)
    abs_errs = [abs(a - b) for a, b in pairs]
    rel_errs = []
    for a, b in pairs:
        denom = abs(b) if b != 0 else (abs(a) if a != 0 else 1.0)
        rel_errs.append(abs(a - b) / denom)
    return (
        statistics.mean(abs_errs),
        max(abs_errs),
        statistics.mean(rel_errs),
        max(rel_errs),
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Valida barras v2 contra klines 1m de Binance testnet."
    )
    ap.add_argument("--bars", required=True, help="CSV de barras v2 (1s o 1m).")
    ap.add_argument("--symbol", default="BTCUSDT", help="Símbolo (ej. BTCUSDT).")
    ap.add_argument(
        "--base", default="https://testnet.binance.vision", help="Base URL de Binance testnet."
    )
    args = ap.parse_args()

    bars_path = Path(args.bars)
    if not bars_path.exists():
        raise SystemExit(f"❌ No existe el fichero de barras: {bars_path}")

    bars_in = _read_bars(bars_path)

    # Detectamos si son 1s o 1m por el rango de end-start:
    gran = int(round(bars_in[0]["end_ts"] - bars_in[0]["start_ts"]))
    if gran <= 1:
        # 1s -> agregamos a 1m
        ours_1m = _group_1s_to_1m(bars_in)
        print(f"Detectado CSV 1s -> agregado internamente a 1m: {len(ours_1m)} minutos.")
    else:
        # asumimos 1m
        ours_1m = bars_in
        print(f"Detectado CSV 1m: {len(ours_1m)} minutos.")

    if not ours_1m:
        raise SystemExit("❌ No hay barras que validar.")

    start_ts = int(ours_1m[0]["start_ts"])
    end_ts = int(ours_1m[-1]["start_ts"])

    # Descargamos klines 1m de Binance
    kl = _fetch_binance_klines(args.base, args.symbol, "1m", start_ts, end_ts)

    # Emparejamos por minuto
    pairs_open: list[tuple[float, float]] = []
    pairs_high: list[tuple[float, float]] = []
    pairs_low: list[tuple[float, float]] = []
    pairs_close: list[tuple[float, float]] = []

    out_rows: list[list[Any]] = [
        [
            "minute",
            "start_ts",
            "our_open",
            "our_high",
            "our_low",
            "our_close",
            "bin_open",
            "bin_high",
            "bin_low",
            "bin_close",
        ]
    ]

    missing = 0
    for b in ours_1m:
        minute = int(b["start_ts"])
        ref = kl.get(minute)
        if not ref:
            missing += 1
            continue
        o, h, low, c = b["open"], b["high"], b["low"], b["close"]
        bo, bh, bl, bc = ref
        pairs_open.append((o, bo))
        pairs_high.append((h, bh))
        pairs_low.append((low, bl))
        pairs_close.append((c, bc))
        out_rows.append([minute, minute, o, h, low, c, bo, bh, bl, bc])

    # Guardamos detalle
    out_csv = Path("reports/diag/compare_v2_vs_klines.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerows(out_rows)

    # Métricas
    ao_mean, ao_max, ro_mean, ro_max = _compute_metrics(pairs_open)
    ah_mean, ah_max, rh_mean, rh_max = _compute_metrics(pairs_high)
    al_mean, al_max, rl_mean, rl_max = _compute_metrics(pairs_low)
    ac_mean, ac_max, rc_mean, rc_max = _compute_metrics(pairs_close)

    print("✅ Comparación completada")
    print(f"    minutos comparados: {len(pairs_open)}, sin kline: {missing}")
    print(
        f"    |abs| mean  -> open={ao_mean:.6f}  high={ah_mean:.6f}  low={al_mean:.6f}  close={ac_mean:.6f}"
    )
    print(
        f"    |abs|  max  -> open={ao_max:.6f}  high={ah_max:.6f}  low={al_max:.6f}  close={ac_max:.6f}"
    )
    print(
        f"    |rel| mean  -> open={ro_mean:.8f} high={rh_mean:.8f} low={rl_mean:.8f} close={rc_mean:.8f}"
    )
    print(
        f"    |rel|  max  -> open={ro_max:.8f} high={rh_max:.8f} low={rl_max:.8f} close={rc_max:.8f}"
    )
    print(f"➡️  Detalle en: {out_csv}")


if __name__ == "__main__":
    main()

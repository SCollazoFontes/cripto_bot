# tools/make_run_from_csv.py
"""
Crea un directorio de run reproducible a partir de un CSV (o JSONL/Parquet).

Salida:
runs/<ts>/
  ├─ manifest.json     # metadatos (fuente, hash, filas, columnas, cmd, etc.)
  ├─ validation.json   # salida completa de src.data.validate.validate(...)
  ├─ quality.json      # métricas rápidas (rango temporal, nulos, quantiles, etc.)
  ├─ data.csv          # copia del dataset usado para el run
  ├─ equity.csv        # placeholder (cabecera lista)
  └─ trades.csv        # placeholder (cabecera lista)

Uso:
$ python -m tools.make_run_from_csv --file data/bars_live/xxx.csv
$ python -m tools.make_run_from_csv --file data/bars_live/xxx.csv --strict
$ python -m tools.make_run_from_csv --file data/bars_live/xxx.csv --out runs

Notas:
- Si --strict y la validación no pasa (ok=False), exit code 1.
- Si el archivo no es CSV, se intentará leer como JSONL/Parquet y se volcará a CSV.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
from typing import Dict, Optional, Tuple

import pandas as pd

from src.data.validate import validate


def _read_any_to_df(path: str) -> pd.DataFrame:
    if path.endswith(".csv"):
        return pd.read_csv(path)
    if path.endswith(".jsonl"):
        return pd.read_json(path, lines=True)
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    # fallback: asumir CSV
    return pd.read_csv(path)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _quantiles(s: Optional[pd.Series]):
    if s is None:
        return {}
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return {}
    out = {"min": float(s.min()), "max": float(s.max())}
    for q in (0.5, 0.9, 0.95, 0.99):
        out[f"p{int(q * 100):02d}"] = float(s.quantile(q))
    return out


def _estimate_time_range_and_rate(df: pd.DataFrame) -> Tuple[Optional[int], Optional[float]]:
    to = df.get("t_open")
    tc = df.get("t_close")

    def _to_ms(x: pd.Series) -> Optional[pd.Series]:
        if x is None:
            return None
        if pd.api.types.is_numeric_dtype(x):
            return pd.to_numeric(x, errors="coerce")
        dt = pd.to_datetime(x, utc=False, errors="coerce")
        if dt.notna().any():
            return (dt.view("int64") / 1_000_000).astype("float64")
        return None

    to_ms = _to_ms(to) if to is not None else None
    tc_ms = _to_ms(tc) if tc is not None else None
    if to_ms is not None and tc_ms is not None and to_ms.notna().any() and tc_ms.notna().any():
        rng = float(tc_ms.max() - to_ms.min())
        if pd.isna(rng):
            return None, None
        rng_int = int(rng)
        rate = float(len(df) / max(1e-9, rng / 1000.0)) if rng > 0 else None
        return rng_int, rate
    return None, None


def _quality(df: pd.DataFrame) -> Dict:
    q = {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "nulls": {c: int(df[c].isna().sum()) for c in df.columns},
    }
    tr_ms, rate = _estimate_time_range_and_rate(df)
    q["t_range_ms"] = tr_ms
    q["bars_per_sec"] = rate
    if "duration_ms" in df:
        q["duration_ms"] = _quantiles(df["duration_ms"])
    if "gap_ms" in df:
        q["gap_ms"] = _quantiles(df["gap_ms"])
    for cand in ("overshoot_pct", "overshoot"):
        if cand in df:
            q["overshoot"] = _quantiles(df[cand])
            break
    return q


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Ruta a CSV/JSONL/Parquet con micro-barras.")
    ap.add_argument("--out", default="runs", help="Carpeta raíz de runs (por defecto 'runs').")
    ap.add_argument(
        "--require",
        nargs="*",
        default=["t_open", "t_close", "open", "high", "low", "close", "volume"],
    )
    ap.add_argument("--allow-nan", nargs="*", default=["gap_ms"])
    ap.add_argument("--strict", action="store_true", help="Exit 1 si la validación no pasa.")
    args = ap.parse_args(argv)

    src_path = args.file
    if not os.path.isfile(src_path):
        print(f"❌ No existe el archivo: {src_path}", file=sys.stderr)
        return 2

    # 1) Carga y valida
    try:
        df = _read_any_to_df(src_path)
    except Exception as e:
        print(f"❌ Error leyendo {src_path}: {e}", file=sys.stderr)
        return 2

    val = validate(df, require_columns=args.require, allow_nan_in=args.allow_nan)

    # 2) Carpetas
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = os.path.join(args.out, ts)
    os.makedirs(run_dir, exist_ok=False)

    # 3) Volcado de datos: siempre a CSV para homogeneidad
    data_csv = os.path.join(run_dir, "data.csv")
    try:
        df.to_csv(data_csv, index=False)
    except Exception as e:
        print(f"❌ Error guardando data.csv: {e}", file=sys.stderr)
        return 3

    # 4) Archivos JSON (manifest, validation, quality)
    manifest = {
        "created_at": ts,
        "source_path": os.path.abspath(src_path),
        "source_sha256": _sha256_file(src_path),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "cmd": " ".join(sys.argv),
        "validator_ok": bool(val.get("ok", False)),
        "issues_count": int(len(val.get("issues", []))),
        "project": "cripto_bot",
        "version": 1,
    }
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    with open(os.path.join(run_dir, "validation.json"), "w", encoding="utf-8") as f:
        json.dump(val, f, ensure_ascii=False, indent=2)

    qual = _quality(df)
    with open(os.path.join(run_dir, "quality.json"), "w", encoding="utf-8") as f:
        json.dump(qual, f, ensure_ascii=False, indent=2)

    # 5) Placeholders equity/trades
    with open(os.path.join(run_dir, "equity.csv"), "w", encoding="utf-8") as f:
        f.write("ts,step,equity,drawdown,position,price\n")
    with open(os.path.join(run_dir, "trades.csv"), "w", encoding="utf-8") as f:
        f.write("ts,side,qty,price,fees,slip,realized_pnl,order_id\n")

    print(f"✅ Run creado en: {run_dir}")
    if args.strict and not val.get("ok", False):
        print("❌ Validación no superada (strict).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

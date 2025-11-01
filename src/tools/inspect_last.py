# scr/tools/inspect_last.py

"""
Herramienta de inspección rápida de la *última* sesión de micro-barras.

Uso típico
---------
$ python -m tools.inspect_last
$ python -m tools.inspect_last --dir data/bars_live \
    --pattern "btcusdt_volume_qty_*.csv"
$ python -m tools.inspect_last --as-json
$ python -m tools.inspect_last --strict   # devuelve código 1 si algún check falla

Qué hace
-------
1) Detecta el archivo más reciente en una carpeta (por mtime) con un patrón opcional.
2) Muestra head/tail (primeras/últimas 3 filas) para inspección rápida.
3) Calcula métricas de calidad:
   - nº de filas, rango temporal, barras/s aprox,
   - distribución de duration_ms y gap_ms,
   - nulos por columna,
   - overshoot (si existe): min/mediana/p95/max.
4) Valida invariantes básicos:
   - OHLC: low ≤ open,close,high ≤ high
   - t_close ≥ t_open, duration_ms ≥ 0, gap_ms ≥ 0
   - target == limit (si existen), overshoot ≥ -tolerancia

Salida
------
- Texto legible por defecto.
- JSON con --as-json (para integraciones/paneles).
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ===========================================================================
# Descubrimiento de archivos
# ===========================================================================


def _find_latest_file(directory: str, pattern: Optional[str] = None) -> str:
    """Busca el archivo más reciente por mtime dentro de un directorio."""
    directory = directory or "data/bars_live"
    paths: List[str] = []  # única definición válida

    if pattern:
        paths = glob.glob(os.path.join(directory, pattern))
    else:
        for ext in ("*.csv", "*.jsonl", "*.parquet"):
            paths += glob.glob(os.path.join(directory, ext))

    if not paths:
        msg = f"No se encontraron archivos en {directory!r} con patrón {pattern!r}"
        raise FileNotFoundError(msg)

    latest = max(paths, key=lambda p: os.path.getmtime(p))
    return latest


def _read_any(path: str) -> pd.DataFrame:
    """Lee CSV, JSONL o Parquet en DataFrame, detectando por extensión."""
    if path.endswith(".csv"):
        return pd.read_csv(path)
    if path.endswith(".jsonl"):
        return pd.read_json(path, lines=True)
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    raise ValueError(f"Extensión no soportada: {path}")


# ===========================================================================
# Métricas y estadísticos
# ===========================================================================


def _quantiles(
    s: Optional[pd.Series],
    qs: Tuple[float, ...] = (0.50, 0.90, 0.95, 0.99),
) -> Dict[str, float]:
    """Calcula cuantiles y extremos; devuelve {} si la serie es nula o vacía."""
    if s is None or len(s) == 0:
        return {}
    out: Dict[str, float] = {}
    for q in qs:
        out[f"p{int(q * 100):02d}"] = float(s.quantile(q))
    out["min"] = float(s.min())
    out["max"] = float(s.max())
    return out


def _has_cols(df: pd.DataFrame, cols: List[str]) -> bool:
    """Comprueba si el DataFrame contiene todas las columnas dadas."""
    return set(cols).issubset(df.columns)


def _estimate_time_range_and_rate(df: pd.DataFrame) -> Tuple[Optional[int], Optional[float]]:
    """Estima rango temporal (ms) y barras/s a partir de t_open/t_close."""
    t_open = df.get("t_open")
    t_close = df.get("t_close")

    if (
        t_open is not None
        and t_close is not None
        and not t_open.isna().all()
        and not t_close.isna().all()
    ):
        rng = int(t_close.max() - t_open.min())
        rate = float(len(df) / max(1e-9, rng / 1000.0)) if rng > 0 else None
        return rng, rate

    # Fallback: columnas start_time / end_time (datetime)
    for col in ("start_time", "end_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    if "start_time" in df and "end_time" in df and not df["start_time"].isna().all():
        rng = int((df["end_time"].max() - df["start_time"].min()).total_seconds() * 1000)
        rate = float(len(df) / max(1e-9, rng / 1000.0)) if rng > 0 else None
        return rng, rate

    return None, None


# ===========================================================================
# Objeto resumen y función principal
# ===========================================================================


@dataclass
class Summary:
    """Resumen estructurado de un archivo de micro-barras."""

    path: str
    rows: int
    columns: List[str]
    t_range_ms: Optional[int]
    bars_per_sec: Optional[float]
    duration_ms_stats: Dict[str, float]
    gap_ms_stats: Dict[str, float]
    overshoot_stats: Dict[str, float]
    nulls_pct: Dict[str, float]
    checks_ok: bool
    failed_checks: List[str]
    head: List[Dict[str, Any]]
    tail: List[Dict[str, Any]]


def _compute_summary(df: pd.DataFrame, path: str) -> Summary:
    """Calcula métricas, checks y resumen general del archivo."""
    rows = len(df)
    cols = list(df.columns)

    # Estadísticos de duración y gap
    duration_stats: Dict[str, float] = _quantiles(df["duration_ms"]) if "duration_ms" in df else {}
    gap_stats: Dict[str, float] = {}
    if "gap_ms" in df:
        gm = df["gap_ms"].dropna()
        gap_stats = _quantiles(gm) if len(gm) else {}

    # Overshoot (si existe)
    overshoot_stats: Dict[str, float] = (
        _quantiles(df["overshoot_pct"]) if "overshoot_pct" in df else {}
    )

    # Porcentaje de nulos
    nulls_pct = {c: float(df[c].isna().mean() * 100.0) for c in df.columns}

    # Rango temporal y ritmo de barras
    t_range_ms, bars_per_sec = _estimate_time_range_and_rate(df)

    # Primeras/últimas filas (para diagnóstico)
    head = df.head(3).to_dict(orient="records")
    tail = df.tail(3).to_dict(orient="records")

    # Checks de coherencia
    checks_ok = True
    failed: List[str] = []

    # a) OHLC
    if _has_cols(df, ["low", "open", "close", "high"]):
        ok_low = df[["open", "close", "high"]].ge(df["low"], axis=0).all().all()
        ok_high = df[["open", "close", "low"]].le(df["high"], axis=0).all().all()
        if not (ok_low and ok_high):
            checks_ok = False
            failed.append("OHLC bounds")

    # b) tiempos y continuidad
    if _has_cols(df, ["t_open", "t_close"]) and not (df["t_close"] >= df["t_open"]).all():
        checks_ok = False
        failed.append("t_close >= t_open")
    if "duration_ms" in df and not (df["duration_ms"] >= 0).all():
        checks_ok = False
        failed.append("duration_ms >= 0")
    if "gap_ms" in df:
        g = df["gap_ms"].dropna()
        if len(g) and not (g >= 0).all():
            checks_ok = False
            failed.append("gap_ms >= 0")

    # c) overshoot y target
    if _has_cols(df, ["target", "limit"]) and not (df["target"] == df["limit"]).all():
        checks_ok = False
        failed.append("target == limit")
    if "rule" in df and "overshoot" in df:
        is_volume = df["rule"].astype(str).str.contains("volume")
        if is_volume.any():
            if not (df.loc[is_volume, "overshoot"] >= -1e-12).all():
                checks_ok = False
                failed.append("overshoot >= 0 (volume)")

    return Summary(
        path=path,
        rows=rows,
        columns=cols,
        t_range_ms=t_range_ms,
        bars_per_sec=bars_per_sec,
        duration_ms_stats=duration_stats,
        gap_ms_stats=gap_stats,
        overshoot_stats=overshoot_stats,
        nulls_pct=nulls_pct,
        checks_ok=checks_ok,
        failed_checks=failed,
        head=head,
        tail=tail,
    )


# ===========================================================================
# CLI principal
# ===========================================================================


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: localiza el último archivo, genera resumen y valida checks."""
    parser = argparse.ArgumentParser(description="Inspección del último archivo de micro-barras.")
    parser.add_argument("--dir", default="data/bars_live", help="Carpeta donde buscar.")
    parser.add_argument(
        "--pattern", default=None, help='Patrón glob opcional (ej: "btcusdt_volume_qty_*.csv")'
    )
    parser.add_argument("--as-json", action="store_true", help="Salida en formato JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Código de salida 1 si algún check falla.",
    )
    args = parser.parse_args(argv)

    try:
        path = _find_latest_file(args.dir, args.pattern)
        df = _read_any(path)
    except Exception as e:
        print(f"❌ Error leyendo archivo: {e}", file=sys.stderr)
        return 2

    try:
        summary = _compute_summary(df, path)
    except Exception as e:
        print(f"❌ Error calculando resumen: {e}", file=sys.stderr)
        return 3

    if args.as_json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    else:
        # Formato legible en consola
        print(f"📄 Archivo: {summary.path}")
        print(f"🔢 Filas: {summary.rows}")
        print(f"🧱 Columnas: {', '.join(summary.columns)}")
        if summary.t_range_ms is not None:
            secs = summary.t_range_ms / 1000.0
            bps = summary.bars_per_sec or 0.0
            print(f"⏱️  Rango temporal: {secs:.3f}s | Barras/s ~ {bps:.2f}")
        if summary.duration_ms_stats:
            d = summary.duration_ms_stats
            print(
                f"⏳ duration_ms → min={d['min']:.1f} p50={d.get('p50', 0):.1f} "
                f"p95={d.get('p95', 0):.1f} max={d['max']:.1f}"
            )
        if summary.gap_ms_stats:
            g = summary.gap_ms_stats
            print(
                f"🪟 gap_ms      → min={g['min']:.1f} p50={g.get('p50', 0):.1f} "
                f"p95={g.get('p95', 0):.1f} max={g['max']:.1f}"
            )
        if summary.overshoot_stats:
            o = summary.overshoot_stats
            print(
                f"🎯 overshoot%  → min={o['min']:.4f} p50={o.get('p50', 0):.4f} "
                f"p95={o.get('p95', 0):.4f} max={o['max']:.4f}"
            )

        # Nulos relevantes
        bad_nulls = {k: v for k, v in summary.nulls_pct.items() if v > 0}
        if bad_nulls:
            print("⚠️  Nulos por columna (>0%):")
            for k, v in sorted(bad_nulls.items(), key=lambda kv: -kv[1]):
                print(f"   - {k}: {v:.1f}%")

        # Head/Tail compactos
        print("\n— HEAD —")
        print(pd.DataFrame(summary.head).to_string(index=False))
        print("\n— TAIL —")
        print(pd.DataFrame(summary.tail).to_string(index=False))

        # Checks finales
        if summary.checks_ok:
            print("\n✅ Checks OK")
        else:
            print("\n❌ Checks fallidos:", ", ".join(summary.failed_checks))

    if args.strict and not summary.checks_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

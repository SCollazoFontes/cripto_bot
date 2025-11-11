# tools/inspect_last.py
"""
Herramienta de inspección rápida de la *última* sesión de micro-barras.

Uso típico
---------
$ python -m tools.inspect_last
$ python -m tools.inspect_last --dir data/bars_live --pattern "btcusdt_volume_qty_*.csv"
$ python -m tools.inspect_last --as-json
$ python -m tools.inspect_last --strict   # exit(1) si el validador detecta errores

Qué hace
-------
1) Detecta el archivo más reciente (por mtime) con un patrón opcional.
2) Muestra head/tail (primeras/últimas 3 filas) para inspección rápida.
3) Calcula métricas de calidad:
   - nº de filas, rango temporal, barras/s aprox,
   - distribución de duration_ms y gap_ms,
   - nulos por columna,
   - overshoot (si existe): min/mediana/p95/max.
4) Ejecuta el validador genérico (src/data/validate.py):
   - OHLC: low ≤ open,close ≤ high y low ≤ high
   - t_close ≥ t_open, monotonicidad de t_open, no solapes, duration/gap consistentes
   - NaNs por columna (permitidos en allow-nan)
   - (opcional) columnas requeridas

Salida
------
- Texto legible por defecto (incluye resumen del validador).
- JSON con --as-json (incluye bloque "validation").
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import glob
import json
import os
import sys
from typing import Any

import pandas as pd

# Integración con el validador central
from data.validate import summarize_for_cli, validate

# ===========================================================================
# Descubrimiento y lectura de archivos
# ===========================================================================


def _find_latest_file(directory: str, pattern: str | None = None) -> str:
    """Busca el archivo más reciente por mtime dentro de un directorio."""
    directory = directory or "data/bars_live"
    paths: list[str] = []

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
    s: pd.Series | None,
    qs: tuple[float, ...] = (0.50, 0.90, 0.95, 0.99),
) -> dict[str, float]:
    """Calcula cuantiles y extremos; devuelve {} si la serie es nula o vacía."""
    if s is None or len(s) == 0:
        return {}
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return {}
    out: dict[str, float] = {"min": float(s.min()), "max": float(s.max())}
    for q in qs:
        out[f"p{int(q * 100):02d}"] = float(s.quantile(q))
    return out


def _has_cols(df: pd.DataFrame, cols: list[str]) -> bool:
    """Comprueba si el DataFrame contiene todas las columnas dadas."""
    return set(cols).issubset(df.columns)


def _estimate_time_range_and_rate(
    df: pd.DataFrame,
) -> tuple[int | None, float | None]:
    """
    Estima rango temporal (ms) y barras/s a partir de t_open/t_close.
    - Si t_open/t_close son numéricos, se asume milisegundos.
    - Si son datetime, se convierte a ms.
    """
    t_open = df.get("t_open")
    t_close = df.get("t_close")

    def _to_ms(x: pd.Series) -> pd.Series | None:
        if x is None:
            return None
        if pd.api.types.is_numeric_dtype(x):
            return pd.to_numeric(x, errors="coerce")
        # intento parseo datetime
        dt = pd.to_datetime(x, utc=False, errors="coerce")
        if dt.notna().any():
            return (dt.view("int64") / 1_000_000).astype("float64")
        return None

    to_ms = _to_ms(t_open)
    tc_ms = _to_ms(t_close)

    if to_ms is not None and tc_ms is not None and to_ms.notna().any() and tc_ms.notna().any():
        # Evita mezclar float y None en la misma variable (mypy)
        val = float(tc_ms.max() - to_ms.min())
        if not pd.notna(val):
            return None, None
        rng_int = int(val)
        rate = float(len(df) / max(1e-9, val / 1000.0)) if val > 0 else None
        return rng_int, rate

    # Fallback: columnas start_time / end_time (datetime)
    for col in ("start_time", "end_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    if "start_time" in df and "end_time" in df and df["start_time"].notna().any():
        rng = (df["end_time"].max() - df["start_time"].min()).total_seconds() * 1000.0
        rng_int = int(rng)
        rate = float(len(df) / max(1e-9, rng / 1000.0)) if rng > 0 else None
        return rng_int, rate

    return None, None


# ===========================================================================
# Resumen propio (métricas rápidas) + Validación centralizada
# ===========================================================================


@dataclass
class Summary:
    """Resumen estructurado de un archivo de micro-barras."""

    path: str
    rows: int
    columns: list[str]
    t_range_ms: int | None
    bars_per_sec: float | None
    duration_ms_stats: dict[str, float]
    gap_ms_stats: dict[str, float]
    overshoot_stats: dict[str, float]
    nulls_pct: dict[str, float]
    local_checks_ok: bool
    local_failed_checks: list[str]
    head: list[dict[str, Any]]
    tail: list[dict[str, Any]]
    # Añadimos el resultado del validador externo
    validation: dict[str, Any]


def _compute_local_summary(df: pd.DataFrame, path: str) -> tuple[Summary, list[str]]:
    """
    Calcula métricas rápidas y checks locales (no reemplaza al validador).
    Devuelve el Summary (sin validation todavía) y la lista de checks fallidos.
    """
    rows = len(df)
    cols = list(df.columns)

    # Estadísticos de duración y gap
    duration_stats: dict[str, float] = _quantiles(df["duration_ms"]) if "duration_ms" in df else {}
    gap_stats: dict[str, float] = _quantiles(df["gap_ms"].dropna()) if "gap_ms" in df else {}

    # Overshoot (si existe)
    overshoot_stats: dict[str, float] = {}
    for candidate in ("overshoot_pct", "overshoot"):
        if candidate in df:
            overshoot_stats = _quantiles(pd.to_numeric(df[candidate], errors="coerce"))
            break

    # Porcentaje de nulos
    nulls_pct = {c: float(df[c].isna().mean() * 100.0) for c in df.columns}

    # Rango temporal y ritmo de barras
    t_range_ms, bars_per_sec = _estimate_time_range_and_rate(df)

    # Head/Tail compactos
    head = df.head(3).to_dict(orient="records")
    tail = df.tail(3).to_dict(orient="records")

    # Checks locales (diagnóstico rápido)
    local_ok = True
    failed: list[str] = []

    if _has_cols(df, ["low", "open", "close", "high"]):
        ok_low = df[["open", "close", "high"]].ge(df["low"], axis=0).all().all()
        ok_high = df[["open", "close", "low"]].le(df["high"], axis=0).all().all()
        if not (ok_low and ok_high):
            local_ok = False
            failed.append("OHLC bounds")

    if _has_cols(df, ["t_open", "t_close"]):
        t_close_num = pd.to_numeric(df["t_close"], errors="coerce")
        t_open_num = pd.to_numeric(df["t_open"], errors="coerce")
        tc_ge_to = t_close_num >= t_open_num
        if not bool(tc_ge_to.fillna(True).all()):
            local_ok = False
            failed.append("t_close >= t_open (local)")

    if "duration_ms" in df:
        d = pd.to_numeric(df["duration_ms"], errors="coerce")
        if not bool((d >= 0).fillna(True).all()):
            local_ok = False
            failed.append("duration_ms >= 0 (local)")

    if "gap_ms" in df:
        g = pd.to_numeric(df["gap_ms"], errors="coerce").dropna()
        if len(g) and not (g >= 0).all():
            local_ok = False
            failed.append("gap_ms >= 0 (local)")

    summary = Summary(
        path=path,
        rows=rows,
        columns=cols,
        t_range_ms=t_range_ms,
        bars_per_sec=bars_per_sec,
        duration_ms_stats=duration_stats,
        gap_ms_stats=gap_stats,
        overshoot_stats=overshoot_stats,
        nulls_pct=nulls_pct,
        local_checks_ok=local_ok,
        local_failed_checks=failed,
        head=head,
        tail=tail,
        validation={},  # se rellena fuera
    )
    return summary, failed


# ===========================================================================
# CLI principal
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    """CLI: localiza el último archivo, genera resumen, valida y decide exit code."""
    parser = argparse.ArgumentParser(description="Inspección del último archivo de micro-barras.")
    parser.add_argument(
        "--dir",
        default="data/bars_live",
        help="Carpeta donde buscar.",
    )
    parser.add_argument(
        "--pattern",
        default=None,
        help=('Patrón glob opcional (p.ej. "btcusdt_volume_qty_*.csv")'),
    )
    parser.add_argument(
        "--as-json",
        action="store_true",
        help="Salida en formato JSON.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Devuelve exit(1) si el validador marca errores.",
    )
    parser.add_argument(
        "--require",
        nargs="*",
        default=[],
        help=("Columnas requeridas para validar (opcional)."),
    )
    parser.add_argument(
        "--allow-nan",
        nargs="*",
        default=["gap_ms"],
        help=("Columnas en las que se permiten NaNs."),
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        default=12,
        help=("Máximo de tipos de issues a listar en CLI."),
    )
    args = parser.parse_args(argv)

    try:
        path = _find_latest_file(args.dir, args.pattern)
        df = _read_any(path)
    except Exception as e:
        print(f"Error leyendo archivo: {e}", file=sys.stderr)
        return 2

    try:
        summary, _ = _compute_local_summary(df, path)
    except Exception as e:
        print(f"Error calculando métricas locales: {e}", file=sys.stderr)
        return 3

    # Validación centralizada
    try:
        validation = validate(
            df,
            require_columns=args.require,
            allow_nan_in=args.allow_nan,
        )
        summary.validation = validation
    except Exception as e:
        print(f"Error en validador: {e}", file=sys.stderr)
        return 4

    # Salidas
    if args.as_json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    else:
        # Formato legible en consola
        print(f"Archivo: {summary.path}")
        print(f"Filas: {summary.rows}")
        print(f"Columnas: {', '.join(summary.columns)}")
        if summary.t_range_ms is not None:
            secs = summary.t_range_ms / 1000.0
            bps = summary.bars_per_sec if summary.bars_per_sec is not None else 0.0
            print(f"Rango temporal: {secs:.3f}s | Barras/s ~ {bps:.2f}")
        if summary.duration_ms_stats:
            d = summary.duration_ms_stats
            p50 = d.get("p50", 0.0)
            p95 = d.get("p95", 0.0)
            print(
                f"duration_ms -> min={d['min']:.1f} p50={p50:.1f} p95={p95:.1f} max={d['max']:.1f}"
            )
        if summary.gap_ms_stats:
            g = summary.gap_ms_stats
            p50 = g.get("p50", 0.0)
            p95 = g.get("p95", 0.0)
            print(
                f"gap_ms      -> min={g['min']:.1f} p50={p50:.1f} p95={p95:.1f} max={g['max']:.1f}"
            )
        if summary.overshoot_stats:
            o = summary.overshoot_stats
            p50 = o.get("p50", 0.0)
            p95 = o.get("p95", 0.0)
            print(
                f"overshoot   -> min={o['min']:.4f} p50={p50:.4f} p95={p95:.4f} max={o['max']:.4f}"
            )

        # Nulos relevantes
        bad_nulls = {k: v for k, v in summary.nulls_pct.items() if v > 0}
        if bad_nulls:
            print("Nulos por columna (>0%):")
            for k, v in sorted(bad_nulls.items(), key=lambda kv: -kv[1]):
                print(f"  - {k}: {v:.1f}%")

        # Head/Tail compactos
        print("\nHEAD")
        print(pd.DataFrame(summary.head).to_string(index=False))
        print("\nTAIL")
        print(pd.DataFrame(summary.tail).to_string(index=False))

        # Resumen validador
        print("\n== VALIDATION ==")
        print(summarize_for_cli(summary.validation, max_issues=args.max_issues))

    # Política de salida:
    # - --strict: exit(1) si el validador dice ok=False
    # - si no hay --strict, siempre 0 (aunque se muestren issues)
    if args.strict and not summary.validation.get("ok", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

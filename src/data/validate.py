# src/data/validate.py
"""
Validador genérico de micro-barras/velas.

Objetivo
--------
- Comprobar consistencia OHLC (low <= open,close <= high).
- Validar tiempos: t_close >= t_open, orden no decreciente de t_open,
  y no solapamiento entre barras contiguas (si existen ambas columnas).
- Verificar duration_ms (si existe) frente a t_close - t_open.
- Verificar gap_ms (si existe) frente a next.t_open - prev.t_close y no-negatividad.
- Reportar NaNs por columna y columnas faltantes.

Uso rápido
----------
>>> import pandas as pd
>>> from src.data.validate import validate, assert_valid
>>> df = pd.read_csv("data/test.csv")
>>> result = validate(df)
>>> print(result["ok"], len(result["issues"]))
>>> assert_valid(df, strict=True)  # lanza ValueError si hay problemas

Notas
-----
- El validador sólo aplica checks para los que existan columnas suficientes.
- No intenta “arreglar” nada; sólo reporta y (opcionalmente) hace fail estricto.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Issue:
    code: str  # código corto: e.g., "OHLC_BOUNDS", "TIME_ORDER"
    severity: str  # "ERROR" | "WARN"
    message: str  # explicación humana
    count: int  # filas afectadas (aprox)
    sample_idx: list[int]  # indices de ejemplo (hasta 5)


def _sample_indices(mask: np.ndarray, limit: int = 5) -> list[int]:
    idx = np.flatnonzero(mask)[:limit]
    return [int(i) for i in idx]


def _to_ns(series: pd.Series) -> np.ndarray | None:
    """
    Convierte una serie de tiempos a *nanosegundos* (int64) sin tz.
    - Si ya es datetime64[ns], usa su representación entera.
    - Si es numérica, asume milisegundos y multiplica por 1e6.
    - Si no se reconoce, devuelve None.
    """
    if series is None:
        return None
    if np.issubdtype(series.dtype, np.datetime64):
        return series.view("int64").to_numpy()
    if np.issubdtype(series.dtype, np.number):
        # asumimos milisegundos -> ns
        return (series.to_numpy(dtype="float64") * 1_000_000).astype("int64", copy=False)
    # intento de parseo si es string
    try:
        dt = pd.to_datetime(series, utc=False, errors="coerce")
        if dt.notna().any():
            return dt.view("int64").to_numpy()
    except Exception:
        pass
    return None


def validate(
    df: pd.DataFrame,
    *,
    require_columns: list[str] | None = None,
    allow_nan_in: list[str] | None = None,
) -> dict:
    """
    Ejecuta validaciones y devuelve un diccionario autocontenido:
    {
        "ok": bool,
        "stats": {...},
        "issues": [Issue... como dicts],
        "nan_counts": {col: int},
        "missing_columns": [str],
        "checks_run": [str],
    }
    """
    issues: list[Issue] = []
    checks_run: list[str] = []

    if allow_nan_in is None:
        # Por defecto permitimos NaN en gap_ms (última barra puede no tener gap)
        allow_nan_in = ["gap_ms"]

    # ---- columnas esperadas (flexible) ----
    expected_any = [
        "t_open",
        "t_close",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "duration_ms",
        "gap_ms",
        # columnas opcionales que a veces tenemos:
        "v_buy",
        "v_sell",
        "n_trades",
        "overshoot",
    ]
    if require_columns is None:
        require_columns = []  # no forzamos nada por defecto

    cols_present = set(df.columns)
    missing_columns = [c for c in (require_columns or []) if c not in cols_present]

    # ---- NaNs (conteo) ----
    nan_counts = {c: int(df[c].isna().sum()) for c in df.columns}

    # ---- OHLC bounds ----
    if {"low", "high"}.issubset(cols_present):
        # low <= high (siempre)
        bad_low_high = (df["low"] > df["high"]).to_numpy()
        if bad_low_high.any():
            issues.append(
                Issue(
                    code="OHLC_LOW_GT_HIGH",
                    severity="ERROR",
                    message="Se encontraron filas con low > high.",
                    count=int(bad_low_high.sum()),
                    sample_idx=_sample_indices(bad_low_high),
                )
            )
        checks_run.append("LOW_LEQ_HIGH")

    if {"open", "low", "high"}.issubset(cols_present):
        bad_open = (df["open"] < df["low"]) | (df["open"] > df["high"])
        bad_open = bad_open.to_numpy()
        if bad_open.any():
            issues.append(
                Issue(
                    code="OHLC_OPEN_OUTSIDE",
                    severity="ERROR",
                    message="open fuera de [low, high].",
                    count=int(bad_open.sum()),
                    sample_idx=_sample_indices(bad_open),
                )
            )
        checks_run.append("OPEN_IN_RANGE")

    if {"close", "low", "high"}.issubset(cols_present):
        bad_close = (df["close"] < df["low"]) | (df["close"] > df["high"])
        bad_close = bad_close.to_numpy()
        if bad_close.any():
            issues.append(
                Issue(
                    code="OHLC_CLOSE_OUTSIDE",
                    severity="ERROR",
                    message="close fuera de [low, high].",
                    count=int(bad_close.sum()),
                    sample_idx=_sample_indices(bad_close),
                )
            )
        checks_run.append("CLOSE_IN_RANGE")

    # ---- tiempos básicos ----
    t_open_ns = _to_ns(df["t_open"]) if "t_open" in cols_present else None
    t_close_ns = _to_ns(df["t_close"]) if "t_close" in cols_present else None

    if t_open_ns is not None and t_close_ns is not None:
        # t_close >= t_open
        bad_tc_ge_to = t_close_ns < t_open_ns
        if bad_tc_ge_to.any():
            issues.append(
                Issue(
                    code="TIME_T_CLOSE_LT_T_OPEN",
                    severity="ERROR",
                    message="t_close < t_open.",
                    count=int(bad_tc_ge_to.sum()),
                    sample_idx=_sample_indices(bad_tc_ge_to),
                )
            )
        checks_run.append("T_CLOSE_GE_T_OPEN")

        # duration_ms (si existe)
        if "duration_ms" in cols_present:
            duration_ns = t_close_ns - t_open_ns
            # tolerancia: 1 ms por posibles redondeos/conversiones
            expected_ms = np.floor_divide(duration_ns + 500_000, 1_000_000)
            # diferencia donde duration_ms no coincide
            diff = df["duration_ms"].to_numpy(dtype="float64") - expected_ms.astype("float64")
            bad_duration = np.isnan(diff) | (np.abs(diff) > 1.0)
            if bad_duration.any():
                issues.append(
                    Issue(
                        code="DURATION_MISMATCH",
                        severity="ERROR",
                        message="duration_ms no coincide con t_close - t_open (tolerancia ±1 ms).",
                        count=int(bad_duration.sum()),
                        sample_idx=_sample_indices(bad_duration),
                    )
                )
            # no-negatividad
            bad_duration_neg = df["duration_ms"].to_numpy(dtype="float64") < 0
            if bad_duration_neg.any():
                issues.append(
                    Issue(
                        code="DURATION_NEGATIVE",
                        severity="ERROR",
                        message="duration_ms negativo.",
                        count=int(bad_duration_neg.sum()),
                        sample_idx=_sample_indices(bad_duration_neg),
                    )
                )
            checks_run.append("DURATION_CHECK")

        # orden no decreciente de t_open
        if len(t_open_ns) > 1:
            bad_monotonic = np.diff(t_open_ns) < 0
            if bad_monotonic.any():
                # donde falla, referimos a la fila siguiente
                idx_fail = np.flatnonzero(bad_monotonic) + 1
                mask_fail = np.zeros(len(df), dtype=bool)
                mask_fail[idx_fail] = True
                issues.append(
                    Issue(
                        code="TIME_T_OPEN_NOT_MONOTONIC",
                        severity="ERROR",
                        message="t_open no es no-decreciente.",
                        count=int(bad_monotonic.sum()),
                        sample_idx=_sample_indices(mask_fail),
                    )
                )
            checks_run.append("T_OPEN_MONOTONIC")

        # no solapamiento entre barras consecutivas: next.t_open >= prev.t_close
        if len(t_open_ns) > 1:
            prev_t_close = t_close_ns[:-1]
            next_t_open = t_open_ns[1:]
            overlap = next_t_open < prev_t_close
            if overlap.any():
                idx_fail = np.flatnonzero(overlap) + 1
                mask_fail = np.zeros(len(df), dtype=bool)
                mask_fail[idx_fail] = True
                issues.append(
                    Issue(
                        code="TIME_OVERLAP",
                        severity="ERROR",
                        message="Solapamiento entre barras contiguas (next.t_open < prev.t_close).",
                        count=int(overlap.sum()),
                        sample_idx=_sample_indices(mask_fail),
                    )
                )
            checks_run.append("NO_OVERLAP")

        # gap_ms (si existe)
        if "gap_ms" in cols_present and len(t_open_ns) > 1:
            # cálculo esperado: next.t_open - prev.t_close
            gap_ns = (t_open_ns[1:] - t_close_ns[:-1]).astype("float64")
            expected_gap_ms = np.floor_divide(gap_ns + 500_000, 1_000_000).astype("float64")
            gm = df["gap_ms"].to_numpy(dtype="float64")

            # alineamos: gap_ms[0] normalmente NaN; comparamos del 1..n-1 frente a expected 0..n-2
            gm_expected = gm[1:]
            # mismatch (tolerancia ±1 ms)
            bad_gap = np.isnan(gm_expected) | (np.abs(gm_expected - expected_gap_ms) > 1.0)
            if bad_gap.any():
                # referimos a indices 1..n-1
                mask_fail = np.zeros(len(df), dtype=bool)
                bad_pos = np.flatnonzero(bad_gap) + 1
                mask_fail[bad_pos] = True
                issues.append(
                    Issue(
                        code="GAP_MISMATCH",
                        severity="ERROR",
                        message="gap_ms no coincide con next.t_open - prev.t_close (tolerancia ±1 ms).",
                        count=int(bad_gap.sum()),
                        sample_idx=_sample_indices(mask_fail),
                    )
                )

            # no-negatividad (para las posiciones válidas)
            bad_gap_neg = (gm_expected < 0) & ~np.isnan(gm_expected)
            if bad_gap_neg.any():
                mask_fail = np.zeros(len(df), dtype=bool)
                bad_pos = np.flatnonzero(bad_gap_neg) + 1
                mask_fail[bad_pos] = True
                issues.append(
                    Issue(
                        code="GAP_NEGATIVE",
                        severity="ERROR",
                        message="gap_ms negativo (next.t_open < prev.t_close).",
                        count=int(bad_gap_neg.sum()),
                        sample_idx=_sample_indices(mask_fail),
                    )
                )
            checks_run.append("GAP_CHECK")

    # ---- NaNs no permitidos (salvo excepciones) ----
    for c, n in nan_counts.items():
        if n > 0 and c not in (allow_nan_in or []):
            issues.append(
                Issue(
                    code="NAN_PRESENT",
                    severity="ERROR",
                    message=f"NaNs detectados en '{c}'.",
                    count=int(n),
                    sample_idx=_sample_indices(df[c].isna().to_numpy()),
                )
            )
    checks_run.append("NAN_COUNTS")

    # ---- Estadísticas simples ----
    stats: dict[str, float | None] = {}
    stats["rows"] = int(len(df))
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
            stats[f"{col}_min"] = float(np.nanmin(s)) if s.notna().any() else None
            stats[f"{col}_max"] = float(np.nanmax(s)) if s.notna().any() else None

    result = {
        "ok": len([i for i in issues if i.severity == "ERROR"]) == 0 and len(missing_columns) == 0,
        "stats": stats,
        "issues": [asdict(i) for i in issues],
        "nan_counts": nan_counts,
        "missing_columns": missing_columns,
        "checks_run": checks_run,
        "columns_present": sorted(df.columns.tolist()),
        "columns_expected_any": expected_any,
        "columns_required": require_columns,
    }
    return result


def assert_valid(df: pd.DataFrame, *, strict: bool = True) -> None:
    """
    Lanza ValueError si strict=True y el dataset no supera las validaciones.
    """
    res = validate(df)
    if strict and not res["ok"]:
        # Componemos un resumen compacto
        parts = []
        if res["missing_columns"]:
            parts.append(f"Faltan columnas requeridas: {res['missing_columns']}")
        if res["issues"]:
            top = ", ".join(f"{x['code']}({x['count']})" for x in res["issues"][:6])
            parts.append(f"Issues: {top}{'...' if len(res['issues']) > 6 else ''}")
        msg = " / ".join(parts) if parts else "Fallo de validación."
        raise ValueError(msg)


def summarize_for_cli(result: dict, *, max_issues: int = 12) -> str:
    """
    Devuelve un string human-friendly para CLI.
    """
    lines = []
    lines.append(f"OK: {result['ok']}")
    if result["missing_columns"]:
        lines.append(f"- Faltan columnas requeridas: {result['missing_columns']}")
    if result["issues"]:
        lines.append(f"- Issues ({len(result['issues'])} tipos):")
        for i, it in enumerate(result["issues"][:max_issues], start=1):
            lines.append(
                f"  {i:02d}. {it['severity']} {it['code']}  "
                f"(count={it['count']}, sample_idx={it['sample_idx']})  – {it['message']}"
            )
        if len(result["issues"]) > max_issues:
            lines.append(f"  ... {len(result['issues']) - max_issues} más")
    lines.append(f"- Checks ejecutados: {', '.join(result['checks_run'])}")
    return "\n".join(lines)


# CLI opcional: validar un CSV rápido
if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Validador de micro-barras.")
    p.add_argument("--path", required=True, help="Ruta al CSV a validar.")
    p.add_argument(
        "--require", nargs="*", default=[], help="Columnas requeridas (todas deben existir)."
    )
    p.add_argument(
        "--allow-nan", nargs="*", default=["gap_ms"], help="Columnas en las que se permiten NaNs."
    )
    p.add_argument("--strict", action="store_true", help="Exit code 1 si hay errores.")
    args = p.parse_args()

    df = pd.read_csv(args.path)
    res = validate(df, require_columns=args.require, allow_nan_in=args.allow_nan)
    print(summarize_for_cli(res))

    if args.strict and not res["ok"]:
        sys.exit(1)

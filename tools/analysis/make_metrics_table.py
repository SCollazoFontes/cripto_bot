# tools/make_metrics_table.py
"""
Genera tablas de métricas comparables entre runs y las guarda en disco.

Entrada
-------
- Uno o más `run_dir` con `equity.csv` (y opcionalmente `trades.csv`).
- Alternativamente, un patrón `--glob` para descubrir runs automáticamente.

Salida
------
- CSV/Markdown con la comparación de métricas (sin imprimir por consola).
- (Opcional) Un `metrics.json` en cada `run_dir` con las métricas del run.

Uso
----
Ejemplos:
    python -m tools.make_metrics_table --glob "runs/2025110*T*" --out-csv report/metrics.csv --out-md report/metrics.md
    python -m tools.make_metrics_table --runs runs/A runs/B --sort-by sharpe --asc --out-csv metrics.csv
    python -m tools.make_metrics_table --glob "runs/*" --per-run-json

Notas
-----
- No imprime nada por stdout salvo que se pida `--print`.
- Si falta `tabulate`, el Markdown cae automáticamente a `to_string()` para no romper.
- Las columnas por defecto están ordenadas para lectura rápida. Se pueden personalizar con `--columns`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

# Import del comparador común
from report.metrics_compare import compare_runs

DEFAULT_COLUMNS = [
    "run_dir",
    "t0",
    "t1",
    "hours",
    "equity_start",
    "equity_end",
    "ret_total",
    "cagr",
    "vol_ann",
    "sharpe",
    "sortino",
    "max_dd",
    "dd_duration_steps",
    "calmar",
    "n_trades",
    "win_rate_pct",
    "avg_trade_qty",
    "notional",
    "fees_total",
    "fee_bps",
    "slippage_bps",
    "exposure_pct",
    "error",
]


def _expand_glob(glob_pattern: str) -> list[str]:
    return [
        str(p) for p in sorted(Path(".").glob(glob_pattern)) if (Path(p) / "equity.csv").exists()
    ]


def _select_columns(df: pd.DataFrame, columns: list[str] | None) -> pd.DataFrame:
    if not columns:
        columns = DEFAULT_COLUMNS
    cols = [c for c in columns if c in df.columns]
    # Añade cualquier columna extra (latencias) al final
    extras = [c for c in df.columns if c not in cols]
    return df[cols + extras]


def _round_numeric(df: pd.DataFrame, ndigits_map: dict | None = None) -> pd.DataFrame:
    if ndigits_map is None:
        ndigits_map = {
            "hours": 6,
            "ret_total": 6,
            "cagr": 6,
            "vol_ann": 6,
            "sharpe": 6,
            "sortino": 6,
            "max_dd": 6,
            "calmar": 6,
            "win_rate_pct": 2,
            "avg_trade_qty": 6,
            "notional": 2,
            "fees_total": 4,
            "fee_bps": 3,
            "slippage_bps": 3,
            "exposure_pct": 2,
        }
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_numeric_dtype(out[c]):
            nd = ndigits_map.get(c, 6)
            out[c] = out[c].round(nd)
    return out


def _save_csv(df: pd.DataFrame, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)


def _save_md(df: pd.DataFrame, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(df.to_markdown(index=False))
    except Exception:
        # Fallback si no está tabulate
        p.write_text(df.to_string(index=False))


def _write_per_run_json(df: pd.DataFrame) -> None:
    """
    Escribe un `metrics.json` dentro de cada run_dir con la fila correspondiente.
    Ignora filas con columna 'error' no vacía.
    """
    if "run_dir" not in df.columns:
        return
    for row in df.itertuples(index=False):
        row_dict = (
            row._asdict() if hasattr(row, "_asdict") else dict(zip(df.columns, row, strict=False))
        )
        if "error" in row_dict and isinstance(row_dict["error"], str) and row_dict["error"]:
            continue
        run_dir = Path(str(row_dict["run_dir"]))
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / "metrics.json"
        # Limpia NaN -> None para JSON válido
        clean = {
            k: (None if (isinstance(v, float) and (np.isnan(v) or np.isinf(v))) else v)
            for k, v in row_dict.items()
        }
        out_path.write_text(json.dumps(clean, indent=2))


def main():
    ap = argparse.ArgumentParser(
        description="Genera tablas de métricas entre runs y las guarda en disco."
    )
    ap.add_argument(
        "--runs", nargs="*", default=[], help="Lista de run_dir (cada uno con equity.csv)."
    )
    ap.add_argument(
        "--glob", type=str, default=None, help='Patrón glob de runs, p.ej. "runs/2025110*T*".'
    )
    ap.add_argument(
        "--sort-by", type=str, default="cagr", help="Columna para ordenar (por defecto: cagr)."
    )
    ap.add_argument(
        "--asc", action="store_true", help="Orden ascendente (por defecto descendente)."
    )
    ap.add_argument(
        "--columns", nargs="*", default=None, help="Subconjunto/orden de columnas (por nombre)."
    )
    ap.add_argument("--out-csv", type=str, default=None, help="Ruta del CSV de salida.")
    ap.add_argument("--out-md", type=str, default=None, help="Ruta del Markdown de salida.")
    ap.add_argument(
        "--per-run-json", action="store_true", help="Escribe metrics.json dentro de cada run_dir."
    )
    ap.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Imprime la tabla por consola (opcional).",
    )
    args = ap.parse_args()

    run_list: list[str] = list(args.runs)
    if args.glob:
        run_list.extend(_expand_glob(args.glob))
    if not run_list:
        raise SystemExit("Debes proporcionar --runs ... o --glob ...")

    df = compare_runs(run_list, sort_by=args.sort_by, descending=not args.asc)
    df = _select_columns(df, args.columns)
    df_fmt = _round_numeric(df)

    if args.out_csv:
        _save_csv(df_fmt, args.out_csv)
    if args.out_md:
        _save_md(df_fmt, args.out_md)
    if args.per_run_json:
        _write_per_run_json(df_fmt)

    if args.do_print and not (args.out_csv or args.out_md):
        # Solo imprime si no se pidió archivo, para respetar tu preferencia
        try:
            print(df_fmt.to_markdown(index=False))
        except Exception:
            print(df_fmt.to_string(index=False))


if __name__ == "__main__":
    main()

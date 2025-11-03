# tools/make_metrics.py

"""
Calcula métricas básicas de un run y escribe runs/<ts>/summary.json

Uso:
    python -m tools.make_metrics --run-dir runs/20251102T212811Z
Opciones:
    --annualize 0|1   Si 1, añade Sharpe anualizado estimando barras/día (simple).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

from src.report.metrics_basic import compute_metrics, to_dict, write_summary_json


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cálculo de métricas básicas de un run.")
    p.add_argument("--run-dir", required=True, help="Carpeta del run (runs/<ts>/).")
    p.add_argument(
        "--annualize",
        type=int,
        default=0,
        help="Si 1, añade campos anualizados (estimación simple).",
    )
    p.add_argument(
        "--bars_per_day",
        type=float,
        default=24 * 60,  # por defecto: suponemos 1 barra por minuto (ajustable)
        help="Estimación de barras/día para anualización.",
    )
    p.add_argument(
        "--days_per_year",
        type=float,
        default=365.0,
        help="Días/año para anualización.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir)
    equity_csv = run_dir / "equity.csv"
    if not equity_csv.exists():
        raise FileNotFoundError(f"No existe {equity_csv}. Ejecuta antes el runner.")

    metrics = compute_metrics(equity_csv)
    summary = to_dict(metrics)

    if args.annualize == 1:
        # Sharpe anualizado ≈ Sharpe_barra * sqrt(barras/año)
        bars_per_year = float(args.bars_per_day) * float(args.days_per_year)
        if bars_per_year > 0 and summary["ret_std_per_bar"] not in (0.0, 0, None):
            shp_bar = summary["sharpe_per_bar"]
            if shp_bar is not None:
                summary["sharpe_annualized"] = float(shp_bar) * (bars_per_year**0.5)
        summary["annualization"] = {
            "bars_per_day": float(args.bars_per_day),
            "days_per_year": float(args.days_per_year),
        }

    out = write_summary_json(run_dir, summary)
    logger.info("✅ summary.json escrito: {}", out)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Phase 1: Grid search across builders + Bayesian optimization of Momentum params.

Para cada builder config (tick limits, policies), ejecuta optimización Bayesiana
de parámetros de Momentum sobre una ventana de 7 días.

Objetivo: identificar los 3-5 mejores builders (tick configs) según Sharpe o retorno.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from tools.optimize.builder_configs import BUILDER_CATALOG
from tools.optimize.datasets import DatasetSpec
from tools.optimize.runner import OptimizationConfig, OptimizationRunner
from tools.optimize.targets.momentum import BrokerParams, build_momentum_target


def run_phase1(
    symbol: str,
    dataset_path: Path,
    window: str,
    builders: list[str],
    max_trials: int,
    out_root: Path,
) -> None:
    """
    Ejecuta optimización Bayesiana para cada builder.

    Args:
        symbol: Par a tradear (BTCUSDT)
        dataset_path: Path al master dataset
        window: Ventana temporal (ej. "7d")
        builders: Lista de nombres de builders a probar
        max_trials: Trials Bayesianos por builder
        out_root: Directorio base de salida
    """
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    phase1_dir = out_root / f"{ts}_phase1_builders"
    phase1_dir.mkdir(parents=True, exist_ok=True)

    dataset = DatasetSpec(dataset_path)
    broker = BrokerParams(fees_bps=10.0, slip_bps=5.0, starting_cash=1000.0)

    results_summary = []

    for builder_name in builders:
        print(f"\n{'='*60}")
        print(f"BUILDER: {builder_name}")
        print(f"{'='*60}")

        builder_cfg = BUILDER_CATALOG[builder_name]

        # Build Momentum target con espacio Bayesiano
        target = build_momentum_target(
            symbol=symbol,
            builder_cfg=builder_cfg.as_kwargs(),
            broker_params=broker,
            grid_mode=False,  # Bayesian
            min_trades=3,
        )

        # Config de optimización
        config = OptimizationConfig(
            dataset=dataset,
            windows=[window],
            optimizer_name="bayes",
            max_trials=max_trials,
            random_state=42,
            maximize=True,
            target_metric="total_return",
            out_root=phase1_dir,
            save_best_only=True,
        )

        runner = OptimizationRunner(target, config)
        trials = runner.run(builder_name=builder_name)

        if trials:
            best = max(trials, key=lambda t: t.score)
            results_summary.append(
                {
                    "builder": builder_name,
                    "best_score": best.score,
                    "best_params": best.params,
                    "metrics": best.metrics,
                    "run_dir": str(best.run_dir),
                }
            )
            print(f"\n✅ {builder_name}: best_score={best.score:.6f}")
        else:
            print(f"\n⚠️  {builder_name}: sin trials exitosos")

    # Guardar resumen consolidado
    summary_path = phase1_dir / "phase1_summary.json"
    summary_path.write_text(json.dumps(results_summary, indent=2), encoding="utf-8")

    # Rankear por score
    ranked = sorted(results_summary, key=lambda x: x["best_score"], reverse=True)
    print(f"\n{'='*60}")
    print("RANKING FINAL (por score):")
    print(f"{'='*60}")
    for i, r in enumerate(ranked, 1):
        print(f"{i}. {r['builder']}: score={r['best_score']:.6f}")

    print(f"\n📁 Resultados guardados en: {phase1_dir}")
    print(f"📊 Resumen: {summary_path}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 1: Builder grid + Bayesian Momentum optimization"
    )
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--dataset", default="data/datasets/BTCUSDT_master.csv")
    ap.add_argument("--window", default="7d", help="Ventana temporal (ej. 7d, 14d)")
    ap.add_argument(
        "--builders",
        nargs="*",
        default=[
            "compact_60ticks",
            "dense_30ticks",
            "wide_80ticks_all",
            "hybrid_100ticks_all",
            "default_120ticks",
        ],
        help="Builders a probar",
    )
    ap.add_argument("--max-trials", type=int, default=50, help="Trials Bayesianos por builder")
    ap.add_argument("--out-root", default="runs_opt")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    run_phase1(
        symbol=args.symbol,
        dataset_path=Path(args.dataset),
        window=args.window,
        builders=args.builders,
        max_trials=args.max_trials,
        out_root=Path(args.out_root),
    )


if __name__ == "__main__":
    main()

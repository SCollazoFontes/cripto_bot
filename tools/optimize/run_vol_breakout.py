#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.optimize.builder_configs import get_builder, list_builders
from tools.optimize.datasets import DatasetSpec
from tools.optimize.momentum import BrokerParams  # reuse same broker params dataclass
from tools.optimize.runner import OptimizationConfig, OptimizationRunner
from tools.optimize.vol_breakout import build_vol_breakout_target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optimización de la estrategia Volatility Breakout."
    )
    parser.add_argument(
        "--dataset", default="data/datasets/BTCUSDT_master.csv", help="CSV maestro de trades."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo de trading.")
    parser.add_argument(
        "--windows",
        nargs="+",
        default=["30m", "1h", "3h", "6h", "12h", "1d"],
        help="Ventanas relativas/absolutas a evaluar.",
    )
    parser.add_argument(
        "--optimizer",
        choices=["grid", "random", "bayes"],
        default="bayes",
        help="Tipo de optimizador.",
    )
    parser.add_argument(
        "--max-trials", type=int, default=80, help="Número máximo de combinaciones por ventana."
    )
    parser.add_argument(
        "--random-state", type=int, default=42, help="Semilla para optimizadores aleatorios."
    )
    parser.add_argument(
        "--fees-bps", type=float, default=10.0, help="Fees en bps para el SimBroker."
    )
    parser.add_argument(
        "--slip-bps", type=float, default=5.0, help="Slippage en bps para el SimBroker."
    )
    parser.add_argument("--out-root", default="runs_opt_vol", help="Directorio raíz de resultados.")
    parser.add_argument(
        "--min-improvement",
        type=float,
        default=0.0002,
        help="Mejora mínima para resetear paciencia.",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=12,
        help="Número de trials consecutivos sin mejora antes de detener la ventana.",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=5,
        help="Número mínimo de trades para considerar válido un resultado.",
    )
    parser.add_argument(
        "--builders",
        nargs="+",
        default=["hybrid_100ticks_all", "compact_60ticks"],
        help=f"Builders a evaluar (por nombre). Disponibles: {', '.join(list_builders())}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = DatasetSpec(Path(args.dataset))
    broker_params = BrokerParams(
        fees_bps=args.fees_bps, slip_bps=args.slip_bps, starting_cash=100.0
    )
    overall_best = None

    for builder_name in args.builders:
        builder_cfg = get_builder(builder_name)
        print(f"[Runner] Evaluando builder '{builder_cfg.name}' → {builder_cfg.as_kwargs()}")
        target = build_vol_breakout_target(
            symbol=args.symbol,
            builder_cfg=builder_cfg.as_kwargs(),
            broker_params=broker_params,
            grid_mode=args.optimizer.lower() == "grid",
            min_trades=args.min_trades,
        )
        config = OptimizationConfig(
            dataset=dataset,
            windows=args.windows,
            optimizer_name=args.optimizer,
            max_trials=args.max_trials,
            random_state=args.random_state,
            out_root=Path(args.out_root) / builder_cfg.name,
            min_improvement=args.min_improvement,
            patience=args.patience,
        )
        runner = OptimizationRunner(target, config)
        results = runner.run()
        if not results:
            print(f"[Runner] Builder '{builder_cfg.name}' no generó resultados.")
            continue
        best = max(results, key=lambda r: r.score)
        if overall_best is None or best.score > overall_best.score:
            overall_best = best
        print(f"[Runner] Mejor resultado builder '{builder_cfg.name}':")
        print(json.dumps(best.metrics, indent=2))
        print("Parámetros:", best.params)
        print("Run dir:", best.run_dir)

    if overall_best:
        print("=== Mejor resultado global (entre todos los builders) ===")
        print(json.dumps(overall_best.metrics, indent=2))
        print("Parámetros:", overall_best.params)
        print("Run dir:", overall_best.run_dir)
    else:
        print("No se generaron resultados (verifica dataset/builders).")


if __name__ == "__main__":
    main()

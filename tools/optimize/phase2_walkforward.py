#!/usr/bin/env python3
"""
Phase 2: Walk-forward validation del config ganador.

Usa los parámetros de Momentum optimizados de Phase 1 + hybrid_100ticks_all builder.
Valida en 5 folds de 6 días cada uno (25 días totales) para verificar robustez.

Salida: runs/<timestamp>_phase2_wf/
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from tools.optimize.builder_configs import get_builder
from tools.optimize.datasets import DatasetSpec, slice_windows
from tools.optimize.targets.momentum import BrokerParams, evaluate_momentum_target


def run_phase2_walkforward(
    symbol: str,
    dataset_path: Path,
    builder_name: str,
    momentum_params: dict,
    fold_window: str,
    num_folds: int,
    out_root: Path,
    fees_bps: float = 10.0,
    slip_bps: float = 20.0,
) -> None:
    """
    Ejecuta walk-forward validation: N folds, cada uno optimiza en uno y valida en el siguiente.

    Args:
        symbol: Par a tradear (BTCUSDT)
        dataset_path: Path al master dataset
        builder_name: Builder ganador (ej. hybrid_100ticks_all)
        momentum_params: Parámetros de Momentum optimizados
        fold_window: Ventana por fold (ej. "6d" para 25 días = 5 folds)
        num_folds: Número de folds
        out_root: Directorio base de salida
    """
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    phase2_dir = out_root / f"{ts}_phase2_wf"
    phase2_dir.mkdir(parents=True, exist_ok=True)

    dataset = DatasetSpec(dataset_path)
    builder_cfg = get_builder(builder_name)
    broker = BrokerParams(fees_bps=fees_bps, slip_bps=slip_bps, starting_cash=1000.0)

    # Slice windows: 5 folds de 6 días cada uno
    windows = slice_windows(dataset, [fold_window] * num_folds)
    if len(windows) < num_folds:
        print(f"⚠️  Solo {len(windows)} folds disponibles (solicitados {num_folds})")
        windows = windows[:num_folds]

    fold_results = []

    print(f"\n{'='*70}")
    print(f"WALK-FORWARD VALIDATION: {len(windows)} folds")
    print(f"Builder: {builder_name}")
    print(f"Momentum params: {momentum_params}")
    print(f"{'='*70}\n")

    for i, window in enumerate(windows, 1):
        print(f"\n[FOLD {i}/{len(windows)}] {window.label}")
        fold_dir = phase2_dir / f"fold_{i}"
        fold_dir.mkdir(parents=True, exist_ok=True)

        trial = evaluate_momentum_target(
            momentum_params,
            window,
            fold_dir,
            symbol=symbol,
            builder_cfg=builder_cfg.as_kwargs(),
            broker_params=broker,
            min_trades=1,
        )

        metrics = trial.metrics
        fold_results.append(
            {
                "fold": i,
                "window_label": window.label,
                "total_return": trial.score,
                "trades": metrics.get("trades", 0),
                "equity_final": metrics.get("equity_final", 0),
                "run_dir": str(fold_dir),
            }
        )

        print(f"  ✅ Return: {trial.score:.6f} | Trades: {metrics.get('trades', 0)}")

    # Resumen final
    total_return = sum(r["total_return"] for r in fold_results)
    total_trades = sum(r["trades"] for r in fold_results)
    avg_return = total_return / len(fold_results) if fold_results else 0.0

    summary = {
        "builder": builder_name,
        "momentum_params": momentum_params,
        "num_folds": len(fold_results),
        "fold_results": fold_results,
        "total_return_cumulative": total_return,
        "total_trades": total_trades,
        "avg_return_per_fold": avg_return,
        "timestamp": ts,
    }

    summary_path = phase2_dir / "phase2_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n{'='*70}")
    print("WALK-FORWARD SUMMARY")
    print(f"{'='*70}")
    print(f"Total return (cumulative): {total_return:.6f}")
    print(f"Total trades: {total_trades}")
    print(f"Avg return per fold: {avg_return:.6f}")
    print(f"📁 Results: {phase2_dir}")
    print(f"📊 Summary: {summary_path}\n")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 2: Walk-forward validation")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--dataset", default="data/datasets/BTCUSDT_master.csv")
    ap.add_argument("--builder", default="hybrid_100ticks_all")
    ap.add_argument("--fold-window", default="6d", help="Ventana por fold")
    ap.add_argument("--num-folds", type=int, default=5, help="Número de folds")
    ap.add_argument("--out-root", default="runs")
    ap.add_argument(
        "--params",
        default=json.dumps(
            {
                "lookback_ticks": 28,
                "entry_threshold": 0.00121,  # +10% para filtrar señales marginales
                "exit_threshold": 0.00011,  # +10% para salidas menos frecuentes
                "stop_loss_pct": 0.038,
                "take_profit_pct": 0.065,
                "cooldown_bars": 3,
                "volatility_window": 100,
                "min_volatility": 0.0002,
                "max_volatility": 0.049,
                "trend_confirmation": True,  # activamos confirmación de tendencia
                "order_notional": 100.0,  # USD por trade (stress de tamaño)
            }
        ),
        help="Parámetros de Momentum (JSON)",
    )
    ap.add_argument("--fees-bps", type=float, default=10.0, help="Fees en bps (default 10)")
    ap.add_argument(
        "--slip-bps", type=float, default=20.0, help="Slippage base en bps (default 20 stress)"
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    params = json.loads(args.params)
    run_phase2_walkforward(
        symbol=args.symbol,
        dataset_path=Path(args.dataset),
        builder_name=args.builder,
        momentum_params=params,
        fold_window=args.fold_window,
        num_folds=args.num_folds,
        out_root=Path(args.out_root),
        fees_bps=args.fees_bps,
        slip_bps=args.slip_bps,
    )


if __name__ == "__main__":
    main()

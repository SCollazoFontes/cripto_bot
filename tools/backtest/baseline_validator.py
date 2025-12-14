#!/usr/bin/env python3
"""
Baseline Validator - Valida estrategias en condiciones ideales (sin costes).

Propósito:
----------
Validar si una estrategia es rentable ANTES de optimizar parámetros.
Ejecuta backtest con fees=0, slippage=0 para ver el potencial real.

Uso:
----
    python -m tools.backtest.baseline_validator \\
        --strategy momentum \\
        --dataset data/datasets/BTCUSDT_master.csv \\
        --window 7d \\
        --builder hybrid_100ticks_all

Output:
-------
- Total return (sin costes)
- Win rate
- Número de trades
- Max drawdown
- Sharpe ratio

Criterios de viabilidad:
------------------------
✅ Rentable: return > 0, win_rate > 40%, sharpe > 0.5
⚠️  Marginal: return ~ 0, win_rate 30-40%
❌ No rentable: return < 0, win_rate < 30%
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from report.metrics_compare import metrics_for_run
from tools.optimize.builder_configs import get_builder
from tools.optimize.datasets import DatasetSpec, slice_windows
from tools.optimize.targets.momentum import BrokerParams, evaluate_momentum_target


def validate_baseline(
    strategy_name: str,
    dataset_path: Path,
    builder_name: str,
    window: str,
    params: dict | None = None,
) -> dict:
    """
    Valida una estrategia en condiciones ideales (fees=0, slip=0).

    Args:
        strategy_name: Nombre de la estrategia (momentum, vwap, vol_breakout)
        dataset_path: Path al dataset CSV
        builder_name: Builder de barras a usar
        window: Ventana temporal (ej. "7d", "1d")
        params: Parámetros de la estrategia (None = defaults)

    Returns:
        dict con métricas: return, win_rate, trades, max_dd, sharpe, verdict
    """
    # Broker con CERO costes
    broker = BrokerParams(fees_bps=0.0, slip_bps=0.0, starting_cash=1000.0)

    # Dataset y builder
    dataset = DatasetSpec(dataset_path)
    builder_cfg = get_builder(builder_name)

    # Slicing
    windows = slice_windows(dataset, [window])
    if not windows:
        raise ValueError(f"No se pudo crear ventana {window} del dataset")

    target_window = windows[0]

    # Crear directorio temporal para resultados
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(f"runs/baseline_validation/{ts}_{strategy_name}")
    run_dir.mkdir(parents=True, exist_ok=True)

    # Evaluar estrategia
    if strategy_name == "momentum":
        if params is None:
            params = {}  # Usa defaults de MomentumStrategy

        trial = evaluate_momentum_target(
            params,
            target_window,
            run_dir,
            symbol="BTCUSDT",
            builder_cfg=builder_cfg.as_kwargs(),
            broker_params=broker,
            min_trades=1,
        )
    else:
        raise NotImplementedError(f"Estrategia {strategy_name} no soportada aún")

    # Calcular métricas completas
    full_metrics = metrics_for_run(run_dir)

    # Criterios de viabilidad
    total_return = full_metrics.get("ret_total", 0.0)
    win_rate = full_metrics.get("win_rate_pct", 0.0)
    sharpe = full_metrics.get("sharpe", 0.0)
    n_trades = full_metrics.get("n_trades", 0)
    max_dd = full_metrics.get("max_dd", 0.0)

    # Verdict
    if total_return > 0 and win_rate > 40 and sharpe > 0.5:
        verdict = "✅ RENTABLE - Proceder con optimización"
    elif total_return >= 0 and win_rate > 30:
        verdict = "⚠️  MARGINAL - Considerar ajustes de params antes de optimizar"
    else:
        verdict = "❌ NO RENTABLE - Rediseñar lógica de estrategia"

    result = {
        "strategy": strategy_name,
        "dataset": str(dataset_path),
        "builder": builder_name,
        "window": window,
        "broker": "IDEAL (fees=0, slip=0)",
        "metrics": {
            "total_return": total_return,
            "win_rate_pct": win_rate,
            "sharpe": sharpe if not pd.isna(sharpe) else None,
            "n_trades": n_trades,
            "max_drawdown": max_dd,
        },
        "verdict": verdict,
        "run_dir": str(run_dir),
    }

    # Guardar resultado
    result_path = run_dir / "baseline_validation.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    return result


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Baseline Validator - Validar estrategias sin costes")
    ap.add_argument(
        "--strategy",
        choices=["momentum", "vwap", "vol_breakout"],
        required=True,
        help="Estrategia a validar",
    )
    ap.add_argument(
        "--dataset",
        default="data/datasets/BTCUSDT_master.csv",
        help="Path al dataset CSV",
    )
    ap.add_argument(
        "--builder",
        default="hybrid_100ticks_all",
        help="Builder de barras",
    )
    ap.add_argument(
        "--window",
        default="7d",
        help="Ventana temporal para backtest (ej. 7d, 1d, 12h)",
    )
    ap.add_argument(
        "--params",
        type=json.loads,
        default=None,
        help="Parámetros de estrategia en JSON (None = defaults)",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    print("=" * 70)
    print("BASELINE VALIDATION - Condiciones ideales (fees=0, slip=0)")
    print("=" * 70)
    print(f"Estrategia: {args.strategy}")
    print(f"Dataset: {args.dataset}")
    print(f"Builder: {args.builder}")
    print(f"Window: {args.window}")
    print(f"Params: {args.params or 'defaults'}")
    print("=" * 70)
    print()

    result = validate_baseline(
        strategy_name=args.strategy,
        dataset_path=Path(args.dataset),
        builder_name=args.builder,
        window=args.window,
        params=args.params,
    )

    print()
    print("=" * 70)
    print("RESULTADO")
    print("=" * 70)
    print(f"Total Return:    {result['metrics']['total_return']:>12.6f}")
    print(f"Win Rate:        {result['metrics']['win_rate_pct']:>12.2f}%")
    print(f"Trades:          {result['metrics']['n_trades']:>12}")
    print(f"Max Drawdown:    {result['metrics']['max_drawdown']:>12.6f}")
    print(
        f"Sharpe Ratio:    {result['metrics']['sharpe'] if result['metrics']['sharpe'] else 'N/A':>12}"
    )
    print("=" * 70)
    print(f"\n{result['verdict']}\n")
    print(f"📁 Results: {result['run_dir']}")
    print(f"📊 Report: {result['run_dir']}/baseline_validation.json\n")


if __name__ == "__main__":
    import pandas as pd  # needed for isna check

    main()

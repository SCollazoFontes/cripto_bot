#!/usr/bin/env python3
from __future__ import annotations

"""
Run rápido de Momentum sobre una ventana del dataset maestro, generando
salidas en runs/<timestamp>/: equity.csv, trades.csv, summary.json.

Objetivo: producir resultados inmediatos (trades y retorno) sin optimizar.
"""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from tools.optimize.builder_configs import get_builder
from tools.optimize.datasets import DatasetSpec, slice_windows
from tools.optimize.targets.momentum import BrokerParams, evaluate_momentum_target


def run(
    symbol: str,
    dataset_path: Path,
    window: str,
    builder_name: str,
    out_root: Path,
    params_json: str,
) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / f"{ts}_momentum_quick"
    run_dir.mkdir(parents=True, exist_ok=True)

    dataset = DatasetSpec(dataset_path)
    windows = slice_windows(dataset, [window])
    if not windows:
        raise RuntimeError(f"Ventana '{window}' no produjo datos")
    window_slice = windows[0]

    builder = get_builder(builder_name)

    try:
        params = json.loads(params_json) if params_json else {}
    except Exception as e:
        raise RuntimeError(f"Params JSON inválido: {e}")

    broker = BrokerParams(fees_bps=10.0, slip_bps=5.0, starting_cash=1000.0)

    trial = evaluate_momentum_target(
        params,
        window_slice,
        run_dir,
        symbol=symbol,
        builder_cfg=builder.as_kwargs(),
        broker_params=broker,
        min_trades=1,
    )

    # Guardar resumen
    summary = {
        "symbol": symbol,
        "builder": builder.name,
        "window": window_slice.label,
        "params": params,
        "metrics": trial.metrics,
        "score": trial.score,
        "run_dir": str(run_dir),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return run_dir


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Quick run de Momentum para generar resultados")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--dataset", default="data/datasets/BTCUSDT_master.csv")
    ap.add_argument("--window", default="7d")
    ap.add_argument("--builder", default="hybrid_100ticks_all")
    ap.add_argument("--out-root", default="runs")
    ap.add_argument(
        "--params",
        default=json.dumps(
            {
                "lookback_ticks": 30,
                "entry_threshold": 0.0004,
                "exit_threshold": 0.0002,
                "order_notional": 50.0,
                "stop_loss_pct": 0.025,
                "take_profit_pct": 0.04,
                "cooldown_bars": 0,
                "min_volatility": 0.0,
                "max_volatility": 1.0,
                "trend_confirmation": False,
                "volatility_window": 50,
            }
        ),
        help="Parámetros JSON para Momentum",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    run(
        symbol=args.symbol,
        dataset_path=Path(args.dataset),
        window=args.window,
        builder_name=args.builder,
        out_root=Path(args.out_root),
        params_json=args.params,
    )


if __name__ == "__main__":
    main()

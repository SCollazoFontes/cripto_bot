#!/usr/bin/env python3
from __future__ import annotations

"""
Walk-forward evaluator for Momentum over ~30d split into folds.

Runs a small targeted grid and applies guardrails:
- min trades per fold
- non-negative aggregate return required

Outputs a consolidated CSV in runs/<ts>_wf/: results.csv and best_summary.json.
"""

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from tools.optimize.builder_configs import get_builder
from tools.optimize.datasets import DatasetSpec, slice_windows
from tools.optimize.targets.momentum import BrokerParams, evaluate_momentum_target


@dataclass
class Guardrails:
    min_trades_per_fold: int = 80
    require_non_negative_total: bool = True


def generate_param_grid() -> list[dict[str, Any]]:
    grid: list[dict[str, Any]] = []
    for lb in (44, 48, 50):
        for entry in (0.0009, 0.001, 0.0011):
            for min_profit in (40.0, 50.0, 60.0):
                grid.append(
                    {
                        "lookback_ticks": lb,
                        "entry_threshold": entry,
                        "exit_threshold": 0.0003,
                        "order_notional": 120.0,
                        "stop_loss_pct": 0.03,
                        "take_profit_pct": 0.06,
                        "cooldown_bars": 3,
                        "min_volatility": 0.0001,
                        "max_volatility": 0.08,
                        "trend_confirmation": True,
                        "volatility_window": 60,
                        "min_profit_bps": min_profit,
                    }
                )
    return grid


def run_walkforward(
    *,
    symbol: str,
    dataset_path: Path,
    builder_name: str,
    out_root: Path,
    guardrails: Guardrails,
) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_root / f"{ts}_wf"
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset = DatasetSpec(dataset_path)
    # Create 5 folds of ~6d each across ~30d
    windows = slice_windows(dataset, ["6d", "6d", "6d", "6d", "6d"])
    if not windows:
        raise RuntimeError("No windows produced for 30d walk-forward")

    builder = get_builder(builder_name)
    broker = BrokerParams(fees_bps=10.0, slip_bps=5.0, starting_cash=1000.0)

    grid = generate_param_grid()
    results_rows: list[dict[str, Any]] = []

    for params in grid:
        fold_returns: list[float] = []
        fold_trades: list[int] = []
        total_equity_end: float = 0.0
        equity_start: float | None = None

        for w in windows:
            run_dir = (
                out_dir
                / f"{params['lookback_ticks']}_{params['entry_threshold']}_{params['exit_threshold']}"
                / w.label
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            trial = evaluate_momentum_target(
                params,
                w,
                run_dir,
                symbol=symbol,
                builder_cfg=builder.as_kwargs(),
                broker_params=broker,
                min_trades=1,
            )
            m = trial.metrics
            fold_trades.append(int(m.get("trades", 0)))
            fold_returns.append(float(m.get("total_return", 0.0)))
            if equity_start is None:
                equity_start = (
                    float(m.get("equity_final", 0.0)) / (1.0 + float(m.get("total_return", 0.0)))
                    if m.get("total_return", 0.0)
                    else float(m.get("equity_final", 0.0))
                )
            total_equity_end += float(m.get("equity_final", 0.0))

        total_return = sum(fold_returns)
        total_trades = sum(fold_trades)
        passes_trades = all(t >= guardrails.min_trades_per_fold for t in fold_trades)
        passes_total = (total_return >= 0.0) if guardrails.require_non_negative_total else True

        row = {
            "lookback": params["lookback_ticks"],
            "entry": params["entry_threshold"],
            "exit": params["exit_threshold"],
            "cooldown": params["cooldown_bars"],
            "trend": params["trend_confirmation"],
            "order_notional": params["order_notional"],
            "fold_returns": json.dumps(fold_returns),
            "fold_trades": json.dumps(fold_trades),
            "total_return": total_return,
            "total_trades": total_trades,
            "passes_trades": passes_trades,
            "passes_total": passes_total,
        }
        results_rows.append(row)

    # Write CSV
    csv_path = out_dir / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "lookback",
                "entry",
                "exit",
                "cooldown",
                "trend",
                "order_notional",
                "fold_returns",
                "fold_trades",
                "total_return",
                "total_trades",
                "passes_trades",
                "passes_total",
            ],
        )
        writer.writeheader()
        writer.writerows(results_rows)

    # Select best (max total_return among those passing guardrails)
    passing = [r for r in results_rows if r["passes_trades"] and r["passes_total"]]
    best = (
        max(passing, key=lambda r: r["total_return"])
        if passing
        else max(results_rows, key=lambda r: r["total_return"])
    )  # fallback
    (out_dir / "best_summary.json").write_text(json.dumps(best, indent=2), encoding="utf-8")
    return out_dir


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Walk-forward evaluator for Momentum")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--dataset", default="data/datasets/BTCUSDT_master.csv")
    ap.add_argument("--builder", default="hybrid_100ticks_all")
    ap.add_argument("--out-root", default="runs")
    ap.add_argument("--min-trades-per-fold", type=int, default=80)
    ap.add_argument("--require-non-negative", action="store_true")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = run_walkforward(
        symbol=args.symbol,
        dataset_path=Path(args.dataset),
        builder_name=args.builder,
        out_root=Path(args.out_root),
        guardrails=Guardrails(
            min_trades_per_fold=args.min_trades_per_fold,
            require_non_negative_total=args.require_non_negative,
        ),
    )
    print(json.dumps({"out_dir": str(out_dir)}, indent=2))


if __name__ == "__main__":
    main()

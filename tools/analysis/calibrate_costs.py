"""Calibrate slippage model parameters from costs.csv logs.

Usage:
  source activate.sh
  python tools/analysis/calibrate_costs.py --runs-dir runs/ --symbol BTCUSDT

Outputs:
  - Prints fitted parameters (alpha, beta, gamma)
  - Writes calibration JSON at src/core/execution/slippage_calibration.json
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
from statistics import mean


def load_cost_rows(runs_dir: pathlib.Path, symbol: str | None) -> list[dict]:
    rows: list[dict] = []
    for run in sorted(runs_dir.iterdir()):
        if not run.is_dir():
            continue
        costs_path = run / "costs.csv"
        if not costs_path.exists():
            continue
        with costs_path.open("r") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if symbol and r.get("symbol") and r["symbol"] != symbol:
                    continue
                rows.append(r)
    return rows


def fit_simple_linear(rows: list[dict]) -> dict:
    # slip_bps ≈ alpha*spread_bps + beta*(notional_usd/10000) + gamma
    X_spread: list[float] = []
    X_notional: list[float] = []
    Y_slip: list[float] = []
    for r in rows:
        try:
            spread_bps = float(r.get("spread_bps", 0.0))
            qty = float(r.get("qty", 0.0))
            mid = float(r.get("mid_price", 0.0))
            eff = float(r.get("effective_price", 0.0))
            side = str(r.get("side", "buy")).lower()
        except Exception:
            continue
        if mid <= 0 or qty <= 0:
            continue
        notional = mid * qty
        sign = 1.0 if side == "buy" else -1.0
        slip_bps = ((eff - mid) / mid) * 10000.0 * sign
        X_spread.append(spread_bps)
        X_notional.append(notional / 10000.0)
        Y_slip.append(slip_bps)
    if not Y_slip:
        return {"alpha": 0.0, "beta": 0.0, "gamma": 0.0, "n": 0}

    # Simple averages heuristic: gamma = mean(Y - (a*X1 + b*X2)) with a,b from ratios
    # Compute naive coefficients by proportional relationships to reduce overfit
    # alpha ~ cov(Y, X_spread) / var(X_spread) if var>0
    def _var(xs: list[float]) -> float:
        if not xs:
            return 0.0
        m = mean(xs)
        return mean([(x - m) ** 2 for x in xs])

    def _cov(xs: list[float], ys: list[float]) -> float:
        if not xs or not ys or len(xs) != len(ys):
            return 0.0
        mx, my = mean(xs), mean(ys)
        return mean([(x - mx) * (y - my) for x, y in zip(xs, ys)])

    var_spread = _var(X_spread)
    var_notional = _var(X_notional)
    cov_y_spread = _cov(X_spread, Y_slip)
    cov_y_notional = _cov(X_notional, Y_slip)
    alpha = cov_y_spread / var_spread if var_spread > 0 else 0.0
    beta = cov_y_notional / var_notional if var_notional > 0 else 0.0
    # gamma as mean residual
    residuals = [y - (alpha * xs + beta * xn) for y, xs, xn in zip(Y_slip, X_spread, X_notional)]
    gamma = mean(residuals) if residuals else 0.0
    return {"alpha": alpha, "beta": beta, "gamma": gamma, "n": len(Y_slip)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default="runs/", help="Directory containing run folders")
    ap.add_argument("--symbol", default=None, help="Filter by symbol (e.g., BTCUSDT)")
    args = ap.parse_args()

    runs_dir = pathlib.Path(args.runs_dir)
    rows = load_cost_rows(runs_dir, args.symbol)
    params = fit_simple_linear(rows)

    print("Fitted slippage parameters:")
    print(json.dumps(params, indent=2))

    out_path = pathlib.Path("src/core/execution/slippage_calibration.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(params, f, indent=2)
    print(f"Saved calibration to: {out_path}")


if __name__ == "__main__":
    main()

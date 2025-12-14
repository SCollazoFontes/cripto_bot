#!/usr/bin/env python
"""
Simple wrapper to run instrumented paper trading with costs logging.

Usage:
  python -m tools.live.run_paper_trading --symbol BTCUSDT --duration 60 --cash 1000
"""
import argparse
import asyncio
from datetime import datetime
import pathlib
import sys

# Add parent to path for module imports
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from tools.live.trading_loop import run_live_trading


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper trading with instrumented costs logging")
    ap.add_argument("--symbol", default="BTCUSDT", help="Trading symbol (default: BTCUSDT)")
    ap.add_argument("--duration", type=int, default=60, help="Duration in seconds (default: 60)")
    ap.add_argument("--cash", type=float, default=1000.0, help="Starting cash (default: 1000)")
    ap.add_argument("--fees-bps", type=float, default=4.0, help="Fees in bps (default: 4)")
    ap.add_argument("--slip-bps", type=float, default=None, help="Slippage in bps (None=dynamic)")
    ap.add_argument("--testnet", action="store_true", help="Use testnet")
    ap.add_argument(
        "--strategy", default=None, help="Strategy name (e.g., momentum, vol_breakout, vwap)"
    )
    ap.add_argument("--strategy-params", default=None, help="Strategy params as JSON string")
    ap.add_argument(
        "--bar-tick-limit", type=int, default=None, help="Bar tick limit (default: 100)"
    )
    ap.add_argument(
        "--bar-value-limit", type=float, default=None, help="Bar value limit (default: $50k)"
    )
    ap.add_argument("--bar-policy", default="any", choices=["any", "all"], help="Bar policy")
    ap.add_argument("--run-dir", default=None, help="Output directory for run results")

    args = ap.parse_args()

    # Generate run directory if not provided
    if args.run_dir:
        run_dir = pathlib.Path(args.run_dir)
    else:
        now = datetime.now().strftime("%Y%m%dT%H%M%SZ")
        run_dir = pathlib.Path(__file__).parent.parent.parent / "runs" / now

    run_dir.mkdir(parents=True, exist_ok=True)

    print("📊 Running paper trading session...")
    print(f"   Symbol: {args.symbol}")
    print(f"   Duration: {args.duration}s")
    print(f"   Cash: ${args.cash}")
    print(f"   Fees: {args.fees_bps} bps")
    print(f"   Slippage: {args.slip_bps if args.slip_bps else 'dynamic (from spread)'} bps")
    print(f"   Strategy: {args.strategy or 'None (paper only)'}")
    print(f"   Output: {run_dir}")
    print("-" * 60)

    # Run the trading loop
    asyncio.run(
        run_live_trading(
            symbol=args.symbol,
            run_dir=run_dir,
            duration=args.duration,
            cash=args.cash,
            fees_bps=args.fees_bps,
            slip_bps=args.slip_bps,
            testnet=args.testnet,
            strategy_name=args.strategy,
            strategy_params=args.strategy_params,
            bar_tick_limit=args.bar_tick_limit,
            bar_value_limit=args.bar_value_limit,
            bar_policy=args.bar_policy,
        )
    )


if __name__ == "__main__":
    main()

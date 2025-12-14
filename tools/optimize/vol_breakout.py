from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bars.base import Trade
from bars.builders import CompositeBarBuilder
from strategies.vol_breakout import VolatilityBreakoutStrategy
from tools.optimize.optimizers import Choice, Integer, StepContinuous
from tools.optimize.runner import StrategyTarget, TrialResult


@dataclass
class BrokerParams:
    fees_bps: float = 10.0
    slip_bps: float = 5.0
    starting_cash: float = 100.0


class OptimizerBroker:
    def __init__(self, cfg: BrokerParams) -> None:
        self.cfg = cfg
        self.cash: float = cfg.starting_cash
        self.position_qty: float = 0.0
        self.avg_price: float = 0.0
        self.fees_paid: float = 0.0

    def _apply_slippage(self, price: float, side: str) -> float:
        rate = max(0.0, self.cfg.slip_bps) / 10_000.0
        return price * (1.0 + rate) if side.upper() == "BUY" else price * (1.0 - rate)

    def _fee(self, notional: float) -> float:
        return abs(notional) * (max(0.0, self.cfg.fees_bps) / 10_000.0)

    def submit_order(
        self, symbol: str, side: str, qty: float, price: float, reason: str = ""
    ) -> None:
        _ = symbol, reason
        if qty <= 0.0 or price <= 0.0:
            return
        side = side.upper()
        eff_price = self._apply_slippage(price, side)
        fee = self._fee(eff_price * qty)
        if side == "BUY":
            cost = eff_price * qty + fee
            if cost > self.cash:
                qty = max(0.0, (self.cash - fee) / eff_price)
                cost = eff_price * qty + fee
            if qty <= 0.0:
                return
            total_qty = self.position_qty + qty
            if total_qty > 0:
                self.avg_price = (self.avg_price * self.position_qty + eff_price * qty) / total_qty
            self.position_qty = total_qty
            self.cash -= cost
        else:
            qty = min(qty, self.position_qty)
            if qty <= 0:
                return
            revenue = eff_price * qty - fee
            self.position_qty -= qty
            self.cash += revenue
            if self.position_qty == 0:
                self.avg_price = 0.0
        self.fees_paid += fee

    def equity(self, mark_price: float) -> float:
        return self.cash + self.position_qty * mark_price


class SimExecutor:
    def __init__(self, broker: OptimizerBroker, trade_pnls: list[float]) -> None:
        self.broker = broker
        self.trade_pnls = trade_pnls
        self.current_price: float = 0.0

    def set_price(self, price: float) -> None:
        self.current_price = float(price)

    def market_buy(self, symbol: str, qty: float) -> None:
        self._submit(symbol, "BUY", qty)

    def market_sell(self, symbol: str, qty: float) -> None:
        self._submit(symbol, "SELL", qty)

    def _submit(self, symbol: str, side: str, qty: float) -> None:
        if qty <= 0.0 or self.current_price <= 0.0:
            return
        eq_before = self.broker.equity(self.current_price)
        self.broker.submit_order(symbol, side, qty, self.current_price)
        eq_after = self.broker.equity(self.current_price)
        self.trade_pnls.append(eq_after - eq_before)


def _build_trade_objects(df: pd.DataFrame) -> list[Trade]:
    trades: list[Trade] = []
    for row in df.itertuples(index=False):
        ts = datetime.fromtimestamp(float(row.timestamp), tz=UTC)
        trades.append(
            Trade(
                price=float(row.price),
                qty=float(row.qty),
                timestamp=ts,
                is_buyer_maker=bool(getattr(row, "is_buyer_maker", False)),
            )
        )
    return trades


def _build_bars(df: pd.DataFrame, builder_cfg: dict[str, Any]) -> list[dict[str, float]]:
    builder = CompositeBarBuilder(**builder_cfg)
    bars: list[dict[str, float]] = []
    for trade in _build_trade_objects(df):
        bar = builder.update(trade)
        if bar is None:
            continue
        bars.append(
            {
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
                "start_time": bar.start_time.timestamp(),
                "end_time": bar.end_time.timestamp(),
            }
        )
    return bars


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    merged = dict(params)
    merged["order_notional"] = 5.0
    merged["qty_frac"] = min(1.0, max(0.1, float(merged.get("qty_frac", 1.0))))
    merged["allow_short"] = False
    merged["debug"] = False
    merged["lookback"] = int(max(10, min(200, merged.get("lookback", 40))))
    merged["atr_period"] = int(max(5, min(60, merged.get("atr_period", 14))))
    merged["atr_mult"] = float(min(2.0, max(0.2, merged.get("atr_mult", 0.5))))
    merged["stop_mult"] = float(min(5.0, max(0.5, merged.get("stop_mult", 2.0))))
    return merged


def evaluate_vol_breakout_target(
    params: dict[str, Any],
    window,
    out_dir: Path,
    *,
    symbol: str,
    builder_cfg: dict[str, Any],
    broker_params: BrokerParams,
    min_trades: int,
) -> TrialResult:
    bars = _build_bars(window.data, builder_cfg)
    if len(bars) < 5:
        metrics = {"bars_processed": len(bars), "message": "insufficient bars"}
        (out_dir / "summary.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        return TrialResult(params=params, score=-1e6, metrics=metrics, run_dir=out_dir)

    broker = OptimizerBroker(broker_params)
    trade_pnls: list[float] = []
    executor = SimExecutor(broker, trade_pnls)
    strategy = VolatilityBreakoutStrategy(**_sanitize_params(params))

    equity_curve: list[float] = [broker.equity(bars[0]["close"])]
    for bar in bars:
        executor.set_price(bar["close"])
        strategy.on_bar_live(broker, executor, symbol, bar)
        equity_curve.append(broker.equity(bar["close"]))

    total_return = (equity_curve[-1] / equity_curve[0]) - 1.0 if equity_curve[0] > 0 else 0.0
    metrics = {
        "total_return": total_return,
        "bars_processed": len(bars),
        "trades": len(trade_pnls),
        "equity_final": equity_curve[-1],
    }
    score = total_return
    if metrics["trades"] < min_trades:
        metrics["penalized_reason"] = f"trades<{min_trades}"
        score = -1e6

    summary = {
        "params": params,
        "metrics": metrics,
        "window": window.label,
        "start_ts": window.start_ts,
        "end_ts": window.end_ts,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return TrialResult(params=params, score=score, metrics=metrics, run_dir=out_dir)


def build_vol_breakout_target(
    symbol: str,
    builder_cfg: dict[str, Any],
    broker_params: BrokerParams | None = None,
    *,
    grid_mode: bool = False,
    min_trades: int = 1,
) -> StrategyTarget:
    broker_params = broker_params or BrokerParams()
    if grid_mode:
        space = {
            "lookback": Choice([10, 15, 20, 30, 40, 60, 80, 120, 160, 200]),
            "atr_period": Choice([5, 8, 10, 14, 20, 30, 45, 60]),
            "atr_mult": Choice([0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0]),
            "stop_mult": Choice([0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]),
            "qty_frac": Choice([0.2, 0.4, 0.6, 0.8, 1.0]),
        }
    else:
        space = {
            "lookback": Integer(10, 200, step=2),
            "atr_period": Integer(5, 60, step=1),
            "atr_mult": StepContinuous(0.2, 2.0, step=0.1),
            "stop_mult": StepContinuous(0.5, 5.0, step=0.1),
            "qty_frac": StepContinuous(0.2, 1.0, step=0.05),
        }

    def _eval(params: dict[str, Any], window, out_dir: Path) -> TrialResult:
        return evaluate_vol_breakout_target(
            params,
            window,
            out_dir,
            symbol=symbol,
            builder_cfg=builder_cfg,
            broker_params=broker_params,
            min_trades=min_trades,
        )

    return StrategyTarget(
        name="vol_breakout",
        space=space,
        evaluation_fn=_eval,
        preprocess_params=_sanitize_params,
    )

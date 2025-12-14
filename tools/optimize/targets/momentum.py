# tools/optimize/momentum.py
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
from strategies.momentum import MomentumStrategy
from tools.optimize.optimizers import Choice, Integer, StepContinuous
from tools.optimize.runner import StrategyTarget, TrialResult

ENTRY_STEP = 1e-4
EXIT_STEP = 1e-4
VOL_STEP = 1e-4
STOP_STEP = 2e-3
TAKE_STEP = 5e-3


@dataclass
class BrokerParams:
    fees_bps: float = 10.0
    slip_bps: float = 5.0
    starting_cash: float = 100.0


class OptimizerBroker:
    """Broker mínimo para simulaciones rápidas dentro del optimizador."""

    def __init__(self, cfg: BrokerParams) -> None:
        self.cfg = cfg
        self.cash: float = cfg.starting_cash
        self.position_qty: float = 0.0
        self.avg_price: float = 0.0
        self.fees_paid: float = 0.0
        # Contexto dinámico para slippage
        self._ctx_volatility: float = 0.0

    def set_context(self, *, volatility: float | None = None) -> None:
        """Actualiza contexto de mercado (volatilidad) para slippage dinámico."""
        if volatility is not None:
            self._ctx_volatility = max(0.0, float(volatility))

    def _apply_slippage(self, price: float, side: str, qty: float) -> float:
        # Base: slip fijo en bps
        base_rate = max(0.0, self.cfg.slip_bps) / 10_000.0
        # Dinámico: aumenta con volatilidad y tamaño de orden (notional)
        # - alpha pondera impacto de volatilidad en el rate
        # - beta pondera el notional relativo (escala 10k USD)
        alpha = 1.5  # sensibilidad a volatilidad
        beta = 2e-5  # sensibilidad a tamaño
        notional = price * max(0.0, qty)
        size_term = beta * min(5.0, notional / 10_000.0)  # cap en 5x para evitar excesos
        dyn_rate = base_rate + alpha * self._ctx_volatility + size_term
        # Limitar a un máximo razonable (80 bps)
        rate = min(dyn_rate, 0.008)
        if side.upper() == "BUY":
            return price * (1.0 + rate)
        return price * (1.0 - rate)

    def _fee(self, notional: float) -> float:
        return abs(notional) * (max(0.0, self.cfg.fees_bps) / 10_000.0)

    def submit_order(
        self, symbol: str, side: str, qty: float, price: float, reason: str = ""
    ) -> tuple[float, float, float]:
        """Envía orden y devuelve (qty_ejec, precio_ejec, fee).

        Se mantiene lógica previa pero devolviendo datos para poder loguear trades.
        """
        _ = symbol, reason  # no-op, mantenemos firma compatible
        if qty <= 0.0 or price <= 0.0:
            return 0.0, 0.0, 0.0
        side = side.upper()
        eff_price = self._apply_slippage(price, side, qty)
        fee = self._fee(eff_price * qty)
        if side == "BUY":
            cost = eff_price * qty + fee
            if cost > self.cash:
                qty = max(0.0, (self.cash - fee) / eff_price)
                cost = eff_price * qty + fee
            if qty <= 0:
                return 0.0, 0.0, 0.0
            total_qty = self.position_qty + qty
            if total_qty > 0:
                self.avg_price = (self.avg_price * self.position_qty + eff_price * qty) / total_qty
            self.position_qty = total_qty
            self.cash -= cost
        else:
            qty = min(qty, self.position_qty)
            if qty <= 0:
                return 0.0, 0.0, 0.0
            revenue = eff_price * qty - fee
            self.position_qty -= qty
            self.cash += revenue
            if self.position_qty == 0:
                self.avg_price = 0.0
        self.fees_paid += fee
        return qty, eff_price, fee

    def equity(self, mark_price: float) -> float:
        return self.cash + self.position_qty * mark_price


class SimExecutor:
    """Executor que traduce market_buy/sell en órdenes para OptimizerBroker."""

    def __init__(
        self, broker: OptimizerBroker, trade_pnls: list[float], trade_rows: list[dict]
    ) -> None:
        self.broker = broker
        self.trade_pnls = trade_pnls
        self.trade_rows = trade_rows
        self.current_price: float = 0.0
        self.current_vol: float = 0.0

    def set_price(self, price: float) -> None:
        self.current_price = float(price)

    def set_context(self, *, volatility: float | None = None) -> None:
        if volatility is not None:
            self.current_vol = max(0.0, float(volatility))
            # Propagar al broker para cálculo de slippage
            self.broker.set_context(volatility=self.current_vol)

    def market_buy(self, symbol: str, qty: float) -> None:
        self._submit(symbol, "BUY", qty)

    def market_sell(self, symbol: str, qty: float) -> None:
        self._submit(symbol, "SELL", qty)

    def _submit(self, symbol: str, side: str, qty: float) -> None:
        if qty <= 0.0 or self.current_price <= 0.0:
            return
        eq_before = self.broker.equity(self.current_price)
        exec_qty, exec_price, fee = self.broker.submit_order(symbol, side, qty, self.current_price)
        eq_after = self.broker.equity(self.current_price)
        self.trade_pnls.append(eq_after - eq_before)
        if exec_qty > 0:
            self.trade_rows.append(
                {
                    "t": int(self.current_ts_ms),
                    "side": side,
                    "price": exec_price,
                    "qty": exec_qty,
                    "cash": self.broker.cash,
                    "equity": eq_after,
                    "fee": fee,
                    "reason": "",
                }
            )

    @property
    def current_ts_ms(self) -> float:
        # Usamos end_time (segundos) y lo pasamos a ms para equity/trades.
        return getattr(self, "_current_ts", 0.0) * 1000.0

    def set_time(self, ts_seconds: float) -> None:
        self._current_ts = float(ts_seconds)


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
    # Allow order_notional to be configured from params
    if "order_notional" not in merged:
        merged["order_notional"] = 5.0
    merged["qty_frac"] = min(1.0, float(merged.get("qty_frac", 1.0)))
    merged["allow_short"] = False
    merged["debug"] = False

    def _round_step(val: float, step: float, lo: float, hi: float) -> float:
        clipped = max(lo, min(hi, val))
        return round(clipped / step) * step

    merged["entry_threshold"] = _round_step(
        float(merged.get("entry_threshold", ENTRY_STEP)), ENTRY_STEP, 0.0003, 0.002
    )
    merged["exit_threshold"] = _round_step(
        float(merged.get("exit_threshold", EXIT_STEP)), EXIT_STEP, 0.0001, 0.0015
    )

    # Enforce exit_threshold <= entry_threshold
    if merged["exit_threshold"] > merged["entry_threshold"]:
        merged["exit_threshold"] = merged["entry_threshold"] * 0.7  # 70% of entry

    merged["stop_loss_pct"] = _round_step(
        float(merged.get("stop_loss_pct", 0.02)), STOP_STEP, 0.01, 0.05
    )
    merged["take_profit_pct"] = _round_step(
        float(merged.get("take_profit_pct", 0.04)), TAKE_STEP, 0.02, 0.08
    )

    # Enforce take_profit_pct >= stop_loss_pct
    if merged["take_profit_pct"] < merged["stop_loss_pct"]:
        merged["take_profit_pct"] = merged["stop_loss_pct"] * 1.5  # 1.5x SL

    merged["min_volatility"] = _round_step(
        float(merged.get("min_volatility", 0.0002)), VOL_STEP, 0.0001, 0.001
    )
    merged["max_volatility"] = _round_step(
        float(merged.get("max_volatility", 0.06)), 0.001, 0.03, 0.12
    )

    # Enforce min_volatility < max_volatility
    if merged["min_volatility"] >= merged["max_volatility"]:
        merged["max_volatility"] = merged["min_volatility"] * 10.0  # 10x min

    merged["lookback_ticks"] = int(max(10, min(130, merged.get("lookback_ticks", 40))))
    merged["cooldown_bars"] = int(max(0, min(6, merged.get("cooldown_bars", 2))))
    merged["volatility_window"] = int(max(30, min(150, merged.get("volatility_window", 80))))
    return merged


def evaluate_momentum_target(
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
    trade_rows: list[dict[str, Any]] = []
    executor = SimExecutor(broker, trade_pnls, trade_rows)
    strategy = MomentumStrategy(**_sanitize_params(params))

    # Prepara cálculo de volatilidad de barra para slippage dinámico (similar al de la estrategia)
    from collections import deque

    vol_win = int(_sanitize_params(params).get("volatility_window", 50))
    price_window: deque[float] = deque(maxlen=max(2, vol_win))

    equity_curve: list[float] = [broker.equity(bars[0]["close"])]
    equity_rows: list[dict[str, float]] = []
    for bar in bars:
        executor.set_price(bar["close"])
        executor.set_time(bar.get("end_time", bar.get("start_time", 0.0)))
        # Actualizar contexto de volatilidad para este bar
        price_window.append(float(bar["close"]))
        if len(price_window) >= 2:
            prices = list(price_window)
            rets = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
            vol = var_r**0.5
        else:
            vol = 0.0
        executor.set_context(volatility=vol)
        strategy.on_bar_live(broker, executor, symbol, bar)
        equity_curve.append(broker.equity(bar["close"]))
        equity_rows.append(
            {
                "t": int(executor.current_ts_ms),
                "price": float(bar["close"]),
                "qty": float(broker.position_qty),
                "cash": float(broker.cash),
                "equity": float(equity_curve[-1]),
            }
        )

    # Métricas básicas - usar último cierre de posición, no final del dataset
    last_close_idx = min(strategy._last_trade_close_bar, len(equity_curve) - 1)
    if last_close_idx > 0:
        equity_at_close = equity_curve[last_close_idx]
        equity_start = equity_curve[0]
    else:
        equity_at_close = equity_curve[-1]
        equity_start = equity_curve[0]

    returns = []
    for i in range(1, last_close_idx + 1):
        prev = equity_curve[i - 1]
        curr = equity_curve[i]
        returns.append((curr - prev) / prev if prev else 0.0)
    total_return = (equity_at_close / equity_start) - 1.0 if equity_start else 0.0

    def _to_native(obj: Any):
        try:
            import numpy as np  # local import
        except ImportError:
            np = None
        if np is not None:
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
        return obj

    metrics = {
        "total_return": float(total_return),
        "bars_processed": int(len(bars)),
        "trades": int(len(trade_pnls)),
        "equity_final": float(equity_at_close),
        "last_trade_close_bar": int(strategy._last_trade_close_bar),
    }
    score = metrics["total_return"]
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
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=_to_native), encoding="utf-8"
    )

    # Persistir equity/trades para análisis posterior (metrics_compare, etc.)
    try:
        if equity_rows:
            import pandas as _pd

            _pd.DataFrame(equity_rows).to_csv(out_dir / "equity.csv", index=False)
        if trade_rows:
            import pandas as _pd

            _pd.DataFrame(trade_rows).to_csv(out_dir / "trades.csv", index=False)
    except Exception:
        pass

    return TrialResult(params=params, score=score, metrics=metrics, run_dir=out_dir)


def build_momentum_target(
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
            "lookback_ticks": Choice([10, 16, 24, 32, 48, 64, 80, 96, 110, 130]),
            "entry_threshold": Choice(
                [0.0003, 0.0005, 0.0008, 0.001, 0.0013, 0.0016, 0.0018, 0.002]
            ),
            "exit_threshold": Choice(
                [0.0001, 0.0002, 0.0004, 0.0006, 0.0008, 0.001, 0.0012, 0.0014]
            ),
            "stop_loss_pct": Choice([0.01, 0.02, 0.03, 0.04, 0.05]),
            "take_profit_pct": Choice([0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]),
            "cooldown_bars": Choice([0, 1, 2, 3, 4, 5, 6]),
            "volatility_window": Choice([30, 40, 60, 80, 100, 120, 150]),
            "min_volatility": Choice([0.0001, 0.0002, 0.0004, 0.0006, 0.0008, 0.001]),
            "max_volatility": Choice([0.03, 0.04, 0.05, 0.06, 0.08, 0.1, 0.12]),
            "trend_confirmation": Choice([True, False]),
        }
    else:
        space = {
            "lookback_ticks": Integer(10, 130, step=2),
            "entry_threshold": StepContinuous(0.0003, 0.002, step=ENTRY_STEP),
            "exit_threshold": StepContinuous(0.0001, 0.0015, step=EXIT_STEP),
            "stop_loss_pct": StepContinuous(0.01, 0.05, step=STOP_STEP),
            "take_profit_pct": StepContinuous(0.02, 0.08, step=TAKE_STEP),
            "cooldown_bars": Integer(0, 6),
            "volatility_window": Integer(30, 150, step=5),
            "min_volatility": StepContinuous(0.0001, 0.001, step=VOL_STEP),
            "max_volatility": StepContinuous(0.03, 0.12, step=0.001),
            "trend_confirmation": Choice([True, False]),
        }

    def _eval(params: dict[str, Any], window, out_dir: Path) -> TrialResult:
        return evaluate_momentum_target(
            params,
            window,
            out_dir,
            symbol=symbol,
            builder_cfg=builder_cfg,
            broker_params=broker_params,
            min_trades=min_trades,
        )

    return StrategyTarget(
        name="momentum",
        space=space,
        evaluation_fn=_eval,
        preprocess_params=_sanitize_params,
    )

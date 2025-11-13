"""
Core metrics package.

This package splits lightweight telemetry (latencies, bars/sec, timers)
and trading performance metrics (Sharpe, Sortino, drawdown, etc.) into
separate modules while preserving the original public API.

Backward compatibility: All previously available symbols from
`core.metrics` are re-exported here.
"""

from __future__ import annotations

from .performance import (
    calculate_all_metrics,
    calculate_avg_win_loss,
    calculate_max_drawdown,
    calculate_profit_factor,
    calculate_returns,
    calculate_sharpe,
    calculate_sortino,
    calculate_win_rate,
)
from .telemetry import (
    EWMA,
    BarsPerSecond,
    BarsRateSnapshot,
    BlockTimer,
    LatencySnapshot,
    LatencyStats,
)

__all__ = [
    # Telemetry
    "LatencyStats",
    "LatencySnapshot",
    "BarsPerSecond",
    "BarsRateSnapshot",
    "BlockTimer",
    "EWMA",
    # Performance metrics
    "calculate_returns",
    "calculate_sharpe",
    "calculate_sortino",
    "calculate_max_drawdown",
    "calculate_profit_factor",
    "calculate_win_rate",
    "calculate_avg_win_loss",
    "calculate_all_metrics",
]

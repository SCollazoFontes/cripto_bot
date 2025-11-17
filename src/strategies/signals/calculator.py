"""Main signal calculator dispatcher."""

from __future__ import annotations

from typing import Any

import pandas as pd

from strategies.signals.momentum import calculate_momentum_signal
from strategies.signals.vol_breakout import calculate_vol_breakout_signal
from strategies.signals.vwap_reversion import calculate_vwap_reversion_signal


def calculate_signal(
    strategy_name: str,
    df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calculate signal for any strategy.

    Args:
        strategy_name: Strategy name
        df: OHLCV dataframe
        params: Strategy parameters (optional)

    Returns:
        (signal_value, zone_text, metadata)
        signal_value: -1.0 (strong sell) to +1.0 (strong buy)
        zone_text: Textual zone description
        metadata: Calculation details
    """
    params = params or {}

    if strategy_name == "momentum":
        return calculate_momentum_signal(
            df,
            lookback_ticks=params.get("lookback_ticks", 12),
            entry_threshold=params.get("entry_threshold", 0.0002),
            exit_threshold=params.get("exit_threshold", 0.00015),
            min_volatility=params.get("min_volatility", 0.0001),
            max_volatility=params.get("max_volatility", 0.025),
            volatility_window=params.get("volatility_window", 50),
        )
    elif strategy_name == "vwap_reversion":
        return calculate_vwap_reversion_signal(
            df,
            vwap_window=params.get("vwap_window", 50),
            z_entry=params.get("z_entry", 1.5),
            z_exit=params.get("z_exit", 0.5),
        )
    elif strategy_name == "vol_breakout":
        return calculate_vol_breakout_signal(
            df,
            lookback=params.get("lookback", 20),
            atr_period=params.get("atr_period", 14),
            atr_mult=params.get("atr_mult", 0.5),
        )
    else:
        return 0.0, "UNKNOWN", {"reason": f"unknown strategy: {strategy_name}"}

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
        # Aliases for CLI params (backward compatible)
        lookback = params.get("lookback_ticks") or params.get("lookback")
        entry_thr = params.get("entry_threshold") or params.get("threshold")
        exit_thr = params.get("exit_threshold") or params.get("exit")

        return calculate_momentum_signal(
            df,
            lookback_ticks=lookback if lookback is not None else 50,
            entry_threshold=entry_thr if entry_thr is not None else 0.0011,
            exit_threshold=exit_thr if exit_thr is not None else 0.0008,
            min_volatility=params.get("min_volatility", 0.0003),
            max_volatility=params.get("max_volatility", 0.015),
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

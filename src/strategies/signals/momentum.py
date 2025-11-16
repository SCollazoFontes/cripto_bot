"""Momentum strategy signal calculator."""

from __future__ import annotations

from typing import Any

import pandas as pd

from strategies.signals.utils import classify_signal_zone, linear_scale


def calculate_momentum_signal(
    df: pd.DataFrame,
    lookback_ticks: int = 10,
    entry_threshold: float = 0.001,
    exit_threshold: float = 0.0005,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calculate simple momentum signal.

    Args:
        df: OHLCV dataframe
        lookback_ticks: Number of bars for momentum calculation
        entry_threshold: Entry momentum threshold
        exit_threshold: Exit momentum threshold (unused in signal calculation)

    Returns:
        (signal_value, zone_text, metadata)
        signal_value: -1.0 to +1.0
        zone_text: "BUY", "NEUTRAL", "SELL"
        metadata: Calculation details
    """
    if df.empty or len(df) < lookback_ticks:
        return 0.0, "NEUTRAL", {"reason": "insufficient data"}

    current_price = df["close"].iloc[-1]
    recent_prices = df["close"].tail(lookback_ticks)
    mean_price = recent_prices.mean()

    if mean_price <= 0:
        return 0.0, "NEUTRAL", {"reason": "invalid mean price"}

    # Momentum = (price - mean) / mean
    momentum = (current_price - mean_price) / mean_price

    # Scale linearly to [-1, +1] using entry_threshold as reference
    signal = linear_scale(momentum, entry_threshold, max_multiplier=3.0)
    zone = classify_signal_zone(signal)

    metadata = {
        "momentum": momentum,
        "mean_price": mean_price,
        "current_price": current_price,
        "lookback": lookback_ticks,
    }

    return signal, zone, metadata

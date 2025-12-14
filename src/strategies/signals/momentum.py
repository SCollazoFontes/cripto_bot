"""Momentum strategy signal calculator (con filtros avanzados)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from strategies.signals.utils import classify_signal_zone, linear_scale


def calculate_momentum_signal(
    df: pd.DataFrame,
    lookback_ticks: int = 12,
    entry_threshold: float = 0.0002,
    exit_threshold: float = 0.00015,
    min_volatility: float = 0.00001,  # Lowered for testnet compatibility
    max_volatility: float = 0.025,
    volatility_window: int = 50,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calculate momentum signal with volatility/trend filters (ex V2).

    Returns (signal_value, zone_text, metadata)
    """
    # Require minimum bars for signal calculation (adapt to available history)
    min_bars = 10  # Absolute minimum for meaningful calculation
    if df.empty or len(df) < min_bars:
        return 0.0, "NEUTRAL", {"reason": "insufficient data"}

    # Use available lookback capped to actual bars
    actual_lookback = min(lookback_ticks, max(len(df) - 5, 5))

    current_price = df["close"].iloc[-1]
    recent_prices = df["close"].tail(actual_lookback)
    mean_price = recent_prices.mean()

    if mean_price <= 0:
        return 0.0, "NEUTRAL", {"reason": "invalid mean price"}

    momentum = (current_price - mean_price) / mean_price

    prices_array = df["close"].tail(min(volatility_window, len(df))).values
    if len(prices_array) < 2:
        volatility = 0.0
    else:
        returns = np.diff(prices_array) / prices_array[:-1]
        volatility = np.std(returns) if len(returns) > 0 else 0.0

    # Volatility filters (return signal 0 but with full metadata)
    vol_filtered = False
    if volatility < min_volatility:
        vol_filtered = True
    if volatility > max_volatility:
        vol_filtered = True

    # Trend confirmation: use double window if available (else entire dataset)
    long_window = min(actual_lookback * 2, len(df))
    if long_window > actual_lookback + 2 and len(df) > actual_lookback:
        long_mean = df["close"].tail(long_window).mean()
        trend_confirmed = (mean_price > long_mean) if momentum > 0 else (mean_price < long_mean)
    else:
        trend_confirmed = True

    # Always calculate signal, but may be neutralized if vol filtered
    signal = (
        linear_scale(momentum, entry_threshold, max_multiplier=4.0) if not vol_filtered else 0.0
    )

    if vol_filtered:
        if volatility < min_volatility:
            zone = "LOW VOL"
        else:
            zone = "HIGH VOL"
    else:
        zone = classify_signal_zone(signal)

    if not trend_confirmed and abs(signal) >= 0.5:
        signal *= 0.5
        zone = "NEUTRAL"

    metadata = {
        "momentum": momentum,
        "volatility": volatility,
        "trend_confirmed": trend_confirmed,
        "mean_price": mean_price,
        "entry_threshold": entry_threshold,
        "exit_threshold": exit_threshold,
        "vol_filtered": vol_filtered,
    }

    return signal, zone, metadata

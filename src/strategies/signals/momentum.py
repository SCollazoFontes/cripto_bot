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
    min_volatility: float = 0.0001,
    max_volatility: float = 0.025,
    volatility_window: int = 50,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calculate momentum signal with volatility/trend filters (ex V2).

    Returns (signal_value, zone_text, metadata)
    """
    if df.empty or len(df) < lookback_ticks * 2:
        return 0.0, "NEUTRAL", {"reason": "insufficient data"}

    current_price = df["close"].iloc[-1]
    recent_prices = df["close"].tail(lookback_ticks)
    mean_price = recent_prices.mean()

    if mean_price <= 0:
        return 0.0, "NEUTRAL", {"reason": "invalid mean price"}

    momentum = (current_price - mean_price) / mean_price

    prices_array = df["close"].tail(max(volatility_window, lookback_ticks * 2)).values
    if len(prices_array) < 2:
        volatility = 0.0
    else:
        returns = np.diff(prices_array) / prices_array[:-1]
        volatility = np.std(returns) if len(returns) > 0 else 0.0

    if volatility < min_volatility:
        return 0.0, "LOW VOL", {"volatility": volatility, "reason": "volatility too low"}
    if volatility > max_volatility:
        return 0.0, "HIGH VOL", {"volatility": volatility, "reason": "volatility too high"}

    if len(df) >= lookback_ticks * 2:
        long_mean = df["close"].tail(lookback_ticks * 2).mean()
        trend_confirmed = (mean_price > long_mean) if momentum > 0 else (mean_price < long_mean)
    else:
        trend_confirmed = True

    signal = linear_scale(momentum, entry_threshold, max_multiplier=4.0)
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
    }

    return signal, zone, metadata

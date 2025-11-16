"""Volatility Breakout strategy signal calculator."""

from __future__ import annotations

from typing import Any

import pandas as pd


def calculate_vol_breakout_signal(
    df: pd.DataFrame,
    lookback: int = 20,
    atr_period: int = 14,
    atr_mult: float = 0.5,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calculate volatility breakout signal.

    Args:
        df: OHLCV dataframe
        lookback: Channel lookback period
        atr_period: ATR calculation period
        atr_mult: ATR multiplier for bands

    Returns:
        (signal_value, zone_text, metadata)
    """
    if df.empty or len(df) < max(lookback, atr_period):
        return 0.0, "NEUTRAL", {"reason": "insufficient data"}

    recent_df = df.tail(max(lookback, atr_period)).copy()

    # Calculate channel (high/low of last N periods)
    channel_high = recent_df["high"].tail(lookback).max()
    channel_low = recent_df["low"].tail(lookback).min()

    # Calculate ATR (average true range)
    recent_df["tr"] = recent_df.apply(
        lambda row: max(
            row["high"] - row["low"],
            abs(row["high"] - row.get("prev_close", row["close"])),
            abs(row["low"] - row.get("prev_close", row["close"])),
        ),
        axis=1,
    )
    atr = recent_df["tr"].tail(atr_period).mean()

    current_price = df["close"].iloc[-1]

    # Calculate distance to bands in ATR multiples
    upper_band = channel_high + atr_mult * atr
    lower_band = channel_low - atr_mult * atr

    # Breakout signal:
    # Scale from -1 (far below channel) to +1 (far above channel)

    if current_price > upper_band + atr * 2:
        signal = 1.0
        zone = "BUY"
    elif current_price < lower_band - atr * 2:
        signal = -1.0
        zone = "SELL"
    elif current_price > upper_band:
        # Bullish breakout: scale from 0.5 to 1.0
        distance = min(atr * 2, current_price - upper_band)
        signal = 0.5 + (distance / (atr * 2)) * 0.5
        zone = "BUY"
    elif current_price < lower_band:
        # Bearish breakout: scale from -0.5 to -1.0
        distance = min(atr * 2, lower_band - current_price)
        signal = -0.5 - (distance / (atr * 2)) * 0.5
        zone = "SELL"
    else:
        # Inside channel
        channel_range = channel_high - channel_low
        if channel_range > 0:
            position = (current_price - channel_low) / channel_range
            signal = (position - 0.5) * 1.0  # -0.5 to +0.5
        else:
            signal = 0.0
        zone = "NEUTRAL"

    metadata = {
        "channel_high": channel_high,
        "channel_low": channel_low,
        "atr": atr,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "current_price": current_price,
    }

    return signal, zone, metadata

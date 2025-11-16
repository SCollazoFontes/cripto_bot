"""VWAP Reversion strategy signal calculator."""

from __future__ import annotations

from typing import Any

import pandas as pd

from strategies.signals.utils import classify_signal_zone


def calculate_vwap_reversion_signal(
    df: pd.DataFrame,
    vwap_window: int = 50,
    z_entry: float = 1.5,
    z_exit: float = 0.5,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calculate VWAP mean reversion signal.

    Args:
        df: OHLCV dataframe
        vwap_window: Window for VWAP calculation
        z_entry: Z-score entry threshold
        z_exit: Z-score exit threshold (unused)

    Returns:
        (signal_value, zone_text, metadata)

    Note:
        High z-score → price too high → SELL signal (expect reversion down)
        Low z-score → price too low → BUY signal (expect reversion up)
    """
    if df.empty or len(df) < vwap_window:
        return 0.0, "NEUTRAL", {"reason": "insufficient data"}

    # Calculate VWAP
    recent_df = df.tail(vwap_window).copy()
    recent_df["pv"] = recent_df["close"] * recent_df["volume"]
    vwap = recent_df["pv"].sum() / recent_df["volume"].sum()

    # Calculate z-score (deviation in std dev units)
    prices = recent_df["close"].values
    price_mean = prices.mean()
    price_std = prices.std()

    current_price = df["close"].iloc[-1]

    if price_std <= 0 or vwap <= 0:
        return 0.0, "NEUTRAL", {"reason": "invalid std or vwap"}

    z_score = (current_price - price_mean) / price_std

    # Mean reversion signal:
    # z > +z_entry → price VERY high → SELL (expect reversion down)
    # z < -z_entry → price VERY low → BUY (expect reversion up)
    # Scale linearly to [-1, +1]

    max_z = z_entry * 2

    if z_score >= max_z:
        signal = -1.0  # SELL (price too high)
        zone = "SELL"
    elif z_score <= -max_z:
        signal = 1.0  # BUY (price too low)
        zone = "BUY"
    else:
        # Scale linearly, inverted (high z → sell)
        signal = -z_score / max_z
        zone = classify_signal_zone(signal)

    metadata = {
        "z_score": z_score,
        "vwap": vwap,
        "price_mean": price_mean,
        "price_std": price_std,
        "current_price": current_price,
    }

    return signal, zone, metadata

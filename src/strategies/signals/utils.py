"""Utility functions for signal calculations."""

from __future__ import annotations

import pandas as pd


def validate_dataframe(df: pd.DataFrame, min_rows: int) -> tuple[bool, str]:
    """
    Validate that dataframe has sufficient data.

    Args:
        df: Input dataframe
        min_rows: Minimum required rows

    Returns:
        (is_valid, error_message)
    """
    if df.empty:
        return False, "empty dataframe"

    if len(df) < min_rows:
        return False, f"insufficient data (need {min_rows}, got {len(df)})"

    return True, ""


def classify_signal_zone(signal: float) -> str:
    """
    Classify signal value into zone.

    Args:
        signal: Signal value in [-1.0, +1.0]

    Returns:
        Zone name: "BUY", "SELL", or "NEUTRAL"
    """
    if signal >= 0.5:
        return "BUY"
    elif signal <= -0.5:
        return "SELL"
    else:
        return "NEUTRAL"


def linear_scale(value: float, threshold: float, max_multiplier: float = 3.0) -> float:
    """
    Scale a value linearly to [-1, +1] range.

    Args:
        value: Input value
        threshold: Reference threshold
        max_multiplier: How many thresholds = max signal

    Returns:
        Scaled value in [-1, +1]
    """
    max_value = threshold * max_multiplier

    if value >= max_value:
        return 1.0
    elif value <= -max_value:
        return -1.0
    else:
        return value / max_value

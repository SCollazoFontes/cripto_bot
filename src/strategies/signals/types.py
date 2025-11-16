"""Common types for signal calculations."""

from __future__ import annotations

from typing import Any, TypedDict


class SignalResult(TypedDict):
    """Result from signal calculation."""

    value: float  # -1.0 to +1.0
    zone: str  # "BUY", "SELL", "NEUTRAL", etc.
    metadata: dict[str, Any]  # Strategy-specific details

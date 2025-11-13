"""
Unified storage package.

This package separates data record models and the SQLite-backed storage
implementation while preserving the original API that existed at
`src.data.storage`.

Public API preserved:
- TradeRecord, BarRecord, FeatureRecord, SignalRecord, EquityRecord
- DataStorage (SQLite-backed)
"""

from __future__ import annotations

from .records import (
    BarRecord,
    EquityRecord,
    FeatureRecord,
    SignalRecord,
    TradeRecord,
)
from .sqlite_backend import DataStorage

__all__ = [
    "TradeRecord",
    "BarRecord",
    "FeatureRecord",
    "SignalRecord",
    "EquityRecord",
    "DataStorage",
]

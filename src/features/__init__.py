# src/features/__init__.py
"""
Feature engineering para trading strategies.

Módulos:
- technical_indicators: Indicadores técnicos (SMA, RSI, Bollinger, etc.)
"""

from .technical_indicators import (
    SupportResistanceDetector,
    TechnicalIndicators,
    calculate_features_batch,
)

__all__ = [
    "TechnicalIndicators",
    "SupportResistanceDetector",
    "calculate_features_batch",
]

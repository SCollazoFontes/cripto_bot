# src/strategies/vol_breakout/__init__.py
"""
Volume Breakout Strategy - Modular implementation.
"""

from strategies.vol_breakout.strategy import VolatilityBreakoutStrategy

# Alias para compatibilidad
VolBreakoutStrategy = VolatilityBreakoutStrategy

__all__ = ["VolatilityBreakoutStrategy", "VolBreakoutStrategy"]

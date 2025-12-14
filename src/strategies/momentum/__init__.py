# src/strategies/momentum/__init__.py
"""
Momentum Strategy - Modular implementation.

Estructura:
- strategy.py: Clase principal MomentumStrategy
- signals.py: Cálculo de señales y métricas
- config.py: Validación y configuración de parámetros
"""

from strategies.momentum.strategy import MomentumStrategy

__all__ = ["MomentumStrategy"]

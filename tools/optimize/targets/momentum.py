# tools/optimize/targets/momentum.py
"""
Optimizador específico para MomentumStrategy.

Define el espacio de búsqueda y lógica de creación de estrategia.
La simulación y métricas se delegan a BaseStrategyOptimizer.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from strategies.base import Strategy
from strategies.momentum import MomentumStrategy
from tools.optimize.base import BaseStrategyOptimizer
from tools.optimize.optimizers import Integer, StepContinuous

# Steps para búsqueda continua
ENTRY_STEP = 1e-4
EXIT_STEP = 1e-4
VOL_STEP = 1e-4
STOP_STEP = 2e-3
TAKE_STEP = 5e-3


class MomentumOptimizer(BaseStrategyOptimizer):
    """
    Optimizador especializado para MomentumStrategy.

    Espacio de búsqueda basado en optimizaciones previas (BTCUSDT 25 días):
    - lookback_ticks: 20-80 (óptimo ~50-60)
    - entry_threshold: 0.0008-0.003 (óptimo ~0.0011)
    - exit_threshold: 0.0003-0.0015 (óptimo ~0.0008)
    - stop_loss/take_profit: balanceados para ratio 1:2
    - min_profit_bps: 40-100 (cubre costes + margen)

    Uso:
        optimizer = MomentumOptimizer(symbol="BTCUSDT")
        result = optimizer.evaluate(params, trades_df, builder_config)
    """

    @property
    def search_space(self) -> dict[str, Any]:
        return {
            # Core momentum params
            "lookback_ticks": Integer(min=20, max=80, step=5),
            "entry_threshold": StepContinuous(min=0.0008, max=0.003, step=ENTRY_STEP),
            "exit_threshold": StepContinuous(min=0.0003, max=0.0015, step=EXIT_STEP),
            # Risk management
            "stop_loss_pct": StepContinuous(min=0.005, max=0.020, step=STOP_STEP),
            "take_profit_pct": StepContinuous(min=0.010, max=0.040, step=TAKE_STEP),
            # Volatility filters
            "volatility_window": Integer(min=20, max=80, step=10),
            "min_volatility": StepContinuous(min=0.0001, max=0.0005, step=VOL_STEP),
            "max_volatility": StepContinuous(min=0.010, max=0.030, step=VOL_STEP * 10),
            # Profit protection
            "min_profit_bps": StepContinuous(min=40.0, max=100.0, step=10.0),
            # Cooldown
            "cooldown_bars": Integer(min=1, max=10, step=1),
        }

    def create_strategy(self, params: dict[str, Any]) -> Strategy:
        """
        Crea instancia de MomentumStrategy con params dados.

        Nota: No pasamos cost_model aquí porque el broker del optimizador
        maneja costes. La estrategia solo genera señales.
        """
        return MomentumStrategy(
            lookback_ticks=params.get("lookback_ticks", 50),
            entry_threshold=params.get("entry_threshold", 0.0011),
            exit_threshold=params.get("exit_threshold", 0.0008),
            stop_loss_pct=params.get("stop_loss_pct", 0.008),
            take_profit_pct=params.get("take_profit_pct", 0.015),
            volatility_window=params.get("volatility_window", 50),
            min_volatility=params.get("min_volatility", 0.0003),
            max_volatility=params.get("max_volatility", 0.015),
            cooldown_bars=params.get("cooldown_bars", 3),
            min_profit_bps=params.get("min_profit_bps", 60.0),
            # Features dinámicas (desactivadas por defecto)
            use_dynamic_sl=params.get("use_dynamic_sl", False),
            use_dynamic_tp=params.get("use_dynamic_tp", False),
            use_dynamic_entry=params.get("use_dynamic_entry", False),
            # Qty management
            qty_frac=1.0,
            order_notional=5.0,
        )


# Alias para compatibilidad con runner antiguo
def get_momentum_target() -> dict[str, Any]:
    """
    Devuelve configuración de target para sistema de optimización legacy.

    Returns:
        dict con 'search_space' y 'optimizer_class'
    """
    optimizer = MomentumOptimizer()
    return {
        "search_space": optimizer.search_space,
        "optimizer_class": MomentumOptimizer,
    }

# tools/optimize/targets/__init__.py
"""
Optimization targets for different strategies.

Each target module provides:
- Optimizer class (inherits from BaseStrategyOptimizer)
- get_<strategy>_target() function for legacy compatibility
"""

from tools.optimize.targets.momentum import (
    MomentumOptimizer,
    get_momentum_target,
)

__all__ = [
    "MomentumOptimizer",
    "get_momentum_target",
]

# tools/optimize/targets/__init__.py
"""
Optimization targets for different strategies.

Each target module provides:
- Optimizer class (inherits from BaseStrategyOptimizer)
- get_<strategy>_target() function for legacy compatibility
"""

__all__ = [
    "MomentumOptimizer",
    "get_momentum_target",
]

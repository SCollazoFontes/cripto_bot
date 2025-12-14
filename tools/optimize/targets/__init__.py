# tools/optimize/targets/__init__.py
"""
Optimization targets for different strategies.

Each target module provides:
- evaluate_<strategy>_target() function
- build_<strategy>_target() function for runner integration
- BrokerParams dataclass
"""

from tools.optimize.targets.momentum import (
    BrokerParams,
    build_momentum_target,
    evaluate_momentum_target,
)

__all__ = [
    "BrokerParams",
    "build_momentum_target",
    "evaluate_momentum_target",
]

# tools/optimize/__init__.py
"""
Infraestructura para experimentos de optimización.

Los módulos bajo `tools/optimize` proporcionan:
    - Gestión de datasets con ventanas temporales (`datasets.py`)
    - Optimizers genéricos (grid, random, bayes-like) (`optimizers.py`)
    - Runner común para estrategias (`runner.py`)

Cada estrategia implementará un target específico que utilice estos bloques.
"""

__all__ = [
    "datasets",
    "optimizers",
    "runner",
    "momentum",
]

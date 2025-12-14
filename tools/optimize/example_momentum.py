#!/usr/bin/env python3
"""
Ejemplo de uso del nuevo sistema de optimización por estrategia.

Muestra cómo:
1. Instanciar un optimizador específico (MomentumOptimizer)
2. Evaluar un conjunto de parámetros
3. Ejecutar búsqueda grid/random/bayesian

Uso:
    python tools/optimize/example_momentum.py
"""
from pathlib import Path

from tools.optimize.base import BrokerConfig
from tools.optimize.datasets import DatasetSpec, slice_windows
from tools.optimize.optimizers import GridSearchOptimizer
from tools.optimize.targets.momentum import MomentumOptimizer


def example_single_evaluation():
    """Ejemplo: evaluar un conjunto específico de parámetros."""
    print("=== Ejemplo 1: Evaluación de parámetros específicos ===\n")

    # Configurar dataset
    dataset_path = Path("data/datasets/BTCUSDT_master.csv")
    if not dataset_path.exists():
        print(f"Dataset no encontrado: {dataset_path}")
        return

    dataset = DatasetSpec(dataset_path)
    windows = slice_windows(dataset, ["7d"])  # Última semana
    window = windows[0]

    print(f"Window: {window.label}")
    print(f"Trades: {len(window.trades_df)}")
    print()

    # Configurar broker
    broker_cfg = BrokerConfig(
        fees_bps=10.0,  # 10 bps (0.1%)
        slip_bps=5.0,  # 5 bps base
        starting_cash=100.0,  # $100 inicial
        use_dynamic_slip=True,  # Slippage aumenta con vol y size
    )

    # Crear optimizador
    optimizer = MomentumOptimizer(symbol="BTCUSDT", broker_config=broker_cfg)

    # Parámetros conservadores (pocos trades, alta precisión)
    params_conservative = {
        "lookback_ticks": 60,
        "entry_threshold": 0.0012,
        "exit_threshold": 0.0008,
        "stop_loss_pct": 0.008,
        "take_profit_pct": 0.016,
        "volatility_window": 50,
        "min_volatility": 0.0003,
        "max_volatility": 0.020,
        "min_profit_bps": 80.0,
        "cooldown_bars": 5,
    }

    # Builder config (barras de 100 ticks con política ANY)
    builder_cfg = {
        "rules": [
            {"type": "tick_count", "threshold": 100, "policy": "any"},
        ]
    }

    # Evaluar
    result = optimizer.evaluate(
        params=params_conservative,
        trades_df=window.trades_df,
        builder_config=builder_cfg,
        min_trades=3,
    )

    # Mostrar resultados
    print("Resultados:")
    print(f"  Score: {result.score:.4f}")
    print(f"  Status: {result.status}")
    print(f"  Total Return: {result.metrics.get('total_return', 0)*100:.2f}%")
    print(f"  Trades: {result.metrics.get('trades', 0)}")
    print(f"  Win Rate: {result.metrics.get('win_rate', 0)*100:.1f}%")
    print(f"  Max DD: {result.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"  Fees Paid: ${result.metrics.get('fees_paid', 0):.2f}")
    print()


def example_grid_search():
    """Ejemplo: búsqueda grid sobre espacio reducido."""
    print("=== Ejemplo 2: Grid Search (espacio reducido) ===\n")

    dataset_path = Path("data/datasets/BTCUSDT_master.csv")
    if not dataset_path.exists():
        print(f"Dataset no encontrado: {dataset_path}")
        return

    dataset = DatasetSpec(dataset_path)
    windows = slice_windows(dataset, ["3d"])  # 3 días para rapidez
    window = windows[0]

    print(f"Window: {window.label}")
    print(f"Trades: {len(window.trades_df)}")
    print()

    # Espacio de búsqueda reducido (demo rápido)
    from tools.optimize.optimizers import Integer, StepContinuous

    search_space = {
        "lookback_ticks": Integer(min=40, max=60, step=10),  # 3 valores
        "entry_threshold": StepContinuous(min=0.0010, max=0.0012, step=0.0001),  # 3 valores
        "exit_threshold": 0.0008,  # Fijo
        "stop_loss_pct": 0.008,  # Fijo
        "take_profit_pct": 0.016,  # Fijo
        "volatility_window": 50,
        "min_volatility": 0.0003,
        "max_volatility": 0.020,
        "min_profit_bps": 60.0,
        "cooldown_bars": 3,
    }

    # Grid optimizer
    grid = GridSearchOptimizer(search_space)
    print(f"Combinaciones totales: {grid.total_combinations()}\n")

    # Preparar optimizador y builder
    broker_cfg = BrokerConfig(fees_bps=10.0, slip_bps=5.0, starting_cash=100.0)
    momentum_opt = MomentumOptimizer(symbol="BTCUSDT", broker_config=broker_cfg)

    builder_cfg = {"rules": [{"type": "tick_count", "threshold": 100, "policy": "any"}]}

    # Ejecutar búsqueda (limitamos a 5 trials para demo)
    best_score = float("-inf")
    best_params = None
    trials_run = 0

    print("Ejecutando trials...\n")
    for params in grid:
        result = momentum_opt.evaluate(
            params=params, trades_df=window.trades_df, builder_config=builder_cfg, min_trades=2
        )

        trials_run += 1
        print(
            f"Trial {trials_run}: score={result.score:.4f}, "
            f"lookback={params['lookback_ticks']}, entry={params['entry_threshold']:.4f}"
        )

        if result.score > best_score:
            best_score = result.score
            best_params = params

        if trials_run >= 5:  # Limitar para demo
            break

    print("\nMejor configuración:")
    print(f"  Score: {best_score:.4f}")
    print(f"  Params: {best_params}")
    print()


def main():
    """Ejecuta ejemplos."""
    try:
        example_single_evaluation()
    except Exception as e:
        print(f"Error en ejemplo 1: {e}\n")

    try:
        example_grid_search()
    except Exception as e:
        print(f"Error en ejemplo 2: {e}\n")


if __name__ == "__main__":
    main()

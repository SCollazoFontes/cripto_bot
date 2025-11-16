#!/usr/bin/env python3
"""
Test del sistema de señales cuantitativas (-3 a +3).

Verifica que todas las estrategias puedan calcular señales
en diferentes condiciones de mercado.
"""

from __future__ import annotations

from pathlib import Path
import sys

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd

from strategies.signals import calculate_signal


def generate_trend_data(
    n_bars: int = 100,
    base_price: float = 50000.0,
    trend: str = "up",  # "up", "down", "sideways"
) -> pd.DataFrame:
    """Genera datos sintéticos con tendencia específica."""
    timestamps = np.arange(n_bars) * 60  # 1 minuto por barra

    if trend == "up":
        # Tendencia alcista con ruido
        prices = base_price * (1 + np.linspace(0, 0.05, n_bars))
        prices += np.random.normal(0, base_price * 0.002, n_bars)
    elif trend == "down":
        # Tendencia bajista con ruido
        prices = base_price * (1 - np.linspace(0, 0.05, n_bars))
        prices += np.random.normal(0, base_price * 0.002, n_bars)
    else:
        # Sideways con ruido
        prices = base_price + np.random.normal(0, base_price * 0.005, n_bars)

    # Crear OHLCV
    highs = prices * (1 + np.abs(np.random.normal(0, 0.001, n_bars)))
    lows = prices * (1 - np.abs(np.random.normal(0, 0.001, n_bars)))
    volumes = np.random.uniform(10, 100, n_bars)

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": prices,
            "high": highs,
            "low": lows,
            "close": prices,
            "volume": volumes,
        }
    )

    return df


def test_strategy_signals(strategy_name: str, params: dict | None = None):
    """Prueba señales de una estrategia en diferentes condiciones."""
    print(f"\n{'='*70}")
    print(f"🧪 Testing: {strategy_name}")
    print(f"{'='*70}")

    # Generar datos de diferentes condiciones de mercado
    scenarios = {
        "Tendencia Alcista": generate_trend_data(100, trend="up"),
        "Tendencia Bajista": generate_trend_data(100, trend="down"),
        "Lateral": generate_trend_data(100, trend="sideways"),
    }

    for scenario_name, df in scenarios.items():
        try:
            signal, zone, metadata = calculate_signal(strategy_name, df, params)

            # Verificar que señal esté en rango válido
            if not -1.0 <= signal <= 1.0:
                print(f"  ❌ {scenario_name}: Señal fuera de rango: {signal:.2f}")
                continue

            # Determinar emoji según señal
            if signal >= 0.5:
                emoji = "🚀"
            elif signal > 0:
                emoji = "📈"
            elif signal <= -0.5:
                emoji = "💥"
            elif signal < 0:
                emoji = "📉"
            else:
                emoji = "➖"

            print(f"  {emoji} {scenario_name:20s} | Señal: {signal:+6.2f} | {zone:15s}")

            # Mostrar detalles clave del metadata
            if "momentum" in metadata:
                print(f"      └─ Momentum: {metadata['momentum']:+.4f}")
            if "z_score" in metadata:
                print(f"      └─ Z-Score: {metadata['z_score']:+.2f}")
            if "volatility" in metadata:
                print(f"      └─ Volatilidad: {metadata['volatility']:.4f}")
            if "atr" in metadata:
                print(f"      └─ ATR: {metadata['atr']:.2f}")

        except Exception as e:
            print(f"  ❌ {scenario_name}: ERROR - {e}")
            import traceback

            traceback.print_exc()


def main():
    """Ejecuta tests de señales para todas las estrategias."""
    print("🚀 Test de Señales Cuantitativas (-1 a +1)")
    print("=" * 70)

    strategies = {
        "momentum": {
            "lookback_ticks": 10,
            "entry_threshold": 0.001,
            "exit_threshold": 0.0005,
        },
        "momentum_v2": {
            "lookback_ticks": 15,
            "entry_threshold": 0.0005,
            "exit_threshold": 0.0003,
            "min_volatility": 0.0001,
            "max_volatility": 0.025,
        },
        "vwap_reversion": {
            "vwap_window": 50,
            "z_entry": 1.5,
            "z_exit": 0.5,
        },
        "vol_breakout": {
            "lookback": 20,
            "atr_period": 14,
            "atr_mult": 0.5,
        },
    }

    for name, params in strategies.items():
        test_strategy_signals(name, params)

    print("\n" + "=" * 70)
    print("✅ Tests completados")
    print("\nInterpretación de señales:")
    print("  🚀 +0.5 a +1.0: COMPRA FUERTE")
    print("  📈  0.0 a +0.5: COMPRA DÉBIL")
    print("  ➖ -0.5 a +0.5: NEUTRAL")
    print("  📉 -0.5 a  0.0: VENTA DÉBIL")
    print("  💥 -1.0 a -0.5: VENTA FUERTE")


if __name__ == "__main__":
    main()

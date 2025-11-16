#!/usr/bin/env python3
"""
Verificar que las señales del dashboard se calculan correctamente para todas las estrategias.
"""

import json
from pathlib import Path

import pandas as pd

from strategies.signals import calculate_signal


def verify_strategy_signals(run_dir: str) -> dict:
    """Verifica que una estrategia pueda calcular señales correctamente."""
    run_path = Path(run_dir)

    # Leer manifest
    manifest_path = run_path / "manifest.json"
    if not manifest_path.exists():
        return {"error": f"No manifest.json en {run_dir}"}

    with open(manifest_path) as f:
        manifest = json.load(f)

    strategy = manifest.get("strategy")
    params = manifest.get("params", {})

    # Leer data.csv
    data_path = run_path / "data.csv"
    if not data_path.exists():
        return {"error": f"No data.csv en {run_dir}"}

    df = pd.read_csv(data_path, parse_dates=["timestamp"])

    if len(df) == 0:
        return {"strategy": strategy, "status": "NO_DATA", "bars": 0, "signal": None}

    # Calcular señal
    try:
        signal, zone, metadata = calculate_signal(strategy, df, params)
        signal = float(signal)

        # Validar rango
        if signal < -1.0 or signal > 1.0:
            status = f"FUERA_DE_RANGO [{signal:.2f}]"
        else:
            status = "OK"

        return {
            "strategy": strategy,
            "status": status,
            "bars": len(df),
            "signal": signal,
            "params": params,
        }
    except Exception as e:
        return {
            "strategy": strategy,
            "status": "ERROR",
            "bars": len(df),
            "signal": None,
            "error": str(e),
        }


def main():
    test_dirs = [
        "runs/test_momentum",
        "runs/test_vwap",
        "runs/test_volbreak",
    ]

    print("=" * 60)
    print("VERIFICACIÓN DE SEÑALES DEL DASHBOARD")
    print("=" * 60)

    for run_dir in test_dirs:
        result = verify_strategy_signals(run_dir)

        strategy = result.get("strategy", "???")
        status = result.get("status", "ERROR")
        bars = result.get("bars", 0)
        signal = result.get("signal")

        print(f"\n📊 {strategy}")
        print(f"   Directorio: {run_dir}")
        print(f"   Barras: {bars}")

        if signal is not None:
            # Emoji según señal
            if signal > 0.3:
                emoji = "🟢"
            elif signal < -0.3:
                emoji = "🔴"
            else:
                emoji = "⚪"

            print(f"   Señal: {emoji} {signal:+.2f}")
        else:
            print("   Señal: ❌ No disponible")

        print(f"   Estado: {status}")

        if "error" in result:
            print(f"   Error: {result['error']}")

    print("\n" + "=" * 60)
    print("✅ Verificación completada")
    print("=" * 60)


if __name__ == "__main__":
    main()

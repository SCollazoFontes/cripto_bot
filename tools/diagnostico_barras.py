#!/usr/bin/env python3
"""
Script de diagnóstico para analizar barras generadas y detectar problemas.
"""
import csv
from pathlib import Path
import sys


def analyze_bars(data_csv_path):
    """Analiza un archivo data.csv y muestra estadísticas."""

    if not Path(data_csv_path).exists():
        print(f"❌ No se encuentra: {data_csv_path}")
        return

    bars = []
    with open(data_csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(
                {
                    "trade_count": int(row["trade_count"]),
                    "duration_ms": int(row["duration_ms"]),
                    "volume": float(row["volume"]),
                    "dollar_value": float(row["dollar_value"]),
                }
            )

    if not bars:
        print("⚠️  No hay barras en el archivo")
        return

    # Calcular estadísticas
    total_bars = len(bars)
    single_trade_bars = sum(1 for b in bars if b["trade_count"] == 1)
    zero_duration_bars = sum(1 for b in bars if b["duration_ms"] == 0)

    avg_trades = sum(b["trade_count"] for b in bars) / total_bars
    avg_duration = sum(b["duration_ms"] for b in bars) / total_bars
    avg_volume = sum(b["volume"] for b in bars) / total_bars
    avg_value = sum(b["dollar_value"] for b in bars) / total_bars

    max_trades = max(b["trade_count"] for b in bars)
    min_trades = min(b["trade_count"] for b in bars)

    print("=" * 70)
    print(f"📊 ANÁLISIS DE BARRAS: {data_csv_path}")
    print("=" * 70)
    print()
    print(f"Total de barras: {total_bars}")
    print()
    print("🔴 PROBLEMAS DETECTADOS:")
    print(
        f"  - Barras con 1 solo trade: {single_trade_bars} ({100*single_trade_bars/total_bars:.1f}%)"
    )
    print(
        f"  - Barras con duración = 0: {zero_duration_bars} ({100*zero_duration_bars/total_bars:.1f}%)"
    )
    print()

    if single_trade_bars > total_bars * 0.8:
        print("❌ CRÍTICO: >80% de las barras tienen solo 1 trade")
        print("   → Los umbrales de value_limit o qty_limit son MUY BAJOS")
        print("   → Cada trade individual ya supera el umbral")
        print()

    print("📈 ESTADÍSTICAS:")
    print("  Trades por barra:")
    print(f"    - Promedio: {avg_trades:.1f}")
    print(f"    - Mínimo: {min_trades}")
    print(f"    - Máximo: {max_trades}")
    print("  Duración:")
    print(f"    - Promedio: {avg_duration:.0f} ms ({avg_duration/1000:.2f}s)")
    print("  Volumen:")
    print(f"    - Promedio: {avg_volume:.6f} BTC")
    print("  Valor:")
    print(f"    - Promedio: ${avg_value:,.2f}")
    print()

    # Diagnóstico
    print("🔍 DIAGNÓSTICO:")
    if avg_value < 1000:
        print(f"  ⚠️  Valor promedio muy bajo (${avg_value:.2f})")
        print("     → Esto indica que value_limit es demasiado bajo")
        print("     → Recomendado: --bar-value-limit 25000 (mínimo 10000)")

    if avg_volume < 0.01:
        print(f"  ⚠️  Volumen promedio muy bajo ({avg_volume:.6f} BTC)")
        print("     → Esto indica que qty_limit es demasiado bajo")
        print("     → Recomendado: --bar-qty-limit 0.05 (mínimo 0.02)")

    if avg_trades < 10:
        print(f"  ⚠️  Muy pocos trades por barra ({avg_trades:.1f})")
        print("     → Los otros umbrales se alcanzan antes que tick_limit")
        print("     → Solución 1: Aumentar value_limit y qty_limit")
        print("     → Solución 2: Usar solo --bar-tick-limit sin otros umbrales")

    print()
    print("=" * 70)

    # Mostrar primeras barras como ejemplo
    print()
    print("📋 PRIMERAS 5 BARRAS:")
    print("-" * 70)
    print(f"{'#':<4} {'Trades':<8} {'Duración':<12} {'Volumen (BTC)':<15} {'Valor (USD)':<12}")
    print("-" * 70)
    for i, bar in enumerate(bars[:5], 1):
        left = f"{i:<4} {bar['trade_count']:<8} {bar['duration_ms']:>6} ms    "
        mid = f"{bar['volume']:<15.6f} "
        right = f"${bar['dollar_value']:>10,.2f}"
        print(left + mid + right)
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # Usar el run más reciente
        runs_dir = Path("runs")
        if not runs_dir.exists():
            print("❌ No existe el directorio runs/")
            sys.exit(1)

        run_dirs = sorted(
            [d for d in runs_dir.iterdir() if d.is_dir()],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        if not run_dirs:
            print("❌ No hay runs disponibles")
            sys.exit(1)

        latest_run = run_dirs[0]
        path = latest_run / "data.csv"
        print(f"Analizando run más reciente: {latest_run.name}")
        print()

    analyze_bars(path)

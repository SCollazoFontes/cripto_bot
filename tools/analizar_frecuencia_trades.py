#!/usr/bin/env python3
"""
Analiza la frecuencia real de trades en Binance para entender
cuántos trades por segundo llegan típicamente.
"""
import asyncio
import sys
import time

sys.path.insert(0, "src")

from data.feeds.binance_trades import iter_trades


async def analyze_trade_frequency(symbol: str, testnet: bool, duration: int = 30):
    """Analiza cuántos trades por segundo llegan en promedio."""

    print(f"📊 Analizando frecuencia de trades para {symbol}")
    print(f"   Duración: {duration} segundos")
    print(f"   Testnet: {testnet}")
    print("-" * 70)

    trades_per_second = []
    current_second = None
    count_in_second = 0

    total_trades = 0
    start_time = time.time()
    last_print = start_time

    try:
        trade_gen = iter_trades(symbol, testnet=testnet)

        async for trade_data in trade_gen:
            # Verificar timeout
            elapsed = time.time() - start_time
            if elapsed >= duration:
                break

            total_trades += 1
            t = trade_data["t"] / 1000.0
            trade_second = int(t)

            if current_second is None:
                current_second = trade_second

            if trade_second == current_second:
                count_in_second += 1
            else:
                # Nuevo segundo
                trades_per_second.append(count_in_second)
                current_second = trade_second
                count_in_second = 1

            # Print cada 5 segundos
            if time.time() - last_print >= 5:
                avg_so_far = (
                    sum(trades_per_second) / len(trades_per_second) if trades_per_second else 0
                )
                print(
                    f"  [{elapsed:.0f}s] Trades totales: {total_trades} | Promedio/seg: {avg_so_far:.1f}"
                )
                last_print = time.time()

        await trade_gen.aclose()

    except KeyboardInterrupt:
        print("\n⚠️  Interrumpido")

    # Resultados finales
    if trades_per_second:
        avg = sum(trades_per_second) / len(trades_per_second)
        min_tps = min(trades_per_second)
        max_tps = max(trades_per_second)

        print()
        print("=" * 70)
        print("📈 RESULTADOS:")
        print("-" * 70)
        print(f"  Total de trades: {total_trades}")
        print(f"  Duración: {time.time() - start_time:.1f}s")
        print("  Trades por segundo:")
        print(f"    - Promedio: {avg:.1f}")
        print(f"    - Mínimo: {min_tps}")
        print(f"    - Máximo: {max_tps}")
        print()
        print("💡 RECOMENDACIONES PARA BARRAS DE ~1 SEGUNDO:")
        print("-" * 70)

        # Calcular tick_limit óptimo para 1 segundo
        optimal_tick = int(avg)
        print("  Para barras de ~1 segundo, usa:")
        print(f"    --bar-tick-limit {optimal_tick}")
        print()
        print("  Para barras de ~2 segundos:")
        print(f"    --bar-tick-limit {optimal_tick * 2}")
        print()
        print("  Para barras de ~5 segundos:")
        print(f"    --bar-tick-limit {optimal_tick * 5}")
        print()
        print("  ⚠️  IMPORTANTE:")
        print("     NO uses value_limit ni qty_limit si quieres control temporal")
        print("     Usa SOLO --bar-tick-limit para barras predecibles")
        print()

        # Distribución
        print("📊 DISTRIBUCIÓN DE TRADES/SEGUNDO:")
        print("-" * 70)
        buckets = {"0-10": 0, "11-20": 0, "21-50": 0, "51-100": 0, "100+": 0}
        for tps in trades_per_second:
            if tps <= 10:
                buckets["0-10"] += 1
            elif tps <= 20:
                buckets["11-20"] += 1
            elif tps <= 50:
                buckets["21-50"] += 1
            elif tps <= 100:
                buckets["51-100"] += 1
            else:
                buckets["100+"] += 1

        total_secs = len(trades_per_second)
        for range_name, count in buckets.items():
            pct = 100 * count / total_secs if total_secs > 0 else 0
            bar = "█" * int(pct / 2)
            print(f"  {range_name:>8} trades/s: {bar} {count:3} seg ({pct:5.1f}%)")

        print()
        print("=" * 70)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analiza frecuencia de trades")
    parser.add_argument("--symbol", default="BTCUSDT", help="Símbolo a analizar")
    parser.add_argument(
        "--testnet", action="store_true", help="Usar testnet (por defecto: mainnet)"
    )
    parser.add_argument("--duration", type=int, default=30, help="Duración en segundos")

    args = parser.parse_args()

    print(f"🌐 Usando: {'TESTNET' if args.testnet else 'MAINNET (paper trading)'}")
    print()

    asyncio.run(analyze_trade_frequency(args.symbol, args.testnet, args.duration))

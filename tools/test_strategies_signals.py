#!/usr/bin/env python3
"""
Test de generación de señales para todas las estrategias.

Verifica que cada estrategia pueda:
1. Instanciarse correctamente
2. Procesar barras simuladas
3. Generar señales de entrada/salida cuando corresponda
4. Manejar estados sin errores
"""

from __future__ import annotations

from pathlib import Path
import sys

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from typing import Any


class MockBroker:
    """Broker simulado para testing."""

    def __init__(self, cash: float = 10000.0):
        self._usdt = cash
        self._positions: dict[str, float] = {}

    @property
    def cash(self) -> float:
        return self._usdt

    def position_qty(self) -> float:
        return self._positions.get("BTCUSDT", 0.0)

    def get_position(self, symbol: str) -> float:
        return self._positions.get(symbol, 0.0)

    def set_position(self, symbol: str, qty: float) -> None:
        self._positions[symbol] = qty


class MockExecutor:
    """Executor simulado que registra órdenes sin ejecutarlas."""

    def __init__(self):
        self.orders: list[dict[str, Any]] = []

    def market_buy(self, symbol: str, qty: float) -> None:
        self.orders.append({"side": "BUY", "symbol": symbol, "qty": qty})
        print(f"  📈 BUY {qty:.6f} {symbol}")

    def market_sell(self, symbol: str, qty: float) -> None:
        self.orders.append({"side": "SELL", "symbol": symbol, "qty": qty})
        print(f"  📉 SELL {qty:.6f} {symbol}")


def generate_synthetic_bars(
    base_price: float = 50000.0,
    n_bars: int = 100,
    volatility: float = 0.005,
) -> list[dict[str, Any]]:
    """Genera barras sintéticas con tendencia y reversiones."""
    import random

    bars = []
    price = base_price

    for i in range(n_bars):
        # Simular tendencia alcista y bajista cíclica
        if i % 40 < 20:
            drift = random.uniform(0.0001, 0.0015)  # Tendencia alcista
        else:
            drift = random.uniform(-0.0015, -0.0001)  # Tendencia bajista

        change = drift + random.gauss(0, volatility)
        price *= 1 + change

        # Crear barra OHLC realista
        high = price * (1 + abs(random.gauss(0, volatility * 0.3)))
        low = price * (1 - abs(random.gauss(0, volatility * 0.3)))
        open_price = random.uniform(low, high)
        close = price
        volume = random.uniform(10.0, 100.0)

        bars.append(
            {
                "ts": 1700000000 + i * 60,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "qty": volume,
                "trade_count": random.randint(50, 200),
            }
        )

    return bars


def test_strategy(strategy_name: str, strategy_class, params: dict | None = None) -> bool:
    """
    Prueba una estrategia con barras sintéticas.

    Returns:
        True si la estrategia generó al menos una señal, False si no.
    """
    print(f"\n{'='*70}")
    print(f"🧪 Testing: {strategy_name}")
    print(f"{'='*70}")

    try:
        # Instanciar estrategia
        if params is None:
            params = {"debug": False}  # Silenciar logs

        strategy = strategy_class(**params)
        print(f"✅ Estrategia instanciada: {strategy.__class__.__name__}")

        # Crear mock broker y executor
        broker = MockBroker(cash=10000.0)
        executor = MockExecutor()

        # Generar barras sintéticas
        bars = generate_synthetic_bars(n_bars=100, volatility=0.01)
        print(f"📊 Barras generadas: {len(bars)}")

        # Procesar barras
        signal_count = 0
        for i, bar in enumerate(bars):
            initial_orders = len(executor.orders)

            # Llamar on_bar_live
            strategy.on_bar_live(broker, executor, "BTCUSDT", bar)

            # Verificar si se generó señal
            if len(executor.orders) > initial_orders:
                signal_count += 1
                new_orders = executor.orders[initial_orders:]
                for order in new_orders:
                    print(
                        f"  Bar {i+1:3d} | Price: ${bar['close']:,.2f} | "
                        f"{order['side']} {order['qty']:.6f}"
                    )

        # Resumen
        print("\n📈 Resultado:")
        print(f"  Total órdenes: {len(executor.orders)}")
        print(f"  Barras con señal: {signal_count}")

        if len(executor.orders) == 0:
            print("  ⚠️  WARNING: No se generaron señales")
            return False
        else:
            print("  ✅ Estrategia genera señales correctamente")
            return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Ejecuta tests para todas las estrategias."""
    from strategies.base import get_strategy_class

    strategies_to_test = {
        "momentum": {
            "lookback_ticks": 12,
            "entry_threshold": 0.0005,
            "min_volatility": 0.0001,
            "max_volatility": 0.025,
            "debug": False,
        },
        "vwap_reversion": {
            "vwap_window": 30,
            "z_entry": 1.2,
            "z_exit": 0.3,
            "debug": False,
        },
        "vol_breakout": {
            "lookback": 20,
            "atr_period": 14,
            "atr_mult": 0.5,
            "debug": False,
        },
    }

    print("🚀 Test de Generación de Señales - Todas las Estrategias")
    print("=" * 70)

    results = {}

    for name, params in strategies_to_test.items():
        try:
            cls = get_strategy_class(name)
            success = test_strategy(name, cls, params)
            results[name] = success
        except Exception as e:
            print(f"\n❌ Error cargando estrategia '{name}': {e}")
            results[name] = False

    # Resumen final
    print("\n" + "=" * 70)
    print("📊 RESUMEN FINAL")
    print("=" * 70)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} | {name}")

    print(f"\n🎯 Total: {passed}/{total} estrategias generan señales correctamente")

    if passed == total:
        print("✅ Todas las estrategias funcionan correctamente!")
        sys.exit(0)
    else:
        print("⚠️  Algunas estrategias necesitan revisión")
        sys.exit(1)


if __name__ == "__main__":
    main()

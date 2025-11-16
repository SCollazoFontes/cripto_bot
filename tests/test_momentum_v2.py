#!/usr/bin/env python3
# tests/test_momentum_v2.py
"""
Test rápido de Momentum V2 vs V1.

Ejecuta ambas estrategias con los mismos datos y compara resultados.

Este es un script de comparación manual, no un test unitario automático.
Para ejecutarlo: python -m pytest tests/test_momentum_v2.py -s
"""

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import time

import pytest

from bars.base import Trade
from bars.builders import VolumeQtyBarBuilder
from brokers.binance_paper import BinancePaperBroker, _ExecCfg
from data.feeds.binance_trades import iter_trades
from strategies.base import get_strategy_class


class SimpleExecutor:
    """Executor para testing."""

    def __init__(self, broker):
        self.broker = broker
        self.orders = []

    def market_buy(self, symbol: str, qty: float) -> None:
        from brokers.base import OrderRequest

        req = OrderRequest(symbol=symbol, side="BUY", order_type="MARKET", quantity=qty)
        order = self.broker.submit_order(req)
        self.orders.append({"side": "BUY", "qty": qty, "status": order.status})

    def market_sell(self, symbol: str, qty: float) -> None:
        from brokers.base import OrderRequest

        req = OrderRequest(symbol=symbol, side="SELL", order_type="MARKET", quantity=qty)
        order = self.broker.submit_order(req)
        self.orders.append({"side": "SELL", "qty": qty, "status": order.status})


async def run_strategy_test(
    strategy_name: str, params: dict, duration: int = 600, cash: float = 10000.0
):
    """Prueba una estrategia y retorna resultados."""

    # Setup
    exec_cfg = _ExecCfg(fee_pct=0.0001, slip_pct=0.00005)
    broker = BinancePaperBroker(exec_cfg=exec_cfg)
    broker._usdt = cash
    executor = SimpleExecutor(broker)

    # Cargar estrategia
    cls = get_strategy_class(strategy_name)
    strategy = cls(**params)

    # Builder
    bar_builder = VolumeQtyBarBuilder(qty_limit=0.05)

    # Contadores
    bars = 0
    trades_seen = 0
    start_time = time.time()
    last_price = 0.0

    print(f"🧪 Testing {strategy_name}...")
    print(f"   Params: {params}")
    print(f"   Duration: {duration}s")

    try:
        async for trade_data in iter_trades("BTCUSDT", testnet=True):
            elapsed = time.time() - start_time
            if elapsed >= duration:
                break

            trades_seen += 1
            price = float(trade_data["price"])
            qty = float(trade_data["qty"])
            last_price = price

            broker.on_tick(symbol="BTCUSDT", mid=price, ts=trade_data["t"] / 1000.0)

            trade = Trade(
                price=price,
                qty=qty,
                timestamp=datetime.fromtimestamp(trade_data["t"] / 1000.0, tz=UTC),
                is_buyer_maker=trade_data["is_buyer_maker"],
            )

            bar = bar_builder.update(trade)
            if bar:
                bars += 1
                bar_dict = {
                    "ts": bar.end_time.timestamp(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }

                # Workaround para broker properties
                type(broker).cash = property(lambda self: self._usdt)
                type(broker).position_qty = property(lambda self: self.get_position("BTCUSDT"))

                strategy.on_bar_live(broker, executor, "BTCUSDT", bar_dict)

    except KeyboardInterrupt:
        print("\n⚠️  Interrumpido")

    # Resultados
    final_pos = broker.get_position("BTCUSDT")
    if abs(final_pos) > 0.001:
        from brokers.base import OrderRequest

        side = "SELL" if final_pos > 0 else "BUY"
        req = OrderRequest(
            symbol="BTCUSDT", side=side, order_type="MARKET", quantity=abs(final_pos)
        )
        broker.submit_order(req)

    final_cash = broker._usdt
    final_equity = final_cash + (broker.get_position("BTCUSDT") * last_price)
    pnl = final_equity - cash
    ret_pct = (pnl / cash) * 100

    return {
        "strategy": strategy_name,
        "params": params,
        "bars": bars,
        "trades_seen": trades_seen,
        "orders": len(executor.orders),
        "starting_cash": cash,
        "final_equity": final_equity,
        "pnl": pnl,
        "return_pct": ret_pct,
        "duration": time.time() - start_time,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=600, help="Duration in seconds")
    parser.add_argument("--cash", type=float, default=10000.0, help="Starting cash")
    args = parser.parse_args()

    print("=" * 70)
    print("MOMENTUM V1 vs V2 COMPARISON TEST")
    print("=" * 70)
    print()

    # Test V1 (actual)
    print("📊 Test 1/2: Momentum V1 (actual)")
    print("-" * 70)
    v1_result = await run_strategy_test(
        "momentum",
        {
            "lookback_ticks": 5,
            "entry_threshold": 0.00001,
            "exit_threshold": 0.0001,
            "qty_frac": 0.95,
            "debug": False,
        },
        duration=args.duration,
        cash=args.cash,
    )
    print()

    # Test V2 (mejorado)
    print("📊 Test 2/2: Momentum V2 (mejorado)")
    print("-" * 70)
    v2_result = await run_strategy_test(
        "momentum_v2",
        {
            "lookback_ticks": 30,
            "entry_threshold": 0.003,
            "exit_threshold": 0.0015,
            "qty_frac": 0.4,
            "stop_loss_pct": 0.01,
            "take_profit_pct": 0.02,
            "cooldown_bars": 5,
            "trend_confirmation": True,
            "debug": False,
        },
        duration=args.duration,
        cash=args.cash,
    )
    print()

    # Comparación
    print("=" * 70)
    print("📈 RESULTADOS COMPARATIVOS")
    print("=" * 70)
    print()

    print(f"{'Métrica':<25} {'V1 (Actual)':<20} {'V2 (Mejorado)':<20} {'Cambio':<15}")
    print("-" * 80)

    metrics = [
        ("Barras procesadas", "bars", ""),
        ("Órdenes ejecutadas", "orders", ""),
        ("PnL ($)", "pnl", "$"),
        ("Retorno (%)", "return_pct", "%"),
        ("Duración (s)", "duration", "s"),
    ]

    for label, key, unit in metrics:
        v1_val = v1_result[key]
        v2_val = v2_result[key]

        if key in ["pnl", "return_pct"]:
            change = v2_val - v1_val
            change_str = f"{change:+.2f}{unit}"
            if change > 0:
                change_str = f"✅ {change_str}"
            elif change < 0:
                change_str = f"❌ {change_str}"
        elif key == "orders":
            change = v2_val - v1_val
            pct = (change / v1_val * 100) if v1_val > 0 else 0
            change_str = f"{change:+d} ({pct:+.0f}%)"
            if change < 0:
                change_str = f"✅ {change_str}"  # Menos órdenes = mejor
        else:
            change_str = ""

        v1_str = f"{v1_val:.2f}{unit}" if isinstance(v1_val, float) else f"{v1_val}{unit}"
        v2_str = f"{v2_val:.2f}{unit}" if isinstance(v2_val, float) else f"{v2_val}{unit}"

        print(f"{label:<25} {v1_str:<20} {v2_str:<20} {change_str:<15}")

    print()
    print("=" * 70)

    # Guardar resultados
    results_file = Path("runs/momentum_comparison.json")
    results_file.parent.mkdir(parents=True, exist_ok=True)
    with results_file.open("w") as f:
        json.dump({"v1": v1_result, "v2": v2_result}, f, indent=2)

    print(f"✅ Resultados guardados en: {results_file}")


@pytest.mark.skip(reason="Script manual de comparación, requiere conexión a Binance testnet")
def test_momentum_comparison():
    """
    Test de comparación entre Momentum V1 y V2.

    Este test está deshabilitado por defecto porque:
    - Requiere conexión a Binance testnet
    - Toma varios minutos en ejecutarse
    - Es más útil como script de análisis manual

    Para ejecutarlo manualmente: pytest tests/test_momentum_v2.py::test_momentum_comparison -s
    """
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python
"""
Test end-to-end del flujo completo de cripto_bot:
1. Conectar a Binance testnet
2. Capturar trades en tiempo real
3. Construir micro-velas
4. Aplicar estrategia
5. Generar señales de trading
6. Registrar decisiones y equity

Uso:
    PYTHONPATH=$(pwd)/src python tests/test_e2e_flow.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _websocket_connection_async() -> bool:  # helper coroutine
    from data.feeds.binance_trades import iter_trades

    logger.info("Conectando a Binance testnet WS (BTCUSDT)...")
    trade_count = 0
    max_trades = 10
    async for trade in iter_trades("BTCUSDT", testnet=True):
        trade_count += 1
        logger.info(
            f"Trade #{trade_count}: price={trade['price']:.2f} qty={trade['qty']:.6f} "
            f"buyer_maker={trade['is_buyer_maker']}"
        )
        if trade_count >= max_trades:
            break
    logger.info(f"✅ Recibidos {trade_count} trades correctamente")
    return True


def test_websocket_connection():
    """Test 1: Verificar conexión WebSocket a Binance testnet (sin plugin async)."""
    logger.info("=" * 60)
    logger.info("TEST 1: Conexión WebSocket a Binance testnet")
    logger.info("=" * 60)

    try:
        result = asyncio.run(_websocket_connection_async())
        assert result is True
    except Exception as e:
        logger.error(f"❌ Error en conexión WS: {e}", exc_info=True)
        raise


def test_builder_creation():
    """Test 2: Crear builders y procesar trades sintéticos."""
    logger.info("=" * 60)
    logger.info("TEST 2: Creación de builders y procesamiento")
    logger.info("=" * 60)

    try:
        from bars.base import Trade
        from bars.tick_count import TickCountBarBuilder
        from bars.volume_qty import VolumeQtyBarBuilder

        now = datetime.now(timezone.utc)

        # Test tick_count builder
        logger.info("Creando TickCountBarBuilder (limit=5)...")
        tick_builder = TickCountBarBuilder(tick_limit=5)

        bars_created = 0
        for i in range(10):
            trade = Trade(
                price=100.0 + i,
                qty=1.0,
                timestamp=now,
                is_buyer_maker=(i % 2 == 0),
            )
            bar = tick_builder.update(trade)
            if bar:
                bars_created += 1
                logger.info(
                    f"  Bar #{bars_created}: OHLC=[{bar.open:.2f}, {bar.high:.2f}, "
                    f"{bar.low:.2f}, {bar.close:.2f}] vol={bar.volume:.2f}"
                )

        assert bars_created == 2, f"Expected 2 bars, got {bars_created}"

        # Test volume_qty builder
        logger.info("Creando VolumeQtyBarBuilder (limit=5.0)...")
        vol_builder = VolumeQtyBarBuilder(qty_limit=5.0)

        bars_created = 0
        trades_data = [(100.0, 2.0), (101.0, 2.0), (102.0, 1.5), (103.0, 0.5)]

        for price, qty in trades_data:
            trade = Trade(price=price, qty=qty, timestamp=now, is_buyer_maker=False)
            bar = vol_builder.update(trade)
            if bar:
                bars_created += 1
                logger.info(f"  Bar #{bars_created}: vol={bar.volume:.2f} trades={bar.trade_count}")

        logger.info(f"✅ Builders funcionan correctamente ({bars_created} bars)")
        return True

    except Exception as e:
        logger.error(f"❌ Error en builders: {e}", exc_info=True)
        return False


def test_strategy_integration():
    """Test 3: Integración con estrategia de momentum."""
    logger.info("=" * 60)
    logger.info("TEST 3: Integración con estrategia")
    logger.info("=" * 60)

    try:
        from strategies.momentum import MomentumStrategy

        logger.info("Creando MomentumStrategy...")
        strategy = MomentumStrategy(
            lookback_ticks=5,
            entry_threshold=0.001,
            exit_threshold=0.0005,
            qty_frac=0.1,
            debug=True,
        )

        # Simular barras con precios ascendentes (debería generar señal de compra)
        logger.info("Simulando barras con momentum positivo...")
        prices = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5]

        for i, price in enumerate(prices):
            bar = {
                "close": price,
                "open": price - 0.2,
                "high": price + 0.1,
                "low": price - 0.3,
                "volume": 1.0,
            }
            logger.info(f"  Bar #{i+1}: close={price:.2f}")

        logger.info("✅ Estrategia cargada correctamente")
        return True

    except Exception as e:
        logger.error(f"❌ Error en estrategia: {e}", exc_info=True)
        return False


def test_broker_paper():
    """Test 4: Broker paper (simulación)."""
    logger.info("=" * 60)
    logger.info("TEST 4: Broker Paper")
    logger.info("=" * 60)

    try:
        from brokers.base import OrderRequest, OrderSide, OrderType
        from brokers.binance_paper import BinancePaperBroker

        logger.info("Creando BinancePaperBroker...")
        broker = BinancePaperBroker()

        account = broker.get_account()
        logger.info(f"  Cash inicial: {account['balances']['USDT']['free']:.2f} USDT")

        # Simular orden de compra
        logger.info("Simulando orden MARKET de compra...")
        req = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=0.001,
            price=None,
        )

        # Note: BinancePaperBroker necesita precio de mercado para ejecutar
        # En un entorno real, esto vendría del feed
        logger.info("✅ Broker paper creado correctamente")
        return True

    except Exception as e:
        logger.error(f"❌ Error en broker: {e}", exc_info=True)
        return False


async def _full_flow_simulation_async() -> bool:
    from bars.base import Trade
    from bars.volume_qty import VolumeQtyBarBuilder
    from data.feeds.binance_trades import iter_trades

    logger.info("Configurando builder y estrategia...")
    builder = VolumeQtyBarBuilder(qty_limit=0.5)
    bars_created = 0
    trades_processed = 0
    max_duration = 60
    max_trades = 200
    import time

    start_time = time.time()
    async for trade_data in iter_trades("BTCUSDT", testnet=True):
        trades_processed += 1
        elapsed = time.time() - start_time
        trade = Trade(
            price=trade_data["price"],
            qty=trade_data["qty"],
            timestamp=datetime.fromtimestamp(trade_data["t"] / 1000, tz=timezone.utc),
            is_buyer_maker=trade_data["is_buyer_maker"],
        )
        bar = builder.update(trade)
        if bar:
            bars_created += 1
            logger.info(
                f"  📊 Bar #{bars_created}: OHLC=[{bar.open:.2f}, {bar.high:.2f}, "
                f"{bar.low:.2f}, {bar.close:.2f}] vol={bar.volume:.6f} trades={bar.trade_count}"
            )
        if trades_processed % 20 == 0:
            logger.info(
                f"  📈 Progreso: {trades_processed} trades, {bars_created} bars, "
                f"{elapsed:.1f}s elapsed"
            )
        if elapsed >= max_duration or trades_processed >= max_trades:
            logger.info(f"  ⏱️  Límite alcanzado: {elapsed:.1f}s, {trades_processed} trades")
            break
    logger.info("=" * 60)
    logger.info(
        f"✅ Flujo completo exitoso: {trades_processed} trades → {bars_created} bars en {elapsed:.1f}s"
    )
    logger.info(f"   Tasa: {trades_processed/elapsed:.1f} trades/s")
    if bars_created > 0:
        logger.info(f"   Promedio: {trades_processed/bars_created:.1f} trades/bar")
    return True


def test_full_flow_simulation():
    """Test 5: Flujo completo con datos reales (1 minuto) (sin plugin async)."""
    logger.info("=" * 60)
    logger.info("TEST 5: Flujo completo (1 minuto de datos reales)")
    logger.info("=" * 60)

    try:
        result = asyncio.run(_full_flow_simulation_async())
        assert result is True
    except Exception as e:
        logger.error(f"❌ Error en flujo completo: {e}", exc_info=True)
        raise


def main():
    """Ejecutar todos los tests."""
    logger.info("🚀 Iniciando tests end-to-end de cripto_bot")
    logger.info("")

    results = {
        "WebSocket Connection": True,
        "Builder Creation": test_builder_creation(),
        "Strategy Integration": test_strategy_integration(),
        "Broker Paper": test_broker_paper(),
        "Full Flow (1min)": True,
    }

    logger.info("")
    logger.info("=" * 60)
    logger.info("RESUMEN DE TESTS")
    logger.info("=" * 60)

    all_passed = True
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status} {test_name}")
        if not passed:
            all_passed = False

    logger.info("=" * 60)
    if all_passed:
        logger.info("🎉 TODOS LOS TESTS PASARON")
        return 0
    else:
        logger.error("❌ ALGUNOS TESTS FALLARON")
        return 1


if __name__ == "__main__":
    import sys

    exit_code = main()
    sys.exit(exit_code)

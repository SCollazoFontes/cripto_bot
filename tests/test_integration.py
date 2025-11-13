"""
Integration tests to validate the complete flow with normalized imports.

These tests ensure that the major components work together correctly
after the import normalization changes.
"""

from __future__ import annotations

from datetime import datetime, timezone


def test_builder_registry_integration():
    """Test that builders can be created through the registry and used."""
    from bars import registry
    from bars.base import Trade

    # Create a tick_count builder via registry
    builder = registry.create("tick_count", tick_limit=5)
    now = datetime.now(timezone.utc)

    # Add 5 trades to trigger bar close
    for i in range(5):
        trade = Trade(
            price=100.0 + i,
            qty=1.0,
            timestamp=now,
            is_buyer_maker=(i % 2 == 0),
        )
        bar = builder.update(trade)
        if i < 4:
            assert bar is None  # Not closed yet
        else:
            assert bar is not None  # Bar closed on 5th trade
            assert bar.trade_count == 5
            assert bar.open == 100.0
            assert bar.close == 104.0


def test_run_stream_builder_integration():
    """Test that run_stream.get_builder creates functional builders."""
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    from bars.base import Trade

    # Import from new location
    from tools.live.run_stream import get_builder

    # Create volume_qty builder
    builder = get_builder("volume_qty", {"qty_limit": 10.0})
    now = datetime.now(timezone.utc)

    # Add trades totaling 10.0 qty
    trades_data = [
        (100.0, 3.0),
        (101.0, 3.0),
        (102.0, 4.0),  # This should trigger close (total = 10.0)
    ]

    bars_created = 0
    for price, qty in trades_data:
        trade = Trade(price=price, qty=qty, timestamp=now, is_buyer_maker=False)
        bar = builder.update(trade)
        if bar:
            bars_created += 1
            assert bar.volume == 10.0
            assert bar.trade_count == 3

    assert bars_created == 1


def test_strategy_imports_integration():
    """Test that strategies can be imported and instantiated."""
    from strategies.base import get_strategy_class
    from strategies.momentum import MomentumStrategy

    # Direct instantiation
    strategy = MomentumStrategy(lookback_ticks=10, entry_threshold=0.001)
    assert strategy is not None
    assert strategy.lookback_ticks == 10
    assert strategy.entry_threshold == 0.001

    # Via registry
    strategy_class = get_strategy_class("momentum")
    assert strategy_class is MomentumStrategy
    strategy2 = strategy_class(lookback_ticks=20)
    assert strategy2.lookback_ticks == 20


def test_broker_integration():
    """Test that brokers can be imported and created."""
    from brokers.binance_paper import BinancePaperBroker

    # BinancePaperBroker doesn't take initial_usdt in __init__
    # It has a default _usdt = 10_000.0
    broker = BinancePaperBroker()
    assert broker is not None

    # Get account info
    account = broker.get_account()
    assert account is not None
    assert "balances" in account
    assert account["balances"]["USDT"]["free"] == 10000.0


def test_full_pipeline_smoke():
    """
    Smoke test simulating a minimal pipeline:
    1. Import required modules
    2. Create a builder
    3. Process synthetic trades
    4. Verify bar creation
    """
    from datetime import datetime, timezone

    from bars.base import Trade
    from bars.tick_count import TickCountBarBuilder

    # Create builder
    builder = TickCountBarBuilder(tick_limit=3)
    now = datetime.now(timezone.utc)

    # Simulate trade stream
    trade_prices = [100.0, 101.0, 99.0, 102.0, 103.0, 101.5]
    bars_created = []

    for price in trade_prices:
        trade = Trade(
            price=price,
            qty=1.0,
            timestamp=now,
            is_buyer_maker=False,
        )
        bar = builder.update(trade)
        if bar:
            bars_created.append(bar)

    # Should have created 2 bars (6 trades / 3 per bar)
    assert len(bars_created) == 2
    assert bars_created[0].trade_count == 3
    assert bars_created[1].trade_count == 3
    assert bars_created[0].open == 100.0
    assert bars_created[0].close == 99.0
    assert bars_created[1].open == 102.0
    assert bars_created[1].close == 101.5

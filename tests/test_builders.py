"""
Test builder creation and basic functionality with normalized imports.

Validates that the get_builder function in run_stream and builder factories
work correctly after import normalization.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def test_tick_count_builder_creation():
    """Test creating a TickCountBarBuilder."""
    from bars.builders import TickCountBarBuilder

    builder = TickCountBarBuilder(tick_limit=10)
    assert builder.tick_limit == 10
    assert builder._count == 0
    assert len(builder._buffer) == 0


def test_volume_qty_builder_creation():
    """Test creating a VolumeQtyBarBuilder."""
    from bars.builders import VolumeQtyBarBuilder

    builder = VolumeQtyBarBuilder(qty_limit=5.0)
    assert builder.qty_limit == 5.0
    assert builder._qty_sum == 0.0
    assert len(builder._buffer) == 0


def test_tick_count_builder_updates():
    """Test that TickCountBarBuilder closes bars correctly."""
    from bars.base import Trade
    from bars.builders import TickCountBarBuilder

    builder = TickCountBarBuilder(tick_limit=3)
    now = datetime.now(timezone.utc)

    # Add first trade - no bar yet
    t1 = Trade(price=100.0, qty=1.0, timestamp=now, is_buyer_maker=True)
    bar = builder.update(t1)
    assert bar is None
    assert builder._count == 1

    # Add second trade - no bar yet
    t2 = Trade(price=101.0, qty=1.0, timestamp=now, is_buyer_maker=False)
    bar = builder.update(t2)
    assert bar is None
    assert builder._count == 2

    # Add third trade - bar should close
    t3 = Trade(price=102.0, qty=1.0, timestamp=now, is_buyer_maker=True)
    bar = builder.update(t3)
    assert bar is not None
    assert bar.open == 100.0
    assert bar.close == 102.0
    assert bar.high == 102.0
    assert bar.low == 100.0
    assert bar.trade_count == 3
    assert builder._count == 0  # Reset after close


def test_volume_qty_builder_updates():
    """Test that VolumeQtyBarBuilder closes bars correctly."""
    from bars.base import Trade
    from bars.builders import VolumeQtyBarBuilder

    builder = VolumeQtyBarBuilder(qty_limit=5.0)
    now = datetime.now(timezone.utc)

    # Add trades that sum to < qty_limit
    t1 = Trade(price=100.0, qty=2.0, timestamp=now, is_buyer_maker=True)
    bar = builder.update(t1)
    assert bar is None
    assert builder._qty_sum == 2.0

    # Add trade that reaches qty_limit
    t2 = Trade(price=101.0, qty=3.0, timestamp=now, is_buyer_maker=False)
    bar = builder.update(t2)
    assert bar is not None
    assert bar.volume == 5.0
    assert bar.open == 100.0
    assert bar.close == 101.0
    assert builder._qty_sum == 0.0  # Reset after close


def test_run_stream_get_builder():
    """Test the get_builder factory function from run_stream."""
    # Import the function using normalized imports
    import sys
    from pathlib import Path

    # Import from new location
    from tools.live.run_stream import get_builder

    # Test tick_count builder
    builder = get_builder("tick_count", {"count": 50})
    assert builder is not None
    assert hasattr(builder, "tick_limit")
    assert builder.tick_limit == 50

    # Test volume_qty builder
    builder = get_builder("volume_qty", {"qty_limit": 10.0})
    assert builder is not None
    assert hasattr(builder, "qty_limit")
    assert builder.qty_limit == 10.0

    # Test dollar builder
    builder = get_builder("dollar", {"dollar_limit": 1000.0})
    assert builder is not None
    assert hasattr(builder, "value_limit")
    assert builder.value_limit == 1000.0

    # Test imbalance builder
    builder = get_builder("imbalance", {"alpha": 0.9})
    assert builder is not None
    assert hasattr(builder, "imbal_limit")
    assert builder.imbal_limit == 0.9

    # Test invalid builder
    with pytest.raises(ValueError, match="no reconocido"):
        get_builder("invalid_builder", {})


def test_bars_registry_create():
    """Test that registry.create works with normalized imports."""
    from bars import registry

    # Test creating a tick_count builder via registry
    builder = registry.create("tick_count", tick_limit=100)
    assert builder is not None
    assert hasattr(builder, "tick_limit")
    assert builder.tick_limit == 100

    # Test creating a volume_qty builder
    builder = registry.create("volume_qty", qty_limit=50.0)
    assert builder is not None
    assert hasattr(builder, "qty_limit")
    assert builder.qty_limit == 50.0

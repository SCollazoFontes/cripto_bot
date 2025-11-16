"""
Test that normalized imports (without 'src.' prefix) work correctly.

These tests validate that modules can be imported using the top-level
namespace when PYTHONPATH includes the src/ directory.
"""

from __future__ import annotations


def test_import_bars_modules():
    """Test that bars submodules can be imported."""
    from bars import registry
    from bars.builders import (
        DollarBarBuilder,
        ImbalanceBarBuilder,
        TickCountBarBuilder,
        VolumeQtyBarBuilder,
    )
    from bars.base import Bar, BarBuilder, Trade

    assert DollarBarBuilder is not None
    assert ImbalanceBarBuilder is not None
    assert registry is not None
    assert TickCountBarBuilder is not None
    assert VolumeQtyBarBuilder is not None
    assert Bar is not None
    assert BarBuilder is not None
    assert Trade is not None


def test_import_core_modules():
    """Test that core submodules can be imported."""
    from core.execution import Broker, SimBroker, SimBrokerConfig
    from core.execution.costs import _apply_fees, _apply_slippage
    from core.types import Account, TradeRow

    assert Broker is not None
    assert SimBroker is not None
    assert SimBrokerConfig is not None
    assert _apply_fees is not None
    assert _apply_slippage is not None
    assert Account is not None
    assert TradeRow is not None


def test_import_strategies_modules():
    """Test that strategies can be imported."""
    from strategies.base import PositionState, Strategy, get_strategy_class, register_strategy
    from strategies.momentum import MomentumStrategy

    assert Strategy is not None
    assert PositionState is not None
    assert register_strategy is not None
    assert get_strategy_class is not None
    assert MomentumStrategy is not None


def test_import_brokers_modules():
    """Test that brokers can be imported."""
    from brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
    from brokers.binance_paper import BinancePaperBroker

    assert OrderRequest is not None
    assert OrderSide is not None
    assert OrderStatus is not None
    assert OrderType is not None
    assert BinancePaperBroker is not None


def test_import_data_modules():
    """Test that data modules can be imported."""
    from data.feeds.binance_trades import iter_trades
    from data.validate import validate

    assert iter_trades is not None
    assert validate is not None


def test_import_report_modules():
    """Test that report modules can be imported."""
    from report.metrics_compare import compare_runs

    assert compare_runs is not None

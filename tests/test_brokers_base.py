# tests/test_brokers_base.py

from __future__ import annotations

import pytest

from brokers.base import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)


def test_order_request_creation() -> None:
    """Test que OrderRequest se crea correctamente."""
    req = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=0.1,
    )
    assert req.symbol == "BTCUSDT"
    assert req.side == OrderSide.BUY
    assert req.order_type == OrderType.MARKET
    assert req.qty == 0.1


def test_order_request_with_limit() -> None:
    """Test OrderRequest con LIMIT."""
    req = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        qty=0.05,
        price=50000.0,
        tif=TimeInForce.GTC,
    )
    assert req.price == 50000.0
    assert req.tif == TimeInForce.GTC


def test_order_status_enum() -> None:
    """Test OrderStatus enum."""
    assert OrderStatus.NEW != OrderStatus.FILLED
    assert OrderStatus.CANCELED != OrderStatus.FILLED

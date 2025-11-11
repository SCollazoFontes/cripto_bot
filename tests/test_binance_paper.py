# tests/test_binance_paper.py
from __future__ import annotations

from typing import Dict

from brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType, SymbolFilters
from brokers.binance_paper import BinancePaperBroker


def test_binance_paper_broker_init() -> None:
    """Test que BinancePaperBroker se inicializa correctamente."""
    filters: Dict[str, SymbolFilters] = {
        "BTCUSDT": {
            "tick_size": 0.01,
            "step_size": 0.0001,
            "min_notional": 10.0,
            "min_qty": 0.0002,
        }
    }
    br = BinancePaperBroker(symbol_filters=filters)
    assert br.get_position("BTCUSDT") == 0.0


def test_binance_paper_get_account() -> None:
    """Test que get_account devuelve estructura correcta."""
    br = BinancePaperBroker()
    acct = br.get_account()
    assert "balances" in acct
    assert "USDT" in acct["balances"]
    assert "free" in acct["balances"]["USDT"]
    assert acct["balances"]["USDT"]["free"] > 0.0


def test_binance_paper_get_open_orders_empty() -> None:
    """Test que get_open_orders devuelve lista vacía al inicio."""
    br = BinancePaperBroker()
    orders = br.get_open_orders()
    assert isinstance(orders, list)
    assert len(orders) == 0

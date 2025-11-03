# src/tests/test_imbalance.py
"""
Tests para ImbalanceBarBuilder (desequilibrio comprador/vendedor).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bars.base import Trade
from bars.imbalance import ImbalanceBarBuilder


def _ts(i: int) -> datetime:
    """Genera timestamps espaciados en ms."""
    t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return t0 + timedelta(milliseconds=i)


def _trade(price: float, qty: float, i: int, buyer_maker: bool) -> Trade:
    """Crea un trade. buyer_maker=True => taker vendedor (signo -1)."""
    return Trade(price=price, qty=qty, timestamp=_ts(i), is_buyer_maker=buyer_maker)


def test_imbalance_qty_closes_on_limit_and_builds_ohlcv() -> None:
    """
    imbal_limit=1.0 en modo qty. Secuencia de signos:
    - not buyer_maker => taker comprador => +qty
    - buyer_maker => taker vendedor => -qty
    """
    b = ImbalanceBarBuilder(imbal_limit=1.0, mode="qty")

    # +0.4, +0.3, -0.1, +0.5 => suma = 1.1 => cierra en 4º
    t1 = _trade(100.0, 0.4, 0, buyer_maker=False)  # +0.4
    t2 = _trade(101.0, 0.3, 1, buyer_maker=False)  # +0.3
    t3 = _trade(99.5, 0.1, 2, buyer_maker=True)  # -0.1
    t4 = _trade(100.2, 0.5, 3, buyer_maker=False)  # +0.5

    assert b.update(t1) is None
    assert b.update(t2) is None
    assert b.update(t3) is None
    bar = b.update(t4)
    assert bar is not None

    assert bar.open == 100.0
    assert bar.high == 101.0
    assert bar.low == 99.5
    assert bar.close == 100.2
    assert bar.volume == pytest.approx(0.4 + 0.3 + 0.1 + 0.5)
    assert bar.trade_count == 4
    assert bar.start_time == t1.timestamp
    assert bar.end_time == t4.timestamp


def test_imbalance_tick_mode() -> None:
    """En modo tick, cada trade suma ±1. Cierra cuando |∑ signo| ≥ imbal_limit."""
    b = ImbalanceBarBuilder(imbal_limit=3.0, mode="tick")

    # signos: +1, +1, -1, +1 => suma = +2 (no cierra). Añadimos +1 => cierra.
    t1 = _trade(100.0, 0.4, 0, buyer_maker=False)  # +1
    t2 = _trade(101.0, 0.3, 1, buyer_maker=False)  # +1
    t3 = _trade(99.5, 0.1, 2, buyer_maker=True)  # -1
    t4 = _trade(100.2, 0.5, 3, buyer_maker=False)  # +1
    t5 = _trade(100.8, 0.2, 4, buyer_maker=False)  # +1 (cierra)

    assert b.update(t1) is None
    assert b.update(t2) is None
    assert b.update(t3) is None
    assert b.update(t4) is None
    bar = b.update(t5)
    assert bar is not None

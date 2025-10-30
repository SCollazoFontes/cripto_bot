# src/tests/test_dollar.py
"""
Tests para DollarBarBuilder (micro-velas por valor negociado: ∑ price * qty).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bars.base import Trade
from bars.dollar import DollarBarBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(i: int) -> datetime:
    """Genera timestamps espaciados en ms."""
    t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return t0 + timedelta(milliseconds=i)


def _trade(price: float, qty: float, i: int) -> Trade:
    """Crea un trade sintético."""
    return Trade(price=price, qty=qty, timestamp=_ts(i), is_buyer_maker=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dollar_closes_on_value_limit_and_builds_ohlcv() -> None:
    """
    Verifica cierre cuando ∑ (price * qty) alcanza el umbral.
    value_limit = 100.0
    Trades: (100*0.3)=30, (102*0.4)=40.8, (99*0.5)=49.5 → total=120.3 (cierra en 3º)
    """
    builder = DollarBarBuilder(value_limit=100.0)

    t1 = _trade(100.0, 0.3, 0)
    t2 = _trade(102.0, 0.4, 1)
    t3 = _trade(99.0, 0.5, 2)

    assert builder.update(t1) is None
    assert builder.update(t2) is None
    bar = builder.update(t3)
    assert bar is not None

    assert bar.open == 100.0
    assert bar.high == 102.0
    assert bar.low == 99.0
    assert bar.close == 99.0
    # volumen es la suma de qty, no de valor
    assert bar.volume == pytest.approx(0.3 + 0.4 + 0.5)
    assert bar.trade_count == 3
    assert bar.start_time == t1.timestamp
    assert bar.end_time == t3.timestamp


def test_dollar_resets_after_close_and_continues() -> None:
    """Tras cerrar, el estado debe reiniciarse y permitir nuevas barras."""
    builder = DollarBarBuilder(value_limit=50.0)

    # Cierra con un único trade grande
    t1 = _trade(100.0, 0.6, 0)  # valor = 60
    bar1 = builder.update(t1)
    assert bar1 is not None
    assert builder.get_current_trades() == []

    # Segunda barra también cierra con un único trade
    t2 = _trade(120.0, 0.5, 1)  # valor = 60
    bar2 = builder.update(t2)
    assert bar2 is not None
    assert bar2.trade_count == 1
    assert bar2.open == 120.0
    assert bar2.close == 120.0


def test_dollar_invalid_limit() -> None:
    """Debe lanzar error si el límite es <= 0 o no numérico."""
    with pytest.raises(ValueError):
        DollarBarBuilder(value_limit=0)

    with pytest.raises(TypeError):
        DollarBarBuilder(value_limit="abc")

"""
Tests unitarios para VolumeQtyBarBuilder (micro-velas por volumen acumulado).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bars.base import Trade
from bars.volume_qty import VolumeQtyBarBuilder

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


def test_volume_qty_closes_on_limit_and_builds_correct_ohlcv() -> None:
    """Verifica que se cierra al alcanzar el límite de volumen acumulado."""
    builder = VolumeQtyBarBuilder(qty_limit=1.0)

    # Total = 0.4 + 0.3 + 0.5 = 1.2 → cierre en el tercer trade
    t1 = _trade(100.0, 0.4, 0)
    t2 = _trade(101.0, 0.3, 1)
    t3 = _trade(99.5, 0.5, 2)

    assert builder.update(t1) is None
    assert builder.update(t2) is None
    bar = builder.update(t3)
    assert bar is not None

    # OHLCV y tiempos
    assert bar.open == 100.0
    assert bar.high == 101.0
    assert bar.low == 99.5
    assert bar.close == 99.5
    assert bar.volume == pytest.approx(1.2)
    assert bar.trade_count == 3
    assert bar.start_time == t1.timestamp
    assert bar.end_time == t3.timestamp


def test_volume_qty_resets_after_close() -> None:
    """Tras cerrar, debe reiniciarse el estado interno."""
    builder = VolumeQtyBarBuilder(qty_limit=1.0)
    t1 = _trade(100.0, 1.0, 0)
    bar1 = builder.update(t1)
    assert bar1 is not None

    assert builder.get_current_trades() == []
    t2 = _trade(101.0, 1.0, 1)
    bar2 = builder.update(t2)
    assert bar2 is not None
    assert bar2.trade_count == 1


def test_volume_qty_invalid_limit() -> None:
    """Debe lanzar error si el límite es <= 0 o no numérico."""
    with pytest.raises(ValueError):
        VolumeQtyBarBuilder(qty_limit=0)

    with pytest.raises(TypeError):
        VolumeQtyBarBuilder(qty_limit="abc")

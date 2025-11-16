from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _trade(price: float, qty: float, t=None, buyer_maker=False):
    from bars.base import Trade

    return Trade(
        price=price,
        qty=qty,
        timestamp=t or datetime.now(timezone.utc),
        is_buyer_maker=buyer_maker,
    )


def test_composite_any_policy_tick_or_qty():
    from bars.builders import CompositeBarBuilder

    b = CompositeBarBuilder(tick_limit=3, qty_limit=5.0, policy="any")

    # 1) Acumular sin cerrar
    assert b.update(_trade(100, 2.0)) is None
    assert b.update(_trade(101, 2.0)) is None
    # 3) Aquí tick_limit=3 se alcanza (aunque qty_sum=4.0 < 5.0) → cierra por 'any'
    bar = b.update(_trade(102, 1.0))
    assert bar is not None
    assert bar.trade_count == 3
    assert bar.volume == pytest.approx(5.0)

    # Siguiente secuencia: cerrar por qty antes que ticks
    assert b.update(_trade(100, 2.5)) is None
    # 2º trade supera qty_limit=5.0 (2.5+3.0=5.5) con solo 2 trades → cierra
    bar = b.update(_trade(101, 3.0))
    assert bar is not None
    assert bar.trade_count == 2
    assert bar.volume == pytest.approx(5.5)


def test_composite_all_policy():
    from bars.builders import CompositeBarBuilder

    b = CompositeBarBuilder(tick_limit=3, qty_limit=5.0, policy="all")

    # 1) ticks=1, qty=2 -> no
    assert b.update(_trade(100, 2.0)) is None
    # 2) ticks=2, qty=4 -> no
    assert b.update(_trade(101, 2.0)) is None
    # 3) ticks=3, qty=5 -> ahora sí (ambas reglas alcanzadas)
    bar = b.update(_trade(102, 1.0))
    assert bar is not None
    assert bar.trade_count == 3
    assert bar.volume == pytest.approx(5.0)


def test_composite_with_imbalance_qty_any():
    from bars.builders import CompositeBarBuilder

    # imbal_limit=2 en modo qty; buyer_maker False => taker comprador => signo +1
    b = CompositeBarBuilder(imbal_limit=2.0, imbal_mode="qty", policy="any")

    assert b.update(_trade(100, 0.5, buyer_maker=False)) is None  # +0.5
    assert b.update(_trade(100, 0.6, buyer_maker=False)) is None  # +1.1
    bar = b.update(_trade(100, 1.0, buyer_maker=False))  # +2.1 → cierra
    assert bar is not None
    assert bar.volume == pytest.approx(2.1)


def test_composite_validation():
    from bars.builders import CompositeBarBuilder

    with pytest.raises(ValueError):
        CompositeBarBuilder()  # no thresholds
    with pytest.raises(ValueError):
        CompositeBarBuilder(tick_limit=0)
    with pytest.raises(ValueError):
        CompositeBarBuilder(qty_limit=0)
    with pytest.raises(ValueError):
        CompositeBarBuilder(value_limit=0)
    with pytest.raises(ValueError):
        CompositeBarBuilder(imbal_limit=0)
    with pytest.raises(ValueError):
        CompositeBarBuilder(imbal_limit=1, imbal_mode="bad")
    with pytest.raises(ValueError):
        CompositeBarBuilder(tick_limit=1, policy="nope")

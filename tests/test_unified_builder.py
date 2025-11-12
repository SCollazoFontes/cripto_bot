from __future__ import annotations

import pytest


def test_make_tick_builder():
    from bars import make

    b = make("tick", limit=5)
    # TickCountBarBuilder has attribute tick_limit
    assert hasattr(b, "tick_limit")
    assert b.tick_limit == 5


def test_make_volume_builder():
    from bars import make

    b = make("volume", limit=2.5)
    assert hasattr(b, "qty_limit")
    assert b.qty_limit == 2.5


def test_make_dollar_builder():
    from bars import make

    b = make("dollar", limit=100.0)
    assert hasattr(b, "value_limit")
    assert b.value_limit == 100.0


def test_make_imbalance_qty_builder():
    from bars import make

    b = make("imbalance", limit=3.0, mode="qty")
    assert hasattr(b, "imbal_limit")
    assert b.imbal_limit == 3.0
    assert getattr(b, "mode", "qty") == "qty"


def test_make_imbalance_tick_builder():
    from bars import make

    b = make("imbalance", limit=7, mode="tick")
    assert hasattr(b, "imbal_limit")
    assert b.imbal_limit == 7.0
    assert getattr(b, "mode", "qty") == "tick"


def test_make_invalid_rule():
    from bars import make

    with pytest.raises(ValueError):
        make("no_such_rule", limit=1)

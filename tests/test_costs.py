from __future__ import annotations

from core.execution.costs import (
    CostModel,
    SlippageModel,
    apply_fees,
    apply_slippage,
    estimate_costs,
)


def test_apply_fees_basic():
    net, fee = apply_fees(1000, fee_bps=10)
    assert fee == 1.0
    assert net == 999.0


def test_apply_slippage_buy_sell_symmetry():
    p_buy = apply_slippage(100.0, "buy", slippage_bps=50)
    p_sell = apply_slippage(100.0, "sell", slippage_bps=50)
    assert p_buy == 100.0 * 1.005
    assert p_sell == 100.0 * 0.995


def test_estimate_costs_components():
    d = estimate_costs(notional=2000, side="buy", fee_bps=5, slippage_bps=10)
    assert round(d["fee_amount"], 4) == 1.0  # 5 bps
    assert round(d["slippage_amount"], 4) == 2.0  # 10 bps
    assert round(d["total_cost_abs"], 4) == 3.0


def test_cost_model_maker_vs_taker():
    cm = CostModel(
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0006,
        maker_slip=SlippageModel(mode="fixed_bps", fixed_bps=0.0),
        taker_slip=SlippageModel(mode="fixed_bps", fixed_bps=10.0),
    )
    maker_fee = cm.fee_amount(notional=10_000, role="maker")
    taker_fee = cm.fee_amount(notional=10_000, role="taker")
    assert maker_fee == 2.0
    assert round(taker_fee, 6) == 6.0

    px_maker_buy = cm.effective_price(base_price=100.0, side="buy", role="maker")
    px_taker_buy = cm.effective_price(base_price=100.0, side="buy", role="taker")
    assert px_maker_buy == 100.0  # sin slippage
    assert round(px_taker_buy, 5) == round(100.0 * 1.001, 5)  # 10 bps


def test_cost_model_spread_frac():
    # Spread provider: spread = 0.50 (por ejemplo bid=100.00 ask=100.50)
    def sp(symbol: str | None = None) -> float:
        return 0.50

    cm = CostModel(
        maker_slip=SlippageModel(mode="spread_frac", spread_frac=0.25),  # 25% del spread = 0.125
        taker_slip=SlippageModel(mode="spread_frac", spread_frac=0.50),  # 50% del spread = 0.25
        spread_provider=sp,
    )
    # Asumimos base_price ~ mid=100 → rate ≈ 0.125 / 100 = 0.00125 (12.5 bps)
    px_maker_buy = cm.effective_price(base_price=100.0, side="buy", role="maker")
    px_taker_buy = cm.effective_price(base_price=100.0, side="buy", role="taker")
    assert round(px_maker_buy, 5) == round(100.0 * (1 + 0.00125), 5)
    assert round(px_taker_buy, 5) == round(100.0 * (1 + 0.0025), 5)

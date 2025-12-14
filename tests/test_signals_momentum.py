import pandas as pd

from strategies.signals import calculate_signal


def _make_df(close_prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": range(len(close_prices)),
            "open": close_prices,
            "high": close_prices,
            "low": close_prices,
            "close": close_prices,
            "volume": [1.0] * len(close_prices),
            "trade_count": [1] * len(close_prices),
            "dollar_value": [p for p in close_prices],
            "start_time": range(len(close_prices)),
            "end_time": range(len(close_prices)),
            "duration_ms": [1000] * len(close_prices),
        }
    )


def test_momentum_signal_requires_min_bars():
    df = _make_df([100.0] * 8)  # fewer than the 10-bar minimum
    signal, zone, meta = calculate_signal("momentum", df, params={"lookback_ticks": 20})

    assert signal == 0.0
    assert zone == "NEUTRAL"
    assert meta.get("reason") == "insufficient data"


def test_momentum_signal_low_volatility_metadata():
    prices = [100.0 + (i * 0.00001) for i in range(15)]  # tiny drift -> low vol
    df = _make_df(prices)

    signal, zone, meta = calculate_signal(
        "momentum",
        df,
        params={
            "lookback_ticks": 10,
            "entry_threshold": 0.0001,
            "exit_threshold": 0.00005,
            "min_volatility": 0.0,  # allow calculation
        },
    )

    assert zone in {"LOW VOL", "NEUTRAL"}
    assert "momentum" in meta
    assert "volatility" in meta
    # momentum should be small but defined
    assert abs(meta["momentum"]) < 0.001
    # volatility should be small given near-flat prices
    assert meta["volatility"] < 0.001

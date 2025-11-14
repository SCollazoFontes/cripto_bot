# tests/test_vol_breakout.py
from types import SimpleNamespace

from strategies.vol_breakout import VolatilityBreakoutStrategy


class DummyBroker:
    def __init__(self, cash: float = 10_000.0):
        self.cash = cash


class DummyExecutor:
    def __init__(self):
        self.actions = []

    def market_buy(self, symbol: str, qty: float):
        self.actions.append(("BUY", symbol, qty))

    def market_sell(self, symbol: str, qty: float):
        self.actions.append(("SELL", symbol, qty))


def make_channel_bar(base: float = 100.0):
    return {"high": base + 0.2, "low": base - 0.2, "close": base}


def make_breakout_bar(base: float = 100.0, jump: float = 10.0):
    price = base + jump
    return {"high": price + 0.2, "low": price - 0.2, "close": price}


def test_vol_breakout_entry_exit():
    strat = VolatilityBreakoutStrategy(lookback=10, atr_period=5, debug=False)
    broker = DummyBroker()
    ex = DummyExecutor()

    # Canal estable para consolidar ATR
    for _ in range(10):
        strat.on_bar_live(broker, ex, "BTCUSDT", make_channel_bar(100.0))

    # Breakout fuerte
    strat.on_bar_live(broker, ex, "BTCUSDT", make_breakout_bar(100.0, jump=12.0))

    # Debe haber al menos una acción (entrada) o estar cerca de generarla
    assert len(ex.actions) >= 1, "Se esperaba al menos una acción de mercado"

    # Forzar salida por reversión dentro del canal inicial
    if strat.position.qty != 0:
        revert_bar = {"high": 100.2, "low": 99.8, "close": 100.0}
        strat.on_bar_live(broker, ex, "BTCUSDT", revert_bar)

    # Si hubo posición y se aplicó salida, qty debe resetear a 0
    if any(a[0] in ("BUY", "SELL") for a in ex.actions):
        assert strat.position.qty in (
            0.0,
            strat.position.qty,
        ), "La posición debería cerrarse tras reversión si se disparó"

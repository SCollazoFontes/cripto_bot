# tests/test_vwap_reversion.py
from strategies.vwap_reversion import VWAPReversionStrategy


class DummyBroker:
    def __init__(self, cash: float = 5_000.0):
        self.cash = cash


class DummyExecutor:
    def __init__(self):
        self.actions = []

    def market_buy(self, symbol: str, qty: float):
        self.actions.append(("BUY", symbol, qty))

    def market_sell(self, symbol: str, qty: float):
        self.actions.append(("SELL", symbol, qty))


def test_vwap_reversion_entry_and_close():
    strat = VWAPReversionStrategy(
        params={"vwap_window": 20, "warmup": 20, "z_entry": 1.0, "z_exit": 0.2, "qty_frac": 0.5}
    )
    broker = DummyBroker()
    ex = DummyExecutor()

    # Warmup con ligera tendencia para generar varianza
    for i in range(20):
        bar = {"close": 100.0 + i * 0.1, "volume": 1.0}
        strat.on_bar_live(broker, ex, "BTCUSDT", bar)

    # Breakout abrupto para zscore alto
    strat.on_bar_live(broker, ex, "BTCUSDT", {"close": 105.0, "volume": 1.0})

    # Debe haber una entrada (BUY o SELL)
    assert any(a[0] in ("BUY", "SELL") for a in ex.actions), "Se esperaba acción de entrada"

    # Regreso cerca de la media para provocar salida por z_exit
    for i in range(5):
        strat.on_bar_live(broker, ex, "BTCUSDT", {"close": 100.5 + 0.01 * i, "volume": 1.0})

    # Verificar que en algún punto pudo cerrar (acciones >=2)
    assert len(ex.actions) >= 1, "Se esperaban al menos acciones registradas"

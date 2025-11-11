# tools/diag_testnet_ticks.py
"""
Diagnóstico: descarga bid/ask de Binance Testnet (bookTicker) y los guarda en CSV.
No depende de clases del proyecto. Úsalo antes de conectar el broker/engine.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
import json
import os
import time
import urllib.request

BASE = "https://testnet.binance.vision"
SYMBOL = "BTCUSDT"
DURATION_SEC = 10.0
SLEEP_SEC = 0.25  # ~4 Hz


def get_book_ticker(symbol: str) -> tuple[float, float, float]:
    url = f"{BASE}/api/v3/ticker/bookTicker?symbol={symbol}"
    with urllib.request.urlopen(url, timeout=5) as r:
        data = json.loads(r.read().decode("utf-8"))
    bid = float(data["bidPrice"])
    ask = float(data["askPrice"])
    ts = time.time()  # testnet no devuelve ts en este endpoint
    return bid, ask, ts


def main() -> None:
    outdir = "reports/diag"
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "binance_ticks_testnet.csv")

    rows = []
    t0 = time.time()
    while time.time() - t0 < DURATION_SEC:
        bid, ask, ts = get_book_ticker(SYMBOL)
        mid = (bid + ask) / 2.0
        rows.append((ts, SYMBOL, bid, ask, mid))
        time.sleep(SLEEP_SEC)

    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "symbol", "bid", "ask", "mid"])
        w.writerows(rows)

    print(f"✅ Guardado {len(rows)} ticks en {csv_path}")
    if rows:
        ts0 = rows[0][0]
        ts1 = rows[-1][0]
        dt0 = datetime.fromtimestamp(ts0, tz=UTC)
        dt1 = datetime.fromtimestamp(ts1, tz=UTC)
        print(f"Rango UTC: {dt0} → {dt1}")


if __name__ == "__main__":
    main()

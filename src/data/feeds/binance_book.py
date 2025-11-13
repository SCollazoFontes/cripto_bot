# src/data/feeds/binance_book.py
"""
WebSocket de Binance para order book (bid/ask) en tiempo real.

Objetivo:
- Capturar best bid/ask para calcular spread dinámico
- Usado para estimar slippage realista en ejecuciones
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any

import websockets

# Evitamos depender de nombres de excepciones específicos de la versión

# Configuración
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 30
PING_INTERVAL_S = 20
PING_TIMEOUT_S = 10
MAX_QUEUE = 1024

WS_BASE_PROD = "wss://stream.binance.com:9443"
WS_BASE_TEST = "wss://stream.testnet.binance.vision"


@dataclass
class BookSnapshot:
    """Snapshot del best bid/ask."""

    symbol: str
    bid: float  # mejor precio de compra
    ask: float  # mejor precio de venta
    bid_qty: float
    ask_qty: float
    ts_ms: int

    @property
    def spread(self) -> float:
        """Spread absoluto (ask - bid)."""
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float:
        """Spread en basis points."""
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return 0.0
        return (self.spread / mid) * 10_000.0


def _make_book_url(symbol: str, *, testnet: bool) -> str:
    """Construye URL del WebSocket para bookTicker."""
    base = WS_BASE_TEST if testnet else WS_BASE_PROD
    stream = f"{symbol.lower()}@bookTicker"
    return f"{base}/ws/{stream}"


def _parse_book_message(msg: dict[str, Any]) -> BookSnapshot | None:
    """
    Parsea mensaje del stream bookTicker.

    Formato:
    {
      "u": 400900217,     // order book updateId
      "s": "BNBUSDT",     // symbol
      "b": "25.35190000", // best bid price
      "B": "31.21000000", // best bid qty
      "a": "25.36520000", // best ask price
      "A": "40.66000000"  // best ask qty
    }
    """
    try:
        if not isinstance(msg, dict):
            return None

        symbol = msg.get("s")
        bid = float(msg.get("b", 0))
        ask = float(msg.get("a", 0))
        bid_qty = float(msg.get("B", 0))
        ask_qty = float(msg.get("A", 0))

        if not symbol or bid <= 0 or ask <= 0:
            return None

        # Usar timestamp actual (el mensaje no tiene timestamp)
        import time

        ts_ms = int(time.time() * 1000)

        return BookSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            ts_ms=ts_ms,
        )
    except (KeyError, ValueError, TypeError):
        return None


async def iter_book(
    symbol: str,
    testnet: bool = False,
) -> AsyncIterator[BookSnapshot]:
    """
    Iterador asíncrono del order book (best bid/ask).

    Args:
        symbol: símbolo de Binance (ej. BTCUSDT)
        testnet: si True, conecta a testnet

    Yields:
        BookSnapshot con bid, ask, spread
    """
    url = _make_book_url(symbol, testnet=testnet)
    print(f"[binance_book] Conectando WS → {url}", flush=True)

    async def _consume(url: str) -> AsyncIterator[dict[str, Any]]:
        attempt = 0
        while True:
            try:
                async with websockets.connect(
                    url,
                    open_timeout=CONNECT_TIMEOUT_S,
                    close_timeout=3,
                    ping_interval=PING_INTERVAL_S,
                    ping_timeout=PING_TIMEOUT_S,
                    max_queue=MAX_QUEUE,
                ) as ws:
                    attempt = 0
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=READ_TIMEOUT_S)
                        if isinstance(raw, (bytes, bytearray)):
                            raw = raw.decode("utf-8", "ignore")
                        yield json.loads(raw)
            except Exception:
                attempt += 1
                sleep_s = min(20, 2 ** min(5, attempt))
                await asyncio.sleep(sleep_s)
                continue
            await asyncio.sleep(0.2)

    agen = _consume(url)

    try:
        async for msg in agen:
            snapshot = _parse_book_message(msg)
            if snapshot is not None:
                yield snapshot
    except asyncio.CancelledError:
        # Cancelación limpia solicitada por consumidor
        pass
    finally:
        try:
            aclose = getattr(agen, "aclose", None)
            if callable(aclose):
                await aclose()
        except Exception:
            pass

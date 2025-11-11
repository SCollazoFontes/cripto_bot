# src/data/feeds/binance_trades.py
"""
WebSocket público de Binance Spot (prod/testnet) para el stream de trades.

Objetivo:
- Emitir un generador asíncrono de trades individuales en tiempo real.
- Compatible con el pipeline del bot para construir micro-velas o análisis tick a tick.

Formato de salida:
    dict con claves:
        - 't': int (timestamp en ms del trade)
        - 'price': float (precio del trade)
        - 'qty': float (cantidad transada)
        - 'is_buyer_maker': bool (True si el comprador es el que puso la orden pasiva)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import math
from typing import Any

import websockets
from websockets.exceptions import InvalidMessage, InvalidStatusCode

# -------------------------
# Configuración y constantes
# -------------------------
CONNECT_TIMEOUT_S = 6
READ_TIMEOUT_S = 30
PING_INTERVAL_S = 20
PING_TIMEOUT_S = 10
FIRST_MSG_TIMEOUT_S = 12
MAX_QUEUE = 1024

WS_BASE_PROD = "wss://stream.binance.com:9443"
WS_BASE_TEST = "wss://stream.testnet.binance.vision"


# -------------------------
# Utilidades internas
# -------------------------
def _make_trade_url(symbol: str, *, testnet: bool) -> str:
    base = WS_BASE_TEST if testnet else WS_BASE_PROD
    stream = f"{symbol.lower()}@trade"
    return f"{base}/ws/{stream}"


def _bad_float(x: float) -> bool:
    """True si x es NaN o infinito (para sanitizar)."""
    return isinstance(x, float) and (math.isnan(x) or math.isinf(x))


def _map_trade(msg: dict[str, Any]) -> dict[str, Any] | None:
    """
    Mapea payload de trade de Binance → dict estándar del proyecto.

    Formatos posibles:
      1) Directo:
         {"e":"trade", ..., "t":..., "p":"...", "q":"...", "T":..., "m":true, ...}
      2) Envoltorio:
         {"stream":"...", "data":{...el de arriba...}}
    """
    if not isinstance(msg, dict) or ("p" not in msg and "data" not in msg):
        return None

    data = msg.get("data", msg)

    try:
        p = float(data["p"])
        q = float(data["q"])
        t_ms = int(data.get("T", data.get("E")))  # Trade time preferido
        is_buyer_maker = bool(data.get("m"))
    except Exception:
        return None

    if (p is None) or (q is None) or _bad_float(p) or _bad_float(q):
        return None

    return {"t": t_ms, "price": p, "qty": q, "is_buyer_maker": is_buyer_maker}


# -------------------------
# API pública
# -------------------------
async def iter_trades(
    symbol: str,
    testnet: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """
    Iterador asíncrono de trades públicos de Binance Spot.

    Args:
        symbol: símbolo de Binance (ej. BTCUSDT)
        testnet: si True, conecta a testnet

    Yields:
        dict con claves: t, price, qty, is_buyer_maker
    """
    url = _make_trade_url(symbol, testnet=testnet)
    print(f"[binance_trades] Conectando WS → {url}", flush=True)

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
            except (InvalidStatusCode, InvalidMessage) as e:
                code = getattr(e, "status_code", None)
                raise RuntimeError(f"Handshake/WS inválido (HTTP {code}) en {url}.") from e
            except (TimeoutError, websockets.ConnectionClosedError):
                attempt += 1
                sleep_s = min(20, 2 ** min(5, attempt))
                await asyncio.sleep(sleep_s)
                continue
            except Exception:
                attempt += 1
                await asyncio.sleep(3)
                continue
            finally:
                await asyncio.sleep(1)

    agen = _consume(url)

    try:
        while True:
            msg = await asyncio.wait_for(agen.__anext__(), timeout=FIRST_MSG_TIMEOUT_S)
            tick = _map_trade(msg)
            if tick is not None:
                yield tick
                break
    except TimeoutError as exc:
        net = "TESTNET" if testnet else "PROD"
        raise RuntimeError(f"Sin mensajes @trade en {FIRST_MSG_TIMEOUT_S}s ({net}).") from exc

    async for msg in agen:
        tick = _map_trade(msg)
        if tick is not None:
            yield tick

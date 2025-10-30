# ============================================================
# src/exchange/binance_stream.py — Cliente WS mínimo Binance Spot
# ------------------------------------------------------------
"""
Productor mínimo de trades de Binance vía WebSocket.

✅ Objetivo
-----------
- Conectar al WS de Binance (mainnet o testnet).
- Suscribir al canal de trades de un símbolo (`{symbol}@trade`).
- Normalizar cada mensaje a `bars.base.Trade`.
- Exponer un **generador asíncrono** para consumir trades con latencia mínima.

⚙️ Dependencias
---------------
Este módulo usa `websockets` (async). Instálalo una vez en tu entorno:

    pip install websockets

🧠 Diseño
--------
- `iter_trades(symbol: str, testnet: bool = False)` -> `AsyncIterator[Trade]`
    Generador asíncrono que:
    1) abre conexión WS,
    2) escucha mensajes `@trade`,
    3) normaliza y `yield` por cada trade,
    4) intenta reconectar con backoff si la conexión cae.

- Conexión directa por "single stream" (sin multiplexación `combined`), para evitar
  overhead. El endpoint exacto es:
    Mainnet: wss://stream.binance.com:9443/ws/{symbol}@trade
    Testnet: wss://testnet.binance.vision/ws/{symbol}@trade

- `symbol` debe ir en minúsculas y sin separador, p.ej.:
    "btcusdt", "ethusdt", "bnbusdt"
  (En Binance Spot, el canal es **case-insensitive**, pero normalizamos a minúsculas).

- Heartbeats/pings los gestiona `websockets` automáticamente; añadimos timeouts
  conservadores y reintentos con backoff exponencial y jitter.

🛡️ Notas de robustez
--------------------
- Si llega un mensaje inválido, se ignora (log DEBUG) sin romper el stream.
- Reconexión con backoff hasta un máximo (configurable).
- No se hace `subscribe` explícito: la URL /ws/{stream} ya implica suscripción.

📎 Ejemplo de uso (en otro archivo, p.ej. tools/run_stream.py)
--------------------------------------------------------------
    import asyncio
    from bars.registry import create_builder
    from exchange.binance_stream import iter_trades

    async def main():
        builder = create_builder("tick", tick_limit=100)
        async for trade in iter_trades("btcusdt", testnet=False):
            bar = builder.update(trade)
            if bar:
                print("BAR:", bar)

    if __name__ == "__main__":
        asyncio.run(main())
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import random
from typing import AsyncIterator, Optional

import websockets

from bars.base import Trade

# =============================================================================
# Constantes de conexión
# =============================================================================

MAINNET_WS = "wss://stream.binance.com:9443/ws"
TESTNET_WS = "wss://testnet.binance.vision/ws"

# Timeouts y backoff (valores conservadores y seguros por defecto)
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 30
MAX_BACKOFF_S = 20  # límite para el backoff exponencial
MAX_RETRIES = 0  # 0 = reconectar indefinidamente


# =============================================================================
# Utilidades
# =============================================================================


def _stream_url(symbol: str, testnet: bool) -> str:
    """
    Construye la URL del stream simple `{symbol}@trade`.
    - Binance acepta el símbolo en cualquier case; normalizamos a minúsculas.
    """
    base = TESTNET_WS if testnet else MAINNET_WS
    stream = f"{symbol.lower()}@trade"
    return f"{base}/{stream}"


def _normalize_trade(msg: dict) -> Optional[Trade]:
    try:
        price = float(msg["p"])
        qty = float(msg["q"])
        ts_ms = int(msg.get("T") or msg["E"])  # preferimos trade time; si no, event time
        is_buyer_maker = bool(msg["m"])
    except (KeyError, TypeError, ValueError):
        return None

    return Trade(
        price=price,
        qty=qty,
        timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
        is_buyer_maker=is_buyer_maker,
    )


async def _ws_consume(url: str) -> AsyncIterator[Trade]:
    """
    Consume indefinidamente el WS en `url`, rindiendo `Trade` por mensaje válido.
    Maneja timeouts de lectura para detectar sockets colgados.
    """
    async with websockets.connect(url, ping_interval=15, ping_timeout=10, close_timeout=5) as ws:
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=READ_TIMEOUT_S)
            except asyncio.TimeoutError:
                # No llegan mensajes en `READ_TIMEOUT_S`: forzamos ping o relectura
                # Reintentamos el bucle para mantener viva la conexión.
                continue

            # Mensaje → dict
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                # Mensaje no JSON: ignorar
                continue

            # Hay dos formatos posibles: mensaje directo del stream o sobre stream combinado.
            # Aquí usamos stream simple; esperamos un dict con campos @trade.
            trade = _normalize_trade(payload)
            if trade is not None:
                yield trade
            # else: ignorar silenciosamente (payload no era @trade o inválido)


# =============================================================================
# API pública
# =============================================================================


async def iter_trades(symbol: str, testnet: bool = False) -> AsyncIterator[Trade]:
    """
    Generador asíncrono de trades normalizados (latencia mínima).

    Estrategia:
    - Construir la URL de stream `{symbol}@trade`.
    - Conectar con timeout de apertura.
    - Leer y normalizar mensajes, yieldeando `Trade`.
    - Si hay caída de conexión, **reconectar con backoff exponencial + jitter**.

    Parámetros
    ----------
    symbol : str
        Símbolo spot de Binance, p.ej. "btcusdt", "ethusdt".
    testnet : bool, por defecto False
        Si True, conecta a `testnet.binance.vision`.

    Yields
    ------
    Trade
        Objeto normalizado con (price, qty, timestamp UTC, is_buyer_maker).
    """
    retries = 0
    url = _stream_url(symbol, testnet=testnet)

    while True:
        try:
            # Intento de conexión con timeout
            connect_task = _ws_consume(url)
            # Validar la apertura inicial esperando el primer `__anext__` con timeout de conexión.
            # Esto nos evita quedarnos colgados si el handshake no progresa.
            agen = connect_task.__aiter__()
            first = await asyncio.wait_for(agen.__anext__(), timeout=CONNECT_TIMEOUT_S)
            # Si llegó el primer trade, "devolvemos" ese y mantendremos el mismo generador
            yield first
            # Y el resto de trades ya sin timeout de conexión (solo el de lectura en _ws_consume)
            async for tr in agen:
                yield tr

        except (asyncio.TimeoutError, websockets.WebSocketException, OSError):
            # Caída o timeout → reconectar con backoff
            retries += 1
            if MAX_RETRIES and retries > MAX_RETRIES:
                # Si MAX_RETRIES > 0 y superamos el máximo, propagamos
                raise

            # Backoff exponencial con jitter [0,1)
            backoff = min((2 ** min(retries, 10)) + random.random(), MAX_BACKOFF_S)
            await asyncio.sleep(backoff)
            continue
        else:
            # Si salimos del bucle de lectura sin excepción (cierre limpio), reconectamos también
            retries = 0
            await asyncio.sleep(1.0)
            continue

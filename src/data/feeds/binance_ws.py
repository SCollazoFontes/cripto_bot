# src/data/feeds/binance_ws.py
"""
Feed en vivo/paper para velas de Binance con contrato equivalente a CSVFeed.

Objetivo
- Exponer un feed intercambiable con CSVFeed para tools/run_stream_portfolio.py.
- Emitir dicts con el formato estándar: {t, open, high, low, close, volume} (t=closeTime ms).

Contrato (igual que CSVFeed)
- Iteración síncrona; __next__ sin recursión y respetando stop().
- Warm-up opcional de últimas barras cerradas por REST para emitir datos inmediatos al iniciar.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import json
import queue
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_WS_AVAILABLE = False
_WS_IMPL: str | None = None  # "websockets" | "websocket-client"

try:

    import websockets

    _WS_AVAILABLE = True
    _WS_IMPL = "websockets"
except Exception:
    try:

        _WS_AVAILABLE = True
        _WS_IMPL = "websocket-client"
    except Exception:
        _WS_AVAILABLE = False
        _WS_IMPL = None


@dataclass
class _Bar:
    t: int
    o: float
    h: float
    low: float
    c: float
    v: float

    def as_dict(self) -> dict[str, float]:
        return {
            "t": int(self.t),
            "open": float(self.o),
            "high": float(self.h),
            "low": float(self.low),
            "close": float(self.c),
            "volume": float(self.v),
        }


class BinanceWSFeed(Iterator[dict[str, float]]):
    def __init__(
        self,
        symbol: str,
        interval: str = "1m",
        *,
        max_queue: int = 10_000,
        ws_url: str = "wss://stream.binance.com:9443/ws",
        rest_url: str = "https://api.binance.com/api/v3/klines",
        log: Callable[[str], None] | None = None,
        reconnect_backoff_s: float = 1.0,
        reconnect_backoff_max_s: float = 30.0,
        request_timeout_s: float = 10.0,
        rest_poll_sleep_s: float = 0.25,
        warmup_bars: int = 3,
    ):
        self.symbol = symbol.upper()
        self.interval = interval
        self.ws_url = ws_url.rstrip("/")
        self.rest_url = rest_url
        self._log = log or (lambda *_: None)
        self._q: queue.Queue[dict[str, float]] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._started = False
        self._thread: threading.Thread | None = None
        self._reconnect_backoff_s = reconnect_backoff_s
        self._reconnect_backoff_max_s = reconnect_backoff_max_s
        self._request_timeout_s = request_timeout_s
        self._rest_poll_sleep_s = rest_poll_sleep_s
        self._warmup_bars = max(0, int(warmup_bars))
        self._last_enqueued_close_t: int | None = None

    def __enter__(self) -> BinanceWSFeed:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def __iter__(self) -> BinanceWSFeed:
        if not self._started:
            self.start()
        return self

    def __next__(self) -> dict[str, float]:
        # Iterativo y respetando stop(); marcador {} desbloquea de inmediato
        while True:
            if self._stop.is_set() and self._q.empty():
                raise StopIteration
            try:
                item = self._q.get(timeout=1.0)
                if item:
                    return item
                # item vacío → marcador de stop
                if self._stop.is_set():
                    raise StopIteration
            except queue.Empty:
                if self._stop.is_set():
                    raise StopIteration from None
                continue

    def start(self) -> None:
        if self._started:
            return
        self._started = True

        if self._warmup_bars > 0:
            try:
                for b in self._rest_fetch_last_closed(self._warmup_bars):
                    self._enqueue(b)
                    self._last_enqueued_close_t = int(b["t"])
                self._log("[BinanceWSFeed] Warm-up emitido.")
            except Exception as e:
                self._log(f"[BinanceWSFeed] Warm-up REST fallido: {e!r}")

        self._thread = threading.Thread(target=self._run, name="BinanceWSFeed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait({})
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        if _WS_AVAILABLE:
            self._log(f"[BinanceWSFeed] Usando backend WS: {_WS_IMPL}")
            self._run_ws_loop()
        else:
            self._log("[BinanceWSFeed] WS no disponible, usando REST polling.")
            self._run_rest_polling()

    def _run_ws_loop(self) -> None:
        stream = f"{self.symbol.lower()}@kline_{self.interval}"
        url = f"{self.ws_url}/{stream}"
        backoff = self._reconnect_backoff_s
        while not self._stop.is_set():
            try:
                if _WS_IMPL == "websockets":
                    self._ws_loop_websockets(url)
                else:
                    self._ws_loop_websocket_client(url)
                backoff = self._reconnect_backoff_s
            except Exception as e:
                self._log(f"[BinanceWSFeed] WS error: {e!r}. Reintentando en {backoff:.1f}s")
                time.sleep(backoff)
                backoff = min(self._reconnect_backoff_max_s, backoff * 2.0)

    def _ws_loop_websockets(self, url: str) -> None:
        assert _WS_IMPL == "websockets"
        import asyncio

        async def _runner():
            while not self._stop.is_set():
                try:
                    async with websockets.connect(url, ping_interval=20) as ws:
                        self._log("[BinanceWSFeed] Conectado (websockets).")
                        async for message in ws:
                            if self._stop.is_set():
                                break
                            self._handle_ws_message(message)
                except Exception as e:
                    self._log(f"[BinanceWSFeed] websockets error: {e!r}")
                    await asyncio.sleep(1.0)

        asyncio.run(_runner())

    def _ws_loop_websocket_client(self, url: str) -> None:
        assert _WS_IMPL == "websocket-client"
        import websocket

        def on_message(ws, message):
            if self._stop.is_set():
                ws.close()
                return
            try:
                self._handle_ws_message(message)
            except Exception as e:
                self._log(f"[BinanceWSFeed] on_message error: {e!r}")

        def on_error(ws, error):
            self._log(f"[BinanceWSFeed] websocket-client error: {error!r}")

        def on_close(ws, status_code, msg):
            self._log(f"[BinanceWSFeed] WS cerrado: {status_code} {msg}")

        while not self._stop.is_set():
            ws = websocket.WebSocketApp(
                url, on_message=on_message, on_error=on_error, on_close=on_close
            )
            self._log("[BinanceWSFeed] Conectando (websocket-client)...")
            ws.run_forever(ping_interval=20, ping_timeout=10)
            if not self._stop.is_set():
                time.sleep(1.0)

    def _handle_ws_message(self, message: str) -> None:
        data = json.loads(message)
        k = data.get("k")
        if not k:
            return
        if not bool(k.get("x", False)):
            return
        close_t = int(k.get("T"))
        if self._last_enqueued_close_t is not None and close_t <= self._last_enqueued_close_t:
            return
        bar = _Bar(
            t=close_t,
            o=float(k.get("o")),
            h=float(k.get("h")),
            low=float(k.get("l")),
            c=float(k.get("c")),
            v=float(k.get("v")),
        ).as_dict()
        self._enqueue(bar)
        self._last_enqueued_close_t = close_t

    def _run_rest_polling(self) -> None:
        last_close_t: int | None = self._last_enqueued_close_t
        backoff = self._reconnect_backoff_s
        while not self._stop.is_set():
            try:
                bars = self._rest_fetch_last_closed(2)
                if bars:
                    last = bars[-1]
                    close_t = int(last["t"])
                    if last_close_t is None or close_t > last_close_t:
                        self._enqueue(last)
                        last_close_t = close_t
                        self._last_enqueued_close_t = int(close_t)
                time.sleep(self._rest_poll_sleep_s)
                backoff = self._reconnect_backoff_s
            except (HTTPError, URLError, TimeoutError, ValueError) as e:
                self._log(f"[BinanceWSFeed] REST error: {e!r}. Reintento en {backoff:.1f}s")
                time.sleep(backoff)
                backoff = min(self._reconnect_backoff_max_s, backoff * 2.0)
            except Exception as e:
                self._log(
                    f"[BinanceWSFeed] REST error inesperado: {e!r}. Reintento en {backoff:.1f}s"
                )
                time.sleep(backoff)
                backoff = min(self._reconnect_backoff_max_s, backoff * 2.0)

    def _rest_fetch_last_closed(self, limit: int) -> list[dict[str, float]]:
        params = {"symbol": self.symbol, "interval": self.interval, "limit": max(1, int(limit))}
        url = f"{self.rest_url}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "cripto_bot/1.0"})
        with urlopen(req, timeout=self._request_timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, list) or len(payload) == 0:
            raise ValueError("Respuesta REST inesperada")
        out: list[dict[str, float]] = []
        for row in payload[-limit:]:
            out.append(
                _Bar(
                    t=int(row[6]),
                    o=float(row[1]),
                    h=float(row[2]),
                    low=float(row[3]),
                    c=float(row[4]),
                    v=float(row[5]),
                ).as_dict()
            )
        return out

    def _enqueue(self, bar: dict[str, float]) -> None:
        try:
            self._q.put(bar, timeout=1.0)
        except queue.Full:
            try:
                _ = self._q.get_nowait()
                self._q.put_nowait(bar)
                self._log("[BinanceWSFeed] Queue llena: descartada una barra antigua.")
            except Exception:
                pass

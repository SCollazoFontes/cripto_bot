# src/core/spread_tracker.py
"""
Rastreador de spread bid-ask en tiempo real.

Usado por el modelo de costes para calcular slippage dinámico
basado en condiciones reales del mercado.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Any

from data.feeds.binance_book import iter_book


@dataclass
class SpreadStats:
    """Estadísticas del spread en una ventana de tiempo."""

    current_spread_bps: float
    avg_spread_bps: float
    min_spread_bps: float
    max_spread_bps: float
    samples: int


class SpreadTracker:
    """
    Rastreador de spread en tiempo real con buffer circular.

    Mantiene histórico de spreads para calcular promedios y usar
    en modelos de slippage dinámico.
    """

    def __init__(
        self,
        symbol: str,
        window_size: int = 100,
        testnet: bool = False,
    ):
        self.symbol = symbol
        self.window_size = window_size
        self.testnet = testnet

        # Buffer circular de spreads (en bps)
        self._spreads: deque[float] = deque(maxlen=window_size)
        self._current_spread_bps: float = 5.0  # default conservador
        self._last_update: float = 0.0
        self._lock = threading.Lock()

        # Estado del thread
        self._task: asyncio.Task[Any] | None = None
        self._running = False

    def get_spread(self, symbol: str | None = None) -> float:
        """
        Obtiene el spread actual en valor absoluto (no bps).

        Para compatibilidad con SpreadProvider Protocol.
        Nota: Retorna spread en bps porque es más útil para cálculos.
        """
        with self._lock:
            return self._current_spread_bps

    def get_stats(self) -> SpreadStats:
        """Obtiene estadísticas del spread en la ventana actual."""
        with self._lock:
            if not self._spreads:
                return SpreadStats(
                    current_spread_bps=self._current_spread_bps,
                    avg_spread_bps=self._current_spread_bps,
                    min_spread_bps=self._current_spread_bps,
                    max_spread_bps=self._current_spread_bps,
                    samples=0,
                )

            spreads_list = list(self._spreads)
            return SpreadStats(
                current_spread_bps=self._current_spread_bps,
                avg_spread_bps=sum(spreads_list) / len(spreads_list),
                min_spread_bps=min(spreads_list),
                max_spread_bps=max(spreads_list),
                samples=len(spreads_list),
            )

    async def _consume_book(self) -> None:
        """Consume el feed de order book y actualiza spreads."""
        try:
            print(f"[SpreadTracker] Iniciando rastreo de spread para {self.symbol}", flush=True)
            count = 0
            async for snapshot in iter_book(self.symbol, testnet=self.testnet):
                with self._lock:
                    spread_bps = snapshot.spread_bps
                    if spread_bps > 0:  # Solo actualizar si el spread es válido
                        self._current_spread_bps = spread_bps
                        self._spreads.append(spread_bps)
                        self._last_update = time.time()
                        count += 1

                        # Log inicial para debug
                        if count <= 3:
                            print(
                                f"[SpreadTracker] #{count}: bid={snapshot.bid:.2f}, "
                                f"ask={snapshot.ask:.2f}, spread={spread_bps:.4f} bps",
                                flush=True,
                            )

                if not self._running:
                    break
        except asyncio.CancelledError:
            print(f"[SpreadTracker] Detenido para {self.symbol}", flush=True)
        except Exception as e:
            print(f"[SpreadTracker] Error: {e}", flush=True)
            import traceback

            traceback.print_exc()

    def start_background(self, loop: asyncio.AbstractEventLoop) -> None:
        """Inicia el rastreo en background en el event loop dado."""
        if self._running:
            return

        self._running = True
        self._task = loop.create_task(self._consume_book())

    async def start(self) -> None:
        """Inicia el rastreo (versión async)."""
        if self._running:
            return

        self._running = True
        await self._consume_book()

    def stop(self) -> None:
        """Detiene el rastreo."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

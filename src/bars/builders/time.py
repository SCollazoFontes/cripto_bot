"""
TimeBarBuilder: agrega trades en ventanas de tiempo fijas (p. ej. 1s).

Propósito: generar velas para gráficos a cadencia constante desde trades brutos,
independientemente del builder usado por la estrategia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from bars.base import Bar, BarBuilder, Trade

__all__ = ["TimeBarBuilder"]


@dataclass
class TimeBarBuilder(BarBuilder):
    period_ms: int = 1000
    _buffer: list[Trade] = field(default_factory=list, init=False, repr=False)
    _bucket_start_ms: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.period_ms <= 0:
            raise ValueError("period_ms debe ser > 0")

    def _bucket_of(self, ts: datetime) -> int:
        ms = int(ts.timestamp() * 1000)
        return (ms // self.period_ms) * self.period_ms

    def update(self, trade: Trade) -> Bar | None:
        bucket = self._bucket_of(trade.timestamp)

        if self._bucket_start_ms is None:
            self._bucket_start_ms = bucket
            self._buffer.append(trade)
            return None

        if bucket == self._bucket_start_ms:
            self._buffer.append(trade)
            return None

        # Cambió de bucket: cerrar barra previa
        bar = self._build_bar(self._buffer)
        self._buffer = [trade]
        self._bucket_start_ms = bucket
        return bar

    def reset(self) -> None:
        self._buffer.clear()
        self._bucket_start_ms = None

    def flush_partial(self) -> Bar | None:
        """Force-close the current bucket and return a bar if any trades exist.

        Useful when shutting down the stream so the last in-progress bucket is
        not lost if no new trade arrives to advance the time window.
        """
        if not self._buffer:
            return None
        bar = self._build_bar(self._buffer)
        self.reset()
        return bar

    def get_current_trades(self) -> list[Trade]:
        return list(self._buffer)

    @staticmethod
    def _build_bar(trades: list[Trade]) -> Bar:
        if not trades:
            raise ValueError("No hay trades para construir la barra de tiempo.")
        first = trades[0]
        last = trades[-1]
        prices = [t.price for t in trades]
        volume = sum(t.qty for t in trades)
        dval = sum(t.price * t.qty for t in trades)
        return Bar(
            open=first.price,
            high=max(prices),
            low=min(prices),
            close=last.price,
            volume=volume,
            start_time=first.timestamp,
            end_time=last.timestamp,
            trade_count=len(trades),
            dollar_value=dval,
        )

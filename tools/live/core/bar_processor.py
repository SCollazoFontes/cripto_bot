"""Bar processor for composite bar building."""

from __future__ import annotations

from datetime import UTC, datetime

from bars.base import Trade
from bars.builders import CompositeBarBuilder


class BarProcessor:
    """Processes trades and builds composite bars."""

    def __init__(
        self,
        tick_limit: int | None = None,
        qty_limit: float | None = None,
        value_limit: float | None = None,
        imbal_limit: float | None = None,
        policy: str = "any",
    ):
        """
        Initialize bar processor.

        Args:
            tick_limit: Tick count threshold
            qty_limit: Volume (quantity) threshold
            value_limit: Dollar value threshold
            imbal_limit: Imbalance threshold
            policy: Closing policy ("any" or "all")
        """
        self.builder = CompositeBarBuilder(
            tick_limit=tick_limit,
            qty_limit=qty_limit,
            value_limit=value_limit,
            imbal_limit=imbal_limit,
            policy=policy,
        )
        self._bar_count = 0

    def process_trade(self, trade: Trade) -> dict | None:
        """
        Process a trade and potentially emit a bar.

        Args:
            trade: Trade to process

        Returns:
            Bar dict if bar closed, None otherwise
        """
        bar = self.builder.on_trade(trade)
        if bar is not None:
            self._bar_count += 1
            return self._bar_to_dict(bar)
        return None

    def _bar_to_dict(self, bar: tuple) -> dict:
        """Convert bar tuple to dictionary."""
        (
            bar_open,
            bar_high,
            bar_low,
            bar_close,
            bar_volume,
            bar_trade_count,
            bar_dollar_value,
            bar_start_ts,
            bar_end_ts,
            bar_duration_ms,
        ) = bar

        return {
            "timestamp": datetime.fromtimestamp(bar_end_ts / 1000, tz=UTC).isoformat(),
            "open": bar_open,
            "high": bar_high,
            "low": bar_low,
            "close": bar_close,
            "volume": bar_volume,
            "trade_count": bar_trade_count,
            "dollar_value": bar_dollar_value,
            "start_time": bar_start_ts,
            "end_time": bar_end_ts,
            "duration_ms": bar_duration_ms,
        }

    @property
    def bar_count(self) -> int:
        """Get total number of bars emitted."""
        return self._bar_count

    def get_description(self) -> str:
        """Get human-readable description of bar builder configuration."""
        active_rules = []
        if self.builder.tick_limit:
            active_rules.append(f"tick={self.builder.tick_limit}")
        if self.builder.qty_limit:
            active_rules.append(f"qty={self.builder.qty_limit:.3f}")
        if self.builder.value_limit:
            active_rules.append(f"value=${self.builder.value_limit:,.0f}")
        if self.builder.imbal_limit:
            active_rules.append(f"imbal={self.builder.imbal_limit:.2f}")

        return f"CompositeBarBuilder({', '.join(active_rules)}, policy='{self.builder.policy}')"

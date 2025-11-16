"""Feed handler for Binance WebSocket streams."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from bars.base import Trade
from data.feeds.binance_trades import iter_trades


class FeedHandler:
    """Manages WebSocket connection and trade feed."""

    def __init__(self, symbol: str, testnet: bool = False):
        """
        Initialize feed handler.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            testnet: Use testnet instead of mainnet
        """
        self.symbol = symbol
        self.testnet = testnet
        self._trade_count = 0

    async def stream_trades(self, duration: int) -> AsyncGenerator[Trade, None]:
        """
        Stream trades from Binance WebSocket.

        Args:
            duration: Maximum duration in seconds

        Yields:
            Trade objects
        """
        async for trade in iter_trades(
            symbol=self.symbol,
            testnet=self.testnet,
            max_duration=duration,
        ):
            self._trade_count += 1
            yield trade

    @property
    def trade_count(self) -> int:
        """Get total number of trades processed."""
        return self._trade_count

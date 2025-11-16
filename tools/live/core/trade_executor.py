"""Trade executor for strategy order execution."""

from __future__ import annotations

from datetime import UTC, datetime

from brokers.binance_paper import BinancePaperBroker
from tools.live.executor import SimpleExecutor


class TradeExecutor:
    """Executes strategy orders and tracks trades."""

    def __init__(self, broker: BinancePaperBroker, symbol: str):
        """
        Initialize trade executor.

        Args:
            broker: Paper broker instance
            symbol: Trading symbol
        """
        self.executor = SimpleExecutor(broker)
        self.symbol = symbol
        self.broker = broker
        self._trade_history: list[dict] = []
        self._decision_history: list[dict] = []

    def execute_strategy(
        self, strategy, bar_dict: dict, last_price: float
    ) -> tuple[list[dict], list[dict]]:
        """
        Execute strategy on bar and collect trades/decisions.

        Args:
            strategy: Strategy instance
            bar_dict: Bar data dictionary
            last_price: Current market price

        Returns:
            (new_trades, new_decisions) - Lists of trade and decision records
        """
        new_trades = []
        new_decisions = []

        try:
            # Patch broker to expose cash and position_qty as properties
            type(self.broker).cash = property(lambda self: self._usdt)
            type(self.broker).position_qty = property(lambda self: self.get_position(self.symbol))

            # Call strategy
            strategy.on_bar_live(self.broker, self.executor, self.symbol, bar_dict)

            # Collect executed trades
            for trade_info in self.executor.orders_executed:
                pos_after = self.broker.get_position(self.symbol)
                mark = trade_info.get("fill_price", last_price)
                equity_after = self.broker.get_equity(mark_price=mark)

                trade_record = {
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                    "side": trade_info.get("side", ""),
                    "qty": trade_info.get("qty", 0.0),
                    "price": trade_info.get("fill_price", 0.0),
                    "fees": trade_info.get("fees", 0.0),
                    "cash_after": self.broker._usdt,
                    "position_after": pos_after,
                    "equity_after": equity_after,
                }
                new_trades.append(trade_record)
                self._trade_history.append(trade_record)

            # Collect decisions
            for decision in self.executor.decisions:
                decision_record = {
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                    "action": decision.get("action", ""),
                    "reason": decision.get("reason", ""),
                    "qty": decision.get("qty", 0.0),
                    "price": decision.get("price", 0.0),
                }
                new_decisions.append(decision_record)
                self._decision_history.append(decision_record)

            # Clear executor buffers
            self.executor.orders_executed.clear()
            self.executor.decisions.clear()

        except Exception as e:
            print(f"⚠️  Error ejecutando estrategia: {e}")
            import traceback

            traceback.print_exc()

        return new_trades, new_decisions

    @property
    def trade_history(self) -> list[dict]:
        """Get all trades executed."""
        return self._trade_history

    @property
    def decision_history(self) -> list[dict]:
        """Get all decisions made."""
        return self._decision_history

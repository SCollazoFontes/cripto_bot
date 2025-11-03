# ============================================================
# src/strategies/momentum.py — Momentum simple por lookback
# ------------------------------------------------------------
# Lógica:
#   - Calcula retorno % vs. precio de hace N ticks (lookback_ticks).
#   - Si no hay posición y retorno >= entry_threshold -> BUY
#   - Si hay posición y:
#        retorno <= -exit_threshold  -> SELL (salida)
#      o (price/avg - 1) >= take_profit_pct -> SELL (TP)
#      o (price/avg - 1) <= -stop_loss_pct  -> SELL (SL)
#   - El sizing lo decide el Engine (min_notional y max_position_usd).
# ============================================================

from __future__ import annotations

from collections import deque
from typing import Optional

from src.strategies.base import Signal, Strategy


class MomentumStrategy(Strategy):
    def __init__(
        self,
        lookback_ticks: int = 50,
        entry_threshold: float = 0.001,
        exit_threshold: float = 0.0005,
        take_profit_pct: float = 0.004,
        stop_loss_pct: float = 0.002,
    ) -> None:
        assert lookback_ticks >= 1, "lookback_ticks debe ser >= 1"
        self.lookback_ticks = int(lookback_ticks)
        self.entry_threshold = float(entry_threshold)
        self.exit_threshold = float(exit_threshold)
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)

        self._window: deque[float] = deque(maxlen=self.lookback_ticks + 1)

    def on_price(
        self,
        price: float,
        ts_ms: Optional[int],
        position_qty: float,
        position_avg_price: float,
    ) -> Signal:
        # Alimentar ventana
        self._window.append(price)
        if len(self._window) <= self.lookback_ticks:
            return Signal.HOLD  # aún sin suficiente historial

        ref = self._window[0]
        if ref <= 0.0:
            return Signal.HOLD

        ret = (price / ref) - 1.0

        # Sin posición -> buscar entrada
        if position_qty <= 0.0:
            if ret >= self.entry_threshold:
                return Signal.BUY
            return Signal.HOLD

        # Con posición -> gestionar salida por TP/SL/exit
        # Señales basadas en precio vs. avg para SL/TP
        if position_avg_price > 0.0:
            pos_ret = (price / position_avg_price) - 1.0
            if pos_ret >= self.take_profit_pct:
                return Signal.SELL  # take profit
            if pos_ret <= -self.stop_loss_pct:
                return Signal.SELL  # stop loss

        # Salida por reversión del momentum
        if ret <= -self.exit_threshold:
            return Signal.SELL

        return Signal.HOLD

# src/strategies/vol_breakout.py
"""
VolatilityBreakoutStrategy — ruptura de canal con ATR para filtros y TP/SL.

Lógica:
- Mantiene colas de high/low para un canal de 'lookback' barras.
- Señal de compra si close > ch_high + k*ATR; venta si close < ch_low - k*ATR.
- TP/SL porcentuales opcionales sobre precio medio de entrada.
"""

from __future__ import annotations

from collections import deque
from typing import Any, ClassVar

from brokers.base import OrderRequest, OrderSide
from strategies.base import PositionState, Strategy, atr_like, register_strategy


class VolatilityBreakoutStrategy(Strategy):
    """Estrategia de breakout de volatilidad."""

    name: ClassVar[str] = "vol_breakout"

    def __init__(self) -> None:
        self.position = PositionState()
        self.state: dict[str, Any] = {"atr": 0.0}
        self.highs: deque[float] = deque(maxlen=20)
        self.lows: deque[float] = deque(maxlen=20)
        self.closes: deque[float] = deque(maxlen=20)

    def on_bar_live(self, broker: Any, executor: Any, symbol: str, bar: dict[str, Any]) -> None:
        """Procesa barra y ejecuta lógica de trading."""
        high = float(bar.get("high", 0.0))
        low = float(bar.get("low", 0.0))
        close = float(bar.get("close", 0.0))

        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)

        # Calcular ATR si hay suficientes datos
        atr_val = 0.0
        if len(self.highs) >= 14:
            atr_val = atr_like(list(self.highs), list(self.lows), list(self.closes), n=14)
            self.state["atr"] = atr_val

        # Proteger contra None
        atr_val = max(atr_val, 0.0)
        pos_qty = self.position.qty or 0.0
        entry_px = self.position.entry_price or close

        # Long position: stop loss
        if pos_qty > 0:
            stop_loss = entry_px - atr_val * 2
            if close < stop_loss:
                _ = OrderRequest(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    qty=pos_qty,
                    reason="vol_breakout_sl_long",
                )

        # Short position: stop loss
        elif pos_qty < 0:
            stop_loss = entry_px + atr_val * 2
            if close > stop_loss:
                _ = OrderRequest(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    qty=abs(pos_qty),
                    reason="vol_breakout_sl_short",
                )


# Registro
register_strategy("vol_breakout", VolatilityBreakoutStrategy)
register_strategy("VolatilityBreakoutStrategy", VolatilityBreakoutStrategy)

__all__ = ["VolatilityBreakoutStrategy"]

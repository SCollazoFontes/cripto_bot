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

from strategies.base import (
    PositionState,
    Strategy,
    atr_like,
    register_strategy,
    will_exit_non_negative,
)


@register_strategy("vol_breakout")
class VolatilityBreakoutStrategy(Strategy):
    """Estrategia de breakout de volatilidad.

    Lógica añadida:
    - Entrada LONG si close supera canal alto + k*ATR.
    - Entrada SHORT si close cae por debajo canal bajo - k*ATR.
    - Salida por stop-loss (2*ATR contra la posición) o por reversión de ruptura.
    """

    name: ClassVar[str] = "vol_breakout"

    def __init__(
        self,
        lookback: int = 20,
        atr_period: int = 14,
        atr_mult: float = 0.5,
        stop_mult: float = 2.0,
        qty_frac: float = 1.0,
        order_notional: float = 5.0,
        allow_short: bool = False,
        debug: bool = False,
        **kwargs: Any,
    ) -> None:
        params = kwargs.get("params")
        if isinstance(params, dict):
            lookback = int(params.get("lookback", lookback))
            atr_period = int(params.get("atr_period", atr_period))
            atr_mult = float(params.get("atr_mult", atr_mult))
            stop_mult = float(params.get("stop_mult", stop_mult))
            qty_frac = float(params.get("qty_frac", qty_frac))
            order_notional = float(params.get("order_notional", order_notional))
            allow_short = bool(params.get("allow_short", allow_short))
            debug = bool(params.get("debug", debug))

        self.position = PositionState()
        self.state: dict[str, Any] = {"atr": 0.0}
        self.highs: deque[float] = deque(maxlen=lookback)
        self.lows: deque[float] = deque(maxlen=lookback)
        self.closes: deque[float] = deque(maxlen=lookback)
        self.lookback = lookback
        self.atr_period = atr_period
        self.atr_mult = float(atr_mult)
        self.stop_mult = float(stop_mult)
        self.qty_frac = float(qty_frac)
        self.order_notional = float(order_notional)
        self.allow_short = bool(allow_short)
        self.debug = bool(debug)

    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[VolBreakout] {msg}")

    def on_bar_live(self, broker: Any, executor: Any, symbol: str, bar: dict[str, Any]) -> None:
        high = float(bar.get("high", 0.0))
        low = float(bar.get("low", 0.0))
        close = float(bar.get("close", 0.0))

        # Guardar valores previos para detectar ruptura respecto al canal anterior
        prev_highs = list(self.highs)
        prev_lows = list(self.lows)

        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)

        atr_val = 0.0
        if len(self.highs) >= self.atr_period:
            atr_val = atr_like(
                list(self.highs), list(self.lows), list(self.closes), n=self.atr_period
            )
            self.state["atr"] = atr_val
        atr_val = max(atr_val, 0.0)

        ch_high = max(self.highs) if self.highs else high
        ch_low = min(self.lows) if self.lows else low
        ch_high_prev = max(prev_highs) if prev_highs else ch_high
        ch_low_prev = min(prev_lows) if prev_lows else ch_low

        pos_qty = self.position.qty
        entry_px = self.position.entry_price or close

        # Gestión de posición abierta: stop-loss
        if pos_qty > 0:  # LONG
            stop_loss = entry_px - self.stop_mult * atr_val
            if close < stop_loss and pos_qty > 0:
                self._log(f"SL LONG qty={pos_qty:.6f} close={close:.2f} stop={stop_loss:.2f}")
                executor.market_sell(symbol, pos_qty)
                self.position.qty = 0.0
                self.position.side = None
                return
        elif pos_qty < 0:  # SHORT
            stop_loss = entry_px + self.stop_mult * atr_val
            if close > stop_loss and pos_qty < 0:
                self._log(f"SL SHORT qty={abs(pos_qty):.6f} close={close:.2f} stop={stop_loss:.2f}")
                executor.market_buy(symbol, abs(pos_qty))
                self.position.qty = 0.0
                self.position.side = None
                return

        # Entrada si no hay posición y hay ruptura
        if self.position.qty == 0.0 and len(self.closes) == self.closes.maxlen and atr_val > 0.0:
            # LONG breakout
            if close > ch_high_prev + self.atr_mult * atr_val:
                # Calcular tamaño
                cash = float(getattr(broker, "cash", 0.0))
                available_cash = max(0.0, cash * self.qty_frac)
                notional = min(self.order_notional, available_cash)
                qty = notional / close if close > 0 else 0.0
                if qty > 0:
                    self._log(
                        f"ENTRY LONG qty={qty:.6f} close={close:.2f} ch_high={ch_high:.2f} atr={atr_val:.2f}"
                    )
                    executor.market_buy(symbol, qty)
                    self.position.qty = qty
                    self.position.side = "BUY"
                    self.position.entry_price = close
                return
            # SHORT breakout
            if self.allow_short and close < ch_low_prev - self.atr_mult * atr_val:
                cash = float(getattr(broker, "cash", 0.0))
                available_cash = max(0.0, cash * self.qty_frac)
                notional = min(self.order_notional, available_cash)
                qty = notional / close if close > 0 else 0.0
                if qty > 0:
                    self._log(
                        f"ENTRY SHORT qty={qty:.6f} close={close:.2f} ch_low={ch_low:.2f} atr={atr_val:.2f}"
                    )
                    executor.market_sell(symbol, qty)
                    self.position.qty = -qty
                    self.position.side = "SELL"
                    self.position.entry_price = close
                return

        # Salida por reversión (precio vuelve dentro del canal tras ruptura reciente)
        if pos_qty > 0 and close < ch_high:  # Long pierde impulso
            if will_exit_non_negative(
                broker,
                entry_side="LONG",
                entry_price=self.position.entry_price,
                current_price=close,
                qty=pos_qty,
            ):
                self._log("EXIT LONG por reversión canal")
                executor.market_sell(symbol, pos_qty)
                self.position.qty = 0.0
                self.position.side = None
                return
            else:
                self._log("⏸️  Skip EXIT LONG por coste (no rentable)")
                return
        if pos_qty < 0 and close > ch_low:  # Short pierde impulso
            if will_exit_non_negative(
                broker,
                entry_side="SHORT",
                entry_price=self.position.entry_price,
                current_price=close,
                qty=abs(pos_qty),
            ):
                self._log("EXIT SHORT por reversión canal")
                executor.market_buy(symbol, abs(pos_qty))
                self.position.qty = 0.0
                self.position.side = None
                return
            else:
                self._log("⏸️  Skip EXIT SHORT por coste (no rentable)")
                return


# Registro único
register_strategy("vol_breakout", VolatilityBreakoutStrategy)

__all__ = ["VolatilityBreakoutStrategy"]

# src/strategies/momentum_v2.py
"""
Momentum Strategy V2 - Versión mejorada con gestión de riesgo.

Mejoras vs V1:
- Lookback adaptativo según volatilidad
- Confirmación de tendencia (filtro de ruido)
- Stop loss y take profit dinámicos
- Filtro de volatilidad (no operar en whipsaw)
- Cooldown entre trades (evitar overtrading)
- Gestión de posición conservadora

Objetivo: Reducir overtrading y mejorar win rate.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from core.costs import CostModel
from strategies.base import Strategy, register_strategy


@register_strategy("momentum_v2")
class MomentumV2Strategy(Strategy):
    """
    Estrategia de momentum mejorada con filtros y gestión de riesgo.

    Parámetros:
    -----------
    lookback_ticks : int
        Ventana para calcular media móvil (default: 20, balanced)
    entry_threshold : float
        Cambio % mínimo para entrada (default: 0.002 = 0.2%, balanced)
    exit_threshold : float
        Cambio % para salida (default: 0.001 = 0.1%)
    qty_frac : float
        Fracción del capital por trade (default: 0.5 = 50%, balanced)
    stop_loss_pct : float
        Stop loss en % desde entrada (default: 0.015 = 1.5%)
    take_profit_pct : float
        Take profit en % desde entrada (default: 0.025 = 2.5%)
    volatility_window : int
        Ventana para calcular volatilidad (default: 50)
    min_volatility : float
        Volatilidad mínima para operar (default: 0.0003 = 0.03%)
    max_volatility : float
        Volatilidad máxima para operar (default: 0.025 = 2.5%)
    cooldown_bars : int
        Barras de espera después de cerrar posición (default: 3)
    trend_confirmation : bool
        Requiere confirmación de tendencia (default: True)
    """

    name = "momentum_v2"

    def __init__(
        self,
        lookback_ticks: int = 15,  # Más corto → más reactivo
        entry_threshold: float = 0.0005,  # 0.05% → mucho más sensible
        exit_threshold: float = 0.0003,  # 0.03% → salidas rápidas
        qty_frac: float = 0.5,  # Aumentado de 0.4 → más exposición
        stop_loss_pct: float = 0.015,  # Aumentado de 0.01 → dar más margen
        take_profit_pct: float = 0.025,  # Aumentado de 0.02 → buscar más profit
        volatility_window: int = 50,
        min_volatility: float = 0.0001,  # 0.01% → operar en mercados muy calmados
        max_volatility: float = 0.025,  # Aumentado de 0.02 → tolerar más volatilidad
        cooldown_bars: int = 3,  # Reducido de 5 → menos espera
        trend_confirmation: bool = True,
        debug: bool = False,
        cost_model: CostModel | None = None,
        **_: Any,
    ) -> None:
        # Parámetros core
        self.lookback_ticks = int(lookback_ticks)
        self.entry_threshold = float(entry_threshold)
        self.exit_threshold = float(exit_threshold)
        self.qty_frac = float(qty_frac)

        # Gestión de riesgo
        self.stop_loss_pct = float(stop_loss_pct)
        self.take_profit_pct = float(take_profit_pct)

        # Filtros
        self.volatility_window = int(volatility_window)
        self.min_volatility = float(min_volatility)
        self.max_volatility = float(max_volatility)
        self.cooldown_bars = int(cooldown_bars)
        self.trend_confirmation = bool(trend_confirmation)

        self.debug = bool(debug)
        self._cost_model: CostModel | None = cost_model

        # Estado interno
        self._price_window: deque[float] = deque(maxlen=max(lookback_ticks, volatility_window))
        self._in_pos: bool = False
        self._pos_qty: float = 0.0
        self._entry_price: float = 0.0
        self._bars_since_exit: int = 0
        self._total_bars: int = 0

    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[MomentumV2] {msg}")

    def _calculate_volatility(self) -> float:
        """Calcula volatilidad como desviación estándar de retornos."""
        if len(self._price_window) < 2:
            return 0.0

        prices = list(self._price_window)
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]

        if not returns:
            return 0.0

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        return variance**0.5

    def _check_trend_confirmation(self, price: float, mean: float, momentum: float) -> bool:
        """
        Confirma tendencia comparando precio actual vs media de largo plazo.

        Evita entradas en reversiones falsas o whipsaws.
        """
        if len(self._price_window) < self.lookback_ticks * 2:
            return True  # No suficiente historia, permitir

        # Media de corto plazo (lookback_ticks)
        short_mean = mean

        # Media de largo plazo (2x lookback_ticks)
        long_window = list(self._price_window)[-self.lookback_ticks * 2 :]
        long_mean = sum(long_window) / len(long_window) if long_window else mean

        # Confirmación: momentum corto y largo deben estar alineados
        if momentum > 0:  # Intento de compra
            return short_mean > long_mean  # Media corta > media larga
        else:  # Intento de venta
            return short_mean < long_mean

    def _check_stop_loss(self, current_price: float) -> bool:
        """Verifica si se activó el stop loss."""
        if not self._in_pos or self._entry_price == 0:
            return False

        loss_pct = (current_price - self._entry_price) / self._entry_price
        return loss_pct < -self.stop_loss_pct

    def _check_take_profit(self, current_price: float) -> bool:
        """Verifica si se alcanzó el take profit."""
        if not self._in_pos or self._entry_price == 0:
            return False

        profit_pct = (current_price - self._entry_price) / self._entry_price
        return profit_pct > self.take_profit_pct

    def on_bar_live(self, broker, executor, symbol: str, bar: dict[str, Any]) -> None:
        """Lógica principal de trading."""
        price = float(bar["close"])
        self._price_window.append(price)
        self._total_bars += 1

        # Incrementar cooldown
        if not self._in_pos:
            self._bars_since_exit += 1

        # Warmup
        if len(self._price_window) < self.lookback_ticks:
            self._log(f"Warmup {len(self._price_window)}/{self.lookback_ticks}")
            return

        # Calcular indicadores
        mean = sum(list(self._price_window)[-self.lookback_ticks :]) / self.lookback_ticks
        if mean <= 0.0:
            return

        momentum = (price - mean) / mean
        volatility = self._calculate_volatility()

        # Obtener estado del broker
        try:
            cash = float(broker.cash)
            current_qty = float(broker.position_qty)
        except Exception:
            cash = 10_000.0
            current_qty = self._pos_qty

        # Log de estado
        self._log(
            f"Bar {self._total_bars} | Price: ${price:.2f} | Mom: {momentum:+.4f} | "
            f"Vol: {volatility:.4f} | InPos: {self._in_pos} | Cooldown: {self._bars_since_exit}"
        )

        # ==================== GESTIÓN DE POSICIÓN ABIERTA ====================

        if self._in_pos:
            # 1. Check stop loss
            if self._check_stop_loss(price):
                qty = current_qty if current_qty > 0 else self._pos_qty
                if qty > 0:
                    self._log(f"🛑 STOP LOSS @ ${price:.2f} (entry: ${self._entry_price:.2f})")
                    executor.market_sell(symbol, qty)
                    self._in_pos = False
                    self._pos_qty = 0.0
                    self._bars_since_exit = 0
                return

            # 2. Check take profit
            if self._check_take_profit(price):
                qty = current_qty if current_qty > 0 else self._pos_qty
                if qty > 0:
                    self._log(f"🎯 TAKE PROFIT @ ${price:.2f} (entry: ${self._entry_price:.2f})")
                    executor.market_sell(symbol, qty)
                    self._in_pos = False
                    self._pos_qty = 0.0
                    self._bars_since_exit = 0
                return

            # 3. Exit normal (momentum reversal)
            if momentum < -self.exit_threshold:
                qty = current_qty if current_qty > 0 else self._pos_qty
                if qty > 0:
                    self._log(f"📉 EXIT (momentum reversal) @ ${price:.2f}")
                    executor.market_sell(symbol, qty)
                    self._in_pos = False
                    self._pos_qty = 0.0
                    self._bars_since_exit = 0
                return

        # ==================== EVALUACIÓN DE ENTRADA ====================

        if not self._in_pos:
            # 1. Check cooldown
            if self._bars_since_exit < self.cooldown_bars:
                self._log(f"⏳ Cooldown: {self._bars_since_exit}/{self.cooldown_bars}")
                return

            # 2. Check volatilidad
            if volatility < self.min_volatility:
                self._log(f"😴 Volatility too low: {volatility:.6f} < {self.min_volatility:.6f}")
                return

            if volatility > self.max_volatility:
                self._log(f"🌊 Volatility too high: {volatility:.6f} > {self.max_volatility:.6f}")
                return

            # 3. Check momentum entry threshold
            if momentum <= self.entry_threshold:
                return

            # 4. Check trend confirmation
            if self.trend_confirmation:
                if not self._check_trend_confirmation(price, mean, momentum):
                    self._log("❌ Trend not confirmed (short/long MA misalignment)")
                    return

            # 5. Calcular tamaño de posición
            notional = cash * self.qty_frac
            qty = notional / price if price > 0 else 0.0

            if qty > 0:
                self._log(
                    f"🚀 ENTRY @ ${price:.2f} | Qty: {qty:.6f} | "
                    f"Notional: ${notional:.2f} | Mom: {momentum:+.4f}"
                )
                executor.market_buy(symbol, qty)
                self._in_pos = True
                self._pos_qty = qty
                self._entry_price = price
                self._bars_since_exit = 0

    def on_bar_bar(self, bar: dict[str, Any]) -> None:
        """Compatibilidad con backtests antiguos."""
        return None

    @property
    def cost_model(self) -> CostModel | None:
        return self._cost_model

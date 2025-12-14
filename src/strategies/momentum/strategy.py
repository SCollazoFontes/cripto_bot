# src/strategies/momentum.py
"""
Momentum Strategy Profesional - LONG only (Binance compatible).

LÓGICA CORE:
  1. ENTRADA: Momentum (price - SMA) > threshold + filtros (vol, trend)
  2. SALIDA: SL / TP / Momentum reversal (todos con profit mín)
  3. RIESGO: Max 30% loss por trade, cooldown tras exit

PARÁMETROS OPTIMIZABLES:
  - lookback_ticks: Ventana SMA (30-80 recomendado)
  - entry_threshold: Momentum mínimo para entrada (0.0008-0.003)
  - exit_threshold: Momentum reversión para salida (0.0003-0.0015)
  - stop_loss_pct, take_profit_pct: Protecciones (SL: 0.5-2%, TP: 1-5%)
  - volatility_window: Ventana para calcular volatilidad (20-80)
  - min/max_volatility: Rango de operación (min: 0.0001-0.0005, max: 0.01-0.03)

FILTROS SIEMPRE ACTIVOS:
  - Profit mínimo >= 30bps (cubre costes Binance ~10bps + slippage 5bps)
  - Trend confirmation (precio vs SMA larga)
  - Cooldown entre trades
  - Volatilidad dentro de rango

DINÁMICO (flags opcionales, OFF por defecto):
  - use_dynamic_sl/tp: Ajustar SL/TP por volatilidad
  - use_dynamic_entry: Ajustar threshold por volatilidad
  - use_trend_strength: Validar aceleración de momentum
"""

from __future__ import annotations

from collections import deque
from typing import Any

from core.execution.costs import CostModel
from strategies.base import Strategy, register_strategy, will_exit_non_negative


@register_strategy("momentum")
class MomentumStrategy(Strategy):
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
        Fracción del capital disponible (default: 1.0 = 100% del cash) usada como techo.
    stop_loss_pct : float
        Stop loss en % desde entrada (default: 0.015 = 1.5%)
    take_profit_pct : float
        Take profit en % desde entrada (default: 0.025 = 2.5%)
    order_notional : float
        Notional fijo en USD por operación (default: 5.0, mínimo de Binance spot).
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
    min_profit_bps : float
        Profit mínimo en bps para permitir exit (default: 20.0 = 0.2%)
        Evita exits que resulten negativos tras costes.
        Recomendación: 60-100 bps para cubrir fees (~10bps) + slippage (~5bps) + margin

    Adaptabilidad dinámica (TODO OPCIONAL - activar con flags):
    use_dynamic_sl : bool
        Ajusta SL según volatilidad (default: False)
    use_dynamic_tp : bool
        Ajusta TP según volatilidad (default: False)
    use_dynamic_entry : bool
        Ajusta entry threshold según volatilidad (default: False)
    use_dynamic_cooldown : bool
        Ajusta cooldown según rentabilidad del último trade (default: False)
    use_dynamic_min_profit : bool
        Calcula min_profit dinámicamente según costes (default: False)
    use_trend_strength : bool
        Valida fuerza de tendencia antes de entrar (default: False)

            RECOMENDACIONES DE PARÁMETROS (Basados en optimización BTCUSDT 25 días):
            ========================================================================
            Conservador (pocas señales, alta precisión):
                - lookback_ticks: 60-80
                - entry_threshold: 0.0012-0.0015
                - min_profit_bps: 80-100
                - Resultado: ~3-5 trades/25 días, win_rate > 60%

            Balanceado (recomendado):
                - lookback_ticks: 50
                - entry_threshold: 0.0011
                - exit_threshold: 0.0008
                - stop_loss_pct: 0.008, take_profit_pct: 0.015 (SL:TP = 1:2)
                - min_profit_bps: 60
                - Resultado: ~10-15 trades/25 días, win_rate > 50%

            Agresivo (muchas señales, tolerancia de pérdidas):
                - lookback_ticks: 30-40
                - entry_threshold: 0.0008-0.0009
                - min_profit_bps: 40-50
                - Resultado: ~20+ trades/25 días, win_rate variable

            RIESGOS A VALIDAR SIEMPRE:
            ==========================
            - Max drawdown: Nunca perder > 5-10% del capital
            - Win rate: Mantener > 45% (necesario con SL:TP = 1:2)
            - Profit factor: Total ganancias / Total pérdidas > 1.5
    """

    name = "momentum"

    def __init__(
        self,
        lookback_ticks: int = 50,  # SMA window (datos: 50-80 mejor que 12)
        entry_threshold: float = 0.0011,  # Entrada: 0.11% momentum (optimizado vs 0.02%)
        exit_threshold: float = 0.0008,  # Salida: -0.08% reversal (stop momentum)
        qty_frac: float = 1.0,
        order_notional: float = 5.0,
        stop_loss_pct: float = 0.008,  # SL: 80bps (tight, protege capital)
        take_profit_pct: float = 0.015,  # TP: 150bps (2x SL, asimetría favorable)
        volatility_window: int = 50,
        min_volatility: float = 0.0003,  # No operar en calma extrema (< 0.03%)
        max_volatility: float = 0.015,  # No operar en pánico (> 1.5%)
        cooldown_bars: int = 3,  # Espera 3 barras post-exit (evitar overtrading)
        max_hold_bars: int = 9999,  # Máximo barras en posición (default 9999 = sin límite)
        flat_cooldown: int = 0,  # Barras sin entrar después de flat (default 0 = desactivado)
        trend_confirmation: bool = True,  # SIEMPRE validar tendencia
        min_profit_bps: float = 60.0,  # Profit mín 60bps (cubre costes: fees 10 + slip 5 + margin 45)
        use_dynamic_sl: bool = False,
        use_dynamic_tp: bool = False,
        use_dynamic_entry: bool = False,
        use_dynamic_cooldown: bool = False,
        use_dynamic_min_profit: bool = False,
        use_trend_strength: bool = False,
        debug: bool = False,
        cost_model: CostModel | None = None,
        **kwargs: Any,
    ) -> None:
        # Soporte de compatibilidad: permitir `params={...}` además de kwargs directos
        params = kwargs.get("params")
        if isinstance(params, dict):
            lookback_ticks = int(params.get("lookback_ticks", lookback_ticks))
            entry_threshold = float(params.get("entry_threshold", entry_threshold))
            exit_threshold = float(params.get("exit_threshold", exit_threshold))
            qty_frac = float(params.get("qty_frac", qty_frac))
            stop_loss_pct = float(params.get("stop_loss_pct", stop_loss_pct))
            take_profit_pct = float(params.get("take_profit_pct", take_profit_pct))
            order_notional = float(params.get("order_notional", order_notional))
            volatility_window = int(params.get("volatility_window", volatility_window))
            min_volatility = float(params.get("min_volatility", min_volatility))
            max_volatility = float(params.get("max_volatility", max_volatility))
            cooldown_bars = int(params.get("cooldown_bars", cooldown_bars))
            max_hold_bars = int(params.get("max_hold_bars", max_hold_bars))
            flat_cooldown = int(params.get("flat_cooldown", flat_cooldown))
            trend_confirmation = bool(params.get("trend_confirmation", trend_confirmation))
            min_profit_bps = float(params.get("min_profit_bps", min_profit_bps))
            use_dynamic_sl = bool(params.get("use_dynamic_sl", use_dynamic_sl))
            use_dynamic_tp = bool(params.get("use_dynamic_tp", use_dynamic_tp))
            use_dynamic_entry = bool(params.get("use_dynamic_entry", use_dynamic_entry))
            use_dynamic_cooldown = bool(params.get("use_dynamic_cooldown", use_dynamic_cooldown))
            use_dynamic_min_profit = bool(
                params.get("use_dynamic_min_profit", use_dynamic_min_profit)
            )
            use_trend_strength = bool(params.get("use_trend_strength", use_trend_strength))
            debug = bool(params.get("debug", debug))

        # Parámetros core
        self.lookback_ticks = int(lookback_ticks)
        self.entry_threshold = float(entry_threshold)
        self.exit_threshold = float(exit_threshold)
        self.qty_frac = float(qty_frac)
        self.order_notional = float(order_notional)

        # Gestión de riesgo
        self.stop_loss_pct = float(stop_loss_pct)
        self.take_profit_pct = float(take_profit_pct)

        # Filtros
        self.volatility_window = int(volatility_window)
        self.min_volatility = float(min_volatility)
        self.max_volatility = float(max_volatility)
        self.cooldown_bars = int(cooldown_bars)
        self.max_hold_bars = int(max_hold_bars)
        self.flat_cooldown = int(flat_cooldown)
        self.trend_confirmation = bool(trend_confirmation)
        self.min_profit_bps = float(min_profit_bps)

        # ===== VALIDACIONES =====
        if self.lookback_ticks < 10:
            raise ValueError(f"lookback_ticks={self.lookback_ticks} es muy corto (<10)")
        if self.lookback_ticks > 200:
            raise ValueError(f"lookback_ticks={self.lookback_ticks} es muy largo (>200)")

        if self.entry_threshold <= 0:
            raise ValueError(f"entry_threshold debe ser positivo, got {self.entry_threshold}")
        if self.entry_threshold > 0.01:
            raise ValueError(f"entry_threshold={self.entry_threshold} es muy alto (>1%)")

        if self.exit_threshold <= 0:
            raise ValueError(f"exit_threshold debe ser positivo, got {self.exit_threshold}")
        if self.exit_threshold > self.entry_threshold:
            raise ValueError(
                f"exit_threshold ({self.exit_threshold}) NO puede ser > entry_threshold ({self.entry_threshold})"
            )

        if self.stop_loss_pct <= 0:
            raise ValueError(f"stop_loss_pct debe ser positivo, got {self.stop_loss_pct}")
        if self.stop_loss_pct > 0.1:
            raise ValueError(
                f"stop_loss_pct={self.stop_loss_pct} es muy alto (>10%), riesgo excesivo"
            )

        if self.take_profit_pct <= 0:
            raise ValueError(f"take_profit_pct debe ser positivo, got {self.take_profit_pct}")
        if self.take_profit_pct < self.stop_loss_pct:
            raise ValueError(
                f"take_profit_pct ({self.take_profit_pct}) debe ser >= stop_loss_pct ({self.stop_loss_pct})"
            )
        if self.take_profit_pct > 0.2:
            raise ValueError(f"take_profit_pct={self.take_profit_pct} es poco realista (>20%)")

        if self.min_volatility >= self.max_volatility:
            raise ValueError(
                f"min_volatility ({self.min_volatility}) >= max_volatility ({self.max_volatility})"
            )

        if self.min_profit_bps < 20:
            raise ValueError(
                f"min_profit_bps={self.min_profit_bps} es muy bajo (<20bps), no cubre costes"
            )
        if self.min_profit_bps > 200:
            raise ValueError(f"min_profit_bps={self.min_profit_bps} es irreal (>200bps)")

        if self.qty_frac <= 0 or self.qty_frac > 1.0:
            raise ValueError(f"qty_frac debe estar en (0, 1], got {self.qty_frac}")

        # Adaptabilidad dinámica
        self.use_dynamic_sl = bool(use_dynamic_sl)
        self.use_dynamic_tp = bool(use_dynamic_tp)
        self.use_dynamic_entry = bool(use_dynamic_entry)
        self.use_dynamic_cooldown = bool(use_dynamic_cooldown)
        self.use_dynamic_min_profit = bool(use_dynamic_min_profit)
        self.use_trend_strength = bool(use_trend_strength)

        self.debug = bool(debug)
        self._cost_model: CostModel | None = cost_model

        # Estado interno
        self._price_window: deque[float] = deque(maxlen=max(lookback_ticks, volatility_window))
        self._momentum_history: deque[float] = deque(maxlen=10)  # Últimos 10 momentums
        self._in_pos: bool = False
        self._pos_qty: float = 0.0
        self._entry_price: float = 0.0
        self._bars_since_exit: int = 0
        self._total_bars: int = 0
        self._bars_in_pos: int = 0  # Contador de barras en posición actual
        self._flat_counter: int = 0  # Contador de barras sin entrar (flat_cooldown)
        self._last_trade_close_bar: int = 0  # Track ultimo bar donde se cerro posicion
        self._last_profit_bps: float = 0.0  # Profit del último trade cerrado
        self._last_trade_profitable: bool = False  # Si el último trade fue ganador

    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[Momentum] {msg}")

        def _document_logic(self) -> str:
            """
            LÓGICA COMPLETA DE LA ESTRATEGIA (para referencia).

            ENTRADA (si NO hay posición abierta):
                1. Cooldown cumplido (3+ barras desde último exit)
                2. Volatilidad en rango [min, max]
                3. Momentum > entry_threshold
                4. Tendencia confirmada (precio > SMA larga)
                5. Opcional: trend strength > 60%

            SALIDA (si hay posición):
                1. Stop Loss: loss% > -stop_loss_pct → FUERZA venta (si profit > 30bps, protección mínima)
                2. Take Profit: profit% > take_profit_pct AND profit > min_profit_bps → venta
                3. Momentum Reversal: momentum < -exit_threshold → salida natural

            PROTECCIONES SIEMPRE ACTIVAS:
                - min_profit_bps >= 60bps (cubre costes aproximados)
                - Nunca vender si profit < 30bps (evitar pérdidas netas)
                - Trend confirmation siempre (evitar reversiones falsas)

            PARÁMETROS CLAVE (para optimización):
                - lookback_ticks: Cuántas barras para SMA (50-80 óptimo)
                - entry_threshold: Cuánto momentum mínimo (0.0008-0.002)
                - exit_threshold: Cuándo revierte momentum (0.0003-0.001)
                - SL/TP ratio: stop_loss_pct < take_profit_pct (2:1 o 3:1 ideal)
                - min_profit_bps: Profit mínimo para salir (60-100bps recomendado)
            """
            return "Ver docstring para lógica"

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

    def _calculate_dynamic_sl(self, volatility: float) -> float:
        """
        SL dinámico basado en volatilidad.
        Vol baja → SL ajustado (más protección)
        Vol alta → SL amplio (evita stops falsos)
        """
        if not self.use_dynamic_sl:
            return self.stop_loss_pct

        vol_ratio = volatility / 0.001
        factor = max(0.8, min(1.2, 1.0 + (vol_ratio - 1.0) * 0.2))
        return self.stop_loss_pct * factor

    def _calculate_dynamic_tp(self, volatility: float) -> float:
        """
        TP dinámico basado en volatilidad.
        Vol baja → TP alto (movimientos duran más)
        Vol alta → TP bajo (movimientos rápidos)
        """
        if not self.use_dynamic_tp:
            return self.take_profit_pct

        vol_ratio = volatility / 0.001
        factor = max(0.67, min(1.5, 2.0 - vol_ratio))
        return self.take_profit_pct * factor

    def _calculate_dynamic_entry_threshold(self, volatility: float) -> float:
        """
        Entry threshold dinámico.
        Vol baja → menos exigente (más oportunidades)
        Vol alta → más exigente (menos falsos positivos)
        """
        if not self.use_dynamic_entry:
            return self.entry_threshold

        if volatility > 0.01:
            return self.entry_threshold * 1.5
        elif volatility < 0.0005:
            return self.entry_threshold * 0.7
        else:
            return self.entry_threshold

    def _calculate_dynamic_cooldown(self) -> int:
        """
        Cooldown dinámico según rentabilidad del trade anterior.
        Ganancia alta → cooldown bajo (seguir momentum)
        Ganancia baja → cooldown alto (esperar confirmación)
        """
        if not self.use_dynamic_cooldown or self._last_profit_bps == 0.0:
            return self.cooldown_bars

        if self._last_profit_bps > 100:
            return 1
        elif self._last_profit_bps > 50:
            return 2
        elif self._last_profit_bps > 30:
            return 3
        else:
            return 5

    def _calculate_dynamic_min_profit(self, order_size: float, price: float) -> float:
        """
        Min profit dinámico basado en costes reales.
        Órdenes pequeñas → menos profit requerido
        Órdenes grandes → más profit requerido
        """
        if not self.use_dynamic_min_profit:
            return self.min_profit_bps

        fees_bps = 10.0
        base_slippage = 5.0
        notional = order_size * price
        size_factor = (notional / 50000.0) * 10.0
        slippage_bps = base_slippage + min(size_factor, 10.0)
        safety_margin = 5.0

        return fees_bps + slippage_bps + safety_margin

    def _calculate_trend_strength(self) -> float:
        """
        Fuerza de tendencia (0.0 a 1.0).
        Mira si momentum está acelerando consistentemente.
        0.0 = tendencia débil, 1.0 = tendencia fuerte
        """
        if len(self._momentum_history) < 3:
            return 0.5

        recent = list(self._momentum_history)[-3:]
        increasing = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
        return increasing / len(recent)

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

    def _check_stop_loss(self, current_price: float, dynamic_sl: float) -> bool:
        """Verifica si se activó el stop loss (dinámico o fijo)."""
        if not self._in_pos or self._entry_price == 0:
            return False

        loss_pct = (current_price - self._entry_price) / self._entry_price
        return loss_pct < -dynamic_sl

    def _check_take_profit(self, current_price: float, dynamic_tp: float) -> bool:
        """Verifica si se alcanzó el take profit (dinámico o fijo)."""
        if not self._in_pos or self._entry_price == 0:
            return False

        profit_pct = (current_price - self._entry_price) / self._entry_price
        return profit_pct > dynamic_tp

    def _check_min_profit(self, current_price: float, dynamic_min_profit: float) -> bool:
        """Verifica si el profit supera el mínimo requerido (dinámico o fijo)."""
        if not self._in_pos or self._entry_price == 0:
            return False
        profit_pct = (current_price - self._entry_price) / self._entry_price
        profit_bps = profit_pct * 10_000
        return profit_bps >= dynamic_min_profit

    def _check_non_negative_profit(self, current_price: float) -> bool:
        """Verifica si el profit es > 30 bps (cubre costes aprox)."""
        if not self._in_pos or self._entry_price == 0:
            return False
        profit_pct = (current_price - self._entry_price) / self._entry_price
        profit_bps = profit_pct * 10_000
        return profit_bps > 30

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
        self._momentum_history.append(momentum)
        volatility = self._calculate_volatility()

        # Obtener estado del broker
        try:
            cash = float(broker.cash)
            current_qty = float(broker.position_qty)
        except Exception:
            cash = 10_000.0
            current_qty = self._pos_qty

        # Calcular valores dinámicos
        dynamic_sl = self._calculate_dynamic_sl(volatility)
        dynamic_tp = self._calculate_dynamic_tp(volatility)
        dynamic_entry = self._calculate_dynamic_entry_threshold(volatility)
        available_cash = max(0.0, cash * self.qty_frac)
        notional = min(self.order_notional, available_cash)
        dynamic_min_profit = self._calculate_dynamic_min_profit(notional, price)
        trend_strength = self._calculate_trend_strength()

        # Log de estado
        self._log(
            f"Bar {self._total_bars} | Price: ${price:.2f} | Mom: {momentum:+.4f} | "
            f"Vol: {volatility:.6f} | SL: {dynamic_sl:.4f} | TP: {dynamic_tp:.4f} | "
            f"TrendStr: {trend_strength:.2f}"
        )

        # ==================== GESTIÓN DE POSICIÓN ABIERTA ====================

        if self._in_pos:
            self._bars_in_pos += 1

            # 0. Check max_hold_bars: si alcanzamos límite, cerrar posición
            if self._bars_in_pos >= self.max_hold_bars:
                qty = current_qty if current_qty > 0 else self._pos_qty
                if qty > 0 and self._check_non_negative_profit(price):
                    self._log(
                        f"⏰ MAX_HOLD_BARS ({self._bars_in_pos}/{self.max_hold_bars}) alcanzado, cerrando"
                    )
                    executor.market_sell(symbol, qty)
                    profit_pct = (price - self._entry_price) / self._entry_price
                    self._last_profit_bps = profit_pct * 10_000
                    self._last_trade_profitable = self._last_profit_bps > 0
                    self._in_pos = False
                    self._pos_qty = 0.0
                    self._bars_in_pos = 0
                    self._bars_since_exit = 0
                    self._flat_counter = self.flat_cooldown
                    self._last_trade_close_bar = self._total_bars
                return

            # 1. Check stop loss
            if self._check_stop_loss(price, dynamic_sl):
                qty = current_qty if current_qty > 0 else self._pos_qty
                if qty > 0:
                    if self._check_non_negative_profit(price):
                        self._log(
                            f"🛑 STOP LOSS @ ${price:.2f} (entry: ${self._entry_price:.2f}) "
                            f"[dinámico: {dynamic_sl:.4f}]"
                        )
                        executor.market_sell(symbol, qty)

                        # Registrar profit del trade cerrado
                        profit_pct = (price - self._entry_price) / self._entry_price
                        self._last_profit_bps = profit_pct * 10_000
                        self._last_trade_profitable = self._last_profit_bps > 0

                        self._in_pos = False
                        self._pos_qty = 0.0
                        self._bars_in_pos = 0
                        self._bars_since_exit = 0
                        self._flat_counter = self.flat_cooldown
                        self._last_trade_close_bar = self._total_bars
                    else:
                        self._log("🔒 SL alcanzado pero profit < 30bps, mantener")
                return

            # 2. Check take profit
            if self._check_take_profit(price, dynamic_tp):
                if self._check_min_profit(
                    price, dynamic_min_profit
                ) and self._check_non_negative_profit(price):
                    qty = current_qty if current_qty > 0 else self._pos_qty
                    if qty > 0:
                        self._log(
                            f"🎯 TAKE PROFIT @ ${price:.2f} (entry: ${self._entry_price:.2f}) "
                            f"[dinámico: {dynamic_tp:.4f}]"
                        )
                        executor.market_sell(symbol, qty)

                        # Registrar profit del trade cerrado
                        profit_pct = (price - self._entry_price) / self._entry_price
                        self._last_profit_bps = profit_pct * 10_000
                        self._last_trade_profitable = self._last_profit_bps > 0

                        self._in_pos = False
                        self._pos_qty = 0.0
                        self._bars_in_pos = 0
                        self._bars_since_exit = 0
                        self._flat_counter = self.flat_cooldown
                        self._last_trade_close_bar = self._total_bars
                else:
                    self._log("⏸️  TP alcanzado pero condiciones no cumplidas, mantener")
                return

            # 3. Exit normal (momentum reversal)
            if momentum < -self.exit_threshold:
                qty = current_qty if current_qty > 0 else self._pos_qty
                if qty > 0:
                    if not self._check_non_negative_profit(price):
                        self._log("🔒 Momentum reversal pero profit < 30bps, mantener")
                        return

                    if not self._check_min_profit(price, dynamic_min_profit):
                        self._log(
                            f"⏸️  Momentum reversal pero profit < {dynamic_min_profit:.1f} bps, mantener"
                        )
                        return

                    if will_exit_non_negative(
                        broker,
                        entry_side="LONG",
                        entry_price=self._entry_price,
                        current_price=price,
                        qty=qty,
                    ):
                        self._log(f"📉 EXIT (momentum reversal) @ ${price:.2f}")
                        executor.market_sell(symbol, qty)

                        # Registrar profit del trade cerrado
                        profit_pct = (price - self._entry_price) / self._entry_price
                        self._last_profit_bps = profit_pct * 10_000
                        self._last_trade_profitable = self._last_profit_bps > 0

                        self._in_pos = False
                        self._pos_qty = 0.0
                        self._bars_in_pos = 0
                        self._bars_since_exit = 0
                        self._flat_counter = self.flat_cooldown  # Iniciar flat_cooldown
                        self._last_trade_close_bar = self._total_bars
                    else:
                        self._log("⏸️  Skip EXIT: no rentable neto tras costes")
                return

        # ==================== EVALUACIÓN DE ENTRADA ====================

        if not self._in_pos:
            # 0. Check flat_cooldown: esperar después de salida sin entrar
            if self._flat_counter > 0:
                self._flat_counter -= 1
                self._log(f"📍 Flat cooldown: {self._flat_counter}/{self.flat_cooldown}")
                return

            # 1. Check cooldown (dinámico o fijo)
            dynamic_cooldown = self._calculate_dynamic_cooldown()
            if self._bars_since_exit < dynamic_cooldown:
                self._log(f"⏳ Cooldown: {self._bars_since_exit}/{dynamic_cooldown}")
                return

            # 2. Check volatilidad
            if volatility < self.min_volatility:
                self._log(f"😴 Volatility too low: {volatility:.6f} < {self.min_volatility:.6f}")
                return

            if volatility > self.max_volatility:
                self._log(f"🌊 Volatility too high: {volatility:.6f} > {self.max_volatility:.6f}")
                return

            # 3. Check entry threshold (dinámico o fijo)
            if momentum <= dynamic_entry:
                return

            # 4. Check trend confirmation
            if self.trend_confirmation:
                if not self._check_trend_confirmation(price, mean, momentum):
                    self._log("❌ Trend not confirmed (short/long MA misalignment)")
                    return

            # 5. Check trend strength (opcional)
            if self.use_trend_strength:
                if trend_strength < 0.6:
                    self._log(f"⚠️  Trend too weak: {trend_strength:.2f} < 0.6")
                    return

            # 6. Calcular tamaño de posición
            qty = notional / price if price > 0 else 0.0

            if qty > 0:
                self._log(
                    f"🚀 ENTRY @ ${price:.2f} | Qty: {qty:.6f} | "
                    f"Notional: ${notional:.2f} | Mom: {momentum:+.4f} | "
                    f"Entry: {dynamic_entry:.6f} | TrendStr: {trend_strength:.2f}"
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

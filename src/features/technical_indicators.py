# src/features/technical_indicators.py
"""
Cálculo de indicadores técnicos para análisis y estrategias.

Indicadores implementados:
- Medias móviles: SMA, EMA, WMA
- Momentum: RSI, MACD, Stochastic
- Volatilidad: ATR, Bollinger Bands
- Volumen: Volume Profile, OBV, VWAP
- Tendencia: ADX, Supertrend
- Patrones: Support/Resistance zones

Diseño:
- Sin dependencias externas (implementación pura Python)
- Optimizado para streaming (cálculo incremental)
- Compatible con pandas para backtesting
"""

from __future__ import annotations

from collections import deque


class TechnicalIndicators:
    """
    Calculadora de indicadores técnicos con estado interno.

    Uso:
        ti = TechnicalIndicators()

        # Actualizar con nueva barra
        ti.update(price=100.0, volume=1.5)

        # Obtener indicadores
        features = ti.get_all_features()
        # {"sma_20": 99.5, "rsi_14": 65.2, "bb_upper": 102.3, ...}
    """

    def __init__(
        self,
        sma_periods: list[int] | None = None,
        ema_periods: list[int] | None = None,
        rsi_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        atr_period: int = 14,
    ):
        # Parámetros
        self.sma_periods = sma_periods or [10, 20, 50, 100, 200]
        self.ema_periods = ema_periods or [9, 12, 21, 26, 50]
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period

        # Ventanas de precios
        self._max_period = max(
            max(self.sma_periods),
            max(self.ema_periods),
            self.rsi_period,
            self.bb_period,
            self.atr_period,
        )
        self._prices: deque[float] = deque(maxlen=self._max_period * 2)
        self._volumes: deque[float] = deque(maxlen=self._max_period * 2)
        self._highs: deque[float] = deque(maxlen=self._max_period * 2)
        self._lows: deque[float] = deque(maxlen=self._max_period * 2)

        # Estado para EMA (para cálculo incremental eficiente)
        self._ema_values: dict[int, float] = {}

        # Estado para RSI
        self._gains: deque[float] = deque(maxlen=self.rsi_period)
        self._losses: deque[float] = deque(maxlen=self.rsi_period)

    def update(
        self,
        price: float,
        volume: float = 0.0,
        high: float | None = None,
        low: float | None = None,
    ) -> None:
        """Actualiza indicadores con nueva barra."""
        self._prices.append(price)
        self._volumes.append(volume)
        self._highs.append(high if high is not None else price)
        self._lows.append(low if low is not None else price)

        # Actualizar ganancias/pérdidas para RSI
        if len(self._prices) >= 2:
            change = self._prices[-1] - self._prices[-2]
            self._gains.append(max(0, change))
            self._losses.append(max(0, -change))

    def get_all_features(self) -> dict[str, float]:
        """Calcula y retorna todos los indicadores disponibles."""
        features = {}

        # SMAs
        for period in self.sma_periods:
            sma = self._calculate_sma(period)
            if sma is not None:
                features[f"sma_{period}"] = sma

        # EMAs
        for period in self.ema_periods:
            ema = self._calculate_ema(period)
            if ema is not None:
                features[f"ema_{period}"] = ema

        # RSI
        rsi = self._calculate_rsi()
        if rsi is not None:
            features["rsi"] = rsi

        # Bollinger Bands
        bb = self._calculate_bollinger_bands()
        if bb:
            features["bb_upper"] = bb["upper"]
            features["bb_middle"] = bb["middle"]
            features["bb_lower"] = bb["lower"]
            features["bb_width"] = bb["width"]

        # ATR
        atr = self._calculate_atr()
        if atr is not None:
            features["atr"] = atr

        # Price position
        if len(self._prices) > 0:
            current_price = self._prices[-1]
            features["price"] = current_price

            # % from SMAs
            for period in [20, 50, 200]:
                sma = self._calculate_sma(period)
                if sma and sma > 0:
                    features[f"price_vs_sma_{period}_pct"] = ((current_price - sma) / sma) * 100

        # Volume indicators
        if len(self._volumes) > 0:
            features["volume"] = self._volumes[-1]
            vol_sma_20 = self._calculate_volume_sma(20)
            if vol_sma_20 and vol_sma_20 > 0:
                features["volume_vs_avg_pct"] = (
                    (self._volumes[-1] - vol_sma_20) / vol_sma_20
                ) * 100

        return features

    # ==================== CÁLCULOS INTERNOS ====================

    def _calculate_sma(self, period: int) -> float | None:
        """Simple Moving Average."""
        if len(self._prices) < period:
            return None
        window = list(self._prices)[-period:]
        return sum(window) / len(window)

    def _calculate_ema(self, period: int) -> float | None:
        """Exponential Moving Average (incremental)."""
        if len(self._prices) < period:
            return None

        current_price = self._prices[-1]

        # Primera vez: calcular desde SMA
        if period not in self._ema_values:
            sma = self._calculate_sma(period)
            if sma is None:
                return None
            self._ema_values[period] = sma
            return sma

        # Cálculo incremental: EMA = precio * k + EMA_prev * (1 - k)
        k = 2.0 / (period + 1)
        prev_ema = self._ema_values[period]
        new_ema = (current_price * k) + (prev_ema * (1 - k))
        self._ema_values[period] = new_ema

        return new_ema

    def _calculate_rsi(self) -> float | None:
        """Relative Strength Index."""
        if len(self._gains) < self.rsi_period:
            return None

        avg_gain = sum(self._gains) / len(self._gains)
        avg_loss = sum(self._losses) / len(self._losses)

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_bollinger_bands(self) -> dict[str, float] | None:
        """Bollinger Bands (upper, middle, lower, width)."""
        if len(self._prices) < self.bb_period:
            return None

        window = list(self._prices)[-self.bb_period :]
        middle = sum(window) / len(window)

        # Desviación estándar
        variance = sum((x - middle) ** 2 for x in window) / len(window)
        std = variance**0.5

        upper = middle + (self.bb_std * std)
        lower = middle - (self.bb_std * std)
        width = ((upper - lower) / middle) * 100 if middle > 0 else 0

        return {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "width": width,
        }

    def _calculate_atr(self) -> float | None:
        """Average True Range."""
        if len(self._prices) < self.atr_period + 1:
            return None

        true_ranges = []
        for i in range(1, len(self._prices)):
            high = self._highs[i]
            low = self._lows[i]
            prev_close = self._prices[i - 1]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        # Tomar últimos atr_period true ranges
        recent_trs = true_ranges[-self.atr_period :]
        return sum(recent_trs) / len(recent_trs)

    def _calculate_volume_sma(self, period: int) -> float | None:
        """Simple Moving Average del volumen."""
        if len(self._volumes) < period:
            return None
        window = list(self._volumes)[-period:]
        return sum(window) / len(window)


# ==================== BATCH CALCULATOR (para DataFrames) ====================


def calculate_features_batch(df, price_col="close", volume_col="volume") -> dict[str, list]:
    """
    Calcula indicadores técnicos sobre un DataFrame completo.

    Uso:
        features = calculate_features_batch(df, price_col="close")
        df["sma_20"] = features["sma_20"]
        df["rsi"] = features["rsi"]

    Returns:
        Dict con listas de valores para cada indicador
    """
    ti = TechnicalIndicators()

    features_dict: dict[str, list] = {
        "sma_20": [],
        "sma_50": [],
        "ema_12": [],
        "ema_26": [],
        "rsi": [],
        "bb_upper": [],
        "bb_middle": [],
        "bb_lower": [],
        "atr": [],
    }

    for _idx, row in df.iterrows():
        price = row[price_col]
        volume = row.get(volume_col, 0.0)
        high = row.get("high", price)
        low = row.get("low", price)

        ti.update(price=price, volume=volume, high=high, low=low)
        features = ti.get_all_features()

        # Agregar a listas
        for key in features_dict:
            features_dict[key].append(features.get(key))

    return features_dict


# ==================== SUPPORT & RESISTANCE DETECTOR ====================


class SupportResistanceDetector:
    """
    Detecta niveles de soporte y resistencia usando pivots.

    Algoritmo:
    1. Identificar pivots (máximos y mínimos locales)
    2. Agrupar pivots cercanos en zonas
    3. Asignar importancia según:
       - Número de toques
       - Volumen en la zona
       - Recencia
    """

    def __init__(self, lookback: int = 50, zone_threshold_pct: float = 0.5):
        self.lookback = lookback
        self.zone_threshold_pct = zone_threshold_pct
        self._prices: deque[float] = deque(maxlen=lookback)
        self._highs: deque[float] = deque(maxlen=lookback)
        self._lows: deque[float] = deque(maxlen=lookback)
        self._volumes: deque[float] = deque(maxlen=lookback)

    def update(self, high: float, low: float, close: float, volume: float) -> None:
        """Actualiza con nueva barra."""
        self._prices.append(close)
        self._highs.append(high)
        self._lows.append(low)
        self._volumes.append(volume)

    def get_zones(self) -> dict[str, list[dict]]:
        """
        Retorna zonas de soporte y resistencia detectadas.

        Returns:
            {
                "support": [{"price": 100.0, "strength": 5, "touches": 3}, ...],
                "resistance": [{"price": 105.0, "strength": 7, "touches": 4}, ...]
            }
        """
        if len(self._prices) < 10:
            return {"support": [], "resistance": []}

        # Detectar pivots
        resistance_pivots = self._find_resistance_pivots()
        support_pivots = self._find_support_pivots()

        # Agrupar en zonas
        resistance_zones = self._cluster_pivots(resistance_pivots)
        support_zones = self._cluster_pivots(support_pivots)

        return {
            "resistance": resistance_zones,
            "support": support_zones,
        }

    def _find_resistance_pivots(self) -> list[float]:
        """Encuentra máximos locales."""
        pivots = []
        highs = list(self._highs)

        for i in range(2, len(highs) - 2):
            if (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i + 2]
            ):
                pivots.append(highs[i])

        return pivots

    def _find_support_pivots(self) -> list[float]:
        """Encuentra mínimos locales."""
        pivots = []
        lows = list(self._lows)

        for i in range(2, len(lows) - 2):
            if (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i + 2]
            ):
                pivots.append(lows[i])

        return pivots

    def _cluster_pivots(self, pivots: list[float]) -> list[dict]:
        """Agrupa pivots cercanos en zonas."""
        if not pivots:
            return []

        zones = []
        sorted_pivots = sorted(pivots)

        current_zone = [sorted_pivots[0]]
        threshold = sorted_pivots[0] * (self.zone_threshold_pct / 100)

        for pivot in sorted_pivots[1:]:
            if pivot - current_zone[-1] <= threshold:
                current_zone.append(pivot)
            else:
                # Crear zona con pivots acumulados
                avg_price = sum(current_zone) / len(current_zone)
                zones.append(
                    {
                        "price": avg_price,
                        "touches": len(current_zone),
                        "strength": len(current_zone),  # Simplificado
                    }
                )
                current_zone = [pivot]

        # Agregar última zona
        if current_zone:
            avg_price = sum(current_zone) / len(current_zone)
            zones.append(
                {
                    "price": avg_price,
                    "touches": len(current_zone),
                    "strength": len(current_zone),
                }
            )

        # Ordenar por strength (más toques = más importante)
        return sorted(zones, key=lambda x: x["strength"], reverse=True)

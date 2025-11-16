"""
Calculadores de señales cuantitativas para todas las estrategias.

Cada estrategia debe tener un método `calculate_signal()` que devuelva
un valor entre -1.0 (venta fuerte) y +1.0 (compra fuerte).

Escala de señales:
    -1.0: Venta fuerte
    -0.5: Venta moderada
     0.0: Neutral
    +0.5: Compra moderada
    +1.0: Compra fuerte
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def calculate_momentum_signal(
    df: pd.DataFrame,
    lookback_ticks: int = 10,
    entry_threshold: float = 0.001,
    exit_threshold: float = 0.0005,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calcula señal de momentum simple.

    Returns:
        (signal_value, zone_text, metadata)
        signal_value: -1.0 a +1.0
        zone_text: "BUY", "NEUTRAL", "SELL"
        metadata: dict con detalles del cálculo
    """
    if df.empty or len(df) < lookback_ticks:
        return 0.0, "NEUTRAL", {"reason": "insuficientes datos"}

    current_price = df["close"].iloc[-1]
    recent_prices = df["close"].tail(lookback_ticks)
    mean_price = recent_prices.mean()

    if mean_price <= 0:
        return 0.0, "NEUTRAL", {"reason": "precio medio inválido"}

    # Momentum = (precio - media) / media
    momentum = (current_price - mean_price) / mean_price

    # Normalizar a escala [-1, +1]
    # Escalar linealmente usando entry_threshold como referencia
    max_signal = entry_threshold * 3

    if momentum >= max_signal:
        signal = 1.0
        zone = "BUY"
    elif momentum <= -max_signal:
        signal = -1.0
        zone = "SELL"
    else:
        # Escalar linealmente entre -1 y +1
        signal = momentum / max_signal

        if signal >= 0.5:
            zone = "BUY"
        elif signal <= -0.5:
            zone = "SELL"
        else:
            zone = "NEUTRAL"

    metadata = {
        "momentum": momentum,
        "mean_price": mean_price,
        "current_price": current_price,
        "lookback": lookback_ticks,
    }

    return signal, zone, metadata


def calculate_momentum_v2_signal(
    df: pd.DataFrame,
    lookback_ticks: int = 15,
    entry_threshold: float = 0.0005,
    exit_threshold: float = 0.0003,
    min_volatility: float = 0.0001,
    max_volatility: float = 0.025,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calcula señal de momentum v2 con filtros de volatilidad.

    Returns:
        (signal_value, zone_text, metadata)
    """
    if df.empty or len(df) < lookback_ticks * 2:
        return 0.0, "NEUTRAL", {"reason": "insuficientes datos"}

    current_price = df["close"].iloc[-1]
    recent_prices = df["close"].tail(lookback_ticks)
    mean_price = recent_prices.mean()

    if mean_price <= 0:
        return 0.0, "NEUTRAL", {"reason": "precio medio inválido"}

    # Calcular momentum
    momentum = (current_price - mean_price) / mean_price

    # Calcular volatilidad
    prices_array = df["close"].tail(50).values
    if len(prices_array) < 2:
        volatility = 0.0
    else:
        returns = np.diff(prices_array) / prices_array[:-1]
        volatility = np.std(returns) if len(returns) > 0 else 0.0

    # Filtro de volatilidad
    if volatility < min_volatility:
        return 0.0, "LOW VOL", {"volatility": volatility, "reason": "volatilidad muy baja"}
    if volatility > max_volatility:
        return 0.0, "HIGH VOL", {"volatility": volatility, "reason": "volatilidad muy alta"}

    # Confirmar tendencia (media corta vs media larga)
    if len(df) >= lookback_ticks * 2:
        long_mean = df["close"].tail(lookback_ticks * 2).mean()
        trend_confirmed = (mean_price > long_mean) if momentum > 0 else (mean_price < long_mean)
    else:
        trend_confirmed = True

    # Calcular señal base
    max_signal = entry_threshold * 4

    if momentum >= max_signal:
        base_signal = 1.0
        zone = "BUY"
    elif momentum <= -max_signal:
        base_signal = -1.0
        zone = "SELL"
    else:
        # Escalar linealmente
        base_signal = momentum / max_signal

        if base_signal >= 0.5:
            zone = "BUY"
        elif base_signal <= -0.5:
            zone = "SELL"
        else:
            zone = "NEUTRAL"

    # Atenuar señal si tendencia no confirmada
    if not trend_confirmed and abs(base_signal) >= 0.5:
        base_signal *= 0.5
        zone = "NEUTRAL"

    metadata = {
        "momentum": momentum,
        "volatility": volatility,
        "trend_confirmed": trend_confirmed,
        "mean_price": mean_price,
    }

    return base_signal, zone, metadata


def calculate_vwap_reversion_signal(
    df: pd.DataFrame,
    vwap_window: int = 50,
    z_entry: float = 1.5,
    z_exit: float = 0.5,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calcula señal de reversión a media VWAP.

    Returns:
        (signal_value, zone_text, metadata)
    """
    if df.empty or len(df) < vwap_window:
        return 0.0, "NEUTRAL", {"reason": "insuficientes datos"}

    # Calcular VWAP
    recent_df = df.tail(vwap_window).copy()
    recent_df["pv"] = recent_df["close"] * recent_df["volume"]
    vwap = recent_df["pv"].sum() / recent_df["volume"].sum()

    # Calcular z-score (desviación en número de std dev)
    prices = recent_df["close"].values
    price_mean = prices.mean()
    price_std = prices.std()

    current_price = df["close"].iloc[-1]

    if price_std <= 0 or vwap <= 0:
        return 0.0, "NEUTRAL", {"reason": "std o vwap inválidos"}

    z_score = (current_price - price_mean) / price_std

    # Señal de reversión a media:
    # z > +z_entry → precio MUY alto → VENDER (esperar reversión a baja)
    # z < -z_entry → precio MUY bajo → COMPRAR (esperar reversión al alza)
    # Escalar linealmente entre -1 y +1

    max_z = z_entry * 2

    if z_score >= max_z:
        signal = -1.0  # VENDER (precio muy alto)
        zone = "SELL"
    elif z_score <= -max_z:
        signal = 1.0  # COMPRAR (precio muy bajo)
        zone = "BUY"
    else:
        # Escalar linealmente, invertido (alto z → vender)
        signal = -z_score / max_z

        if signal >= 0.5:
            zone = "BUY"
        elif signal <= -0.5:
            zone = "SELL"
        else:
            zone = "NEUTRAL"

    metadata = {
        "z_score": z_score,
        "vwap": vwap,
        "price_mean": price_mean,
        "price_std": price_std,
        "current_price": current_price,
    }

    return signal, zone, metadata


def calculate_vol_breakout_signal(
    df: pd.DataFrame,
    lookback: int = 20,
    atr_period: int = 14,
    atr_mult: float = 0.5,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calcula señal de breakout de volatilidad.

    Returns:
        (signal_value, zone_text, metadata)
    """
    if df.empty or len(df) < max(lookback, atr_period):
        return 0.0, "NEUTRAL", {"reason": "insuficientes datos"}

    recent_df = df.tail(max(lookback, atr_period)).copy()

    # Calcular canal (high/low de los últimos N períodos)
    channel_high = recent_df["high"].tail(lookback).max()
    channel_low = recent_df["low"].tail(lookback).min()

    # Calcular ATR (promedio de rangos true)
    recent_df["tr"] = recent_df.apply(
        lambda row: max(
            row["high"] - row["low"],
            abs(row["high"] - row.get("prev_close", row["close"])),
            abs(row["low"] - row.get("prev_close", row["close"])),
        ),
        axis=1,
    )
    atr = recent_df["tr"].tail(atr_period).mean()

    current_price = df["close"].iloc[-1]

    # Calcular distancia a bandas en múltiplos de ATR
    upper_band = channel_high + atr_mult * atr
    lower_band = channel_low - atr_mult * atr

    # Señal de breakout:
    # Escalar desde -1 (muy abajo del canal) a +1 (muy arriba del canal)

    if current_price > upper_band + atr * 2:
        signal = 1.0
        zone = "BUY"
    elif current_price < lower_band - atr * 2:
        signal = -1.0
        zone = "SELL"
    elif current_price > upper_band:
        # Breakout alcista: escalar de 0.5 a 1.0
        distance = min(atr * 2, current_price - upper_band)
        signal = 0.5 + (distance / (atr * 2)) * 0.5
        zone = "BUY"
    elif current_price < lower_band:
        # Breakout bajista: escalar de -0.5 a -1.0
        distance = min(atr * 2, lower_band - current_price)
        signal = -0.5 - (distance / (atr * 2)) * 0.5
        zone = "SELL"
    else:
        # Dentro del canal
        channel_range = channel_high - channel_low
        if channel_range > 0:
            position = (current_price - channel_low) / channel_range
            signal = (position - 0.5) * 1.0  # -0.5 a +0.5
        else:
            signal = 0.0
        zone = "NEUTRAL"

    metadata = {
        "channel_high": channel_high,
        "channel_low": channel_low,
        "atr": atr,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "current_price": current_price,
    }

    return signal, zone, metadata


def calculate_signal(
    strategy_name: str,
    df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> tuple[float, str, dict[str, Any]]:
    """
    Calcula señal para cualquier estrategia.

    Args:
        strategy_name: Nombre de la estrategia
        df: DataFrame con datos OHLCV
        params: Parámetros de la estrategia (opcional)

    Returns:
        (signal_value, zone_text, metadata)
        signal_value: -1.0 (venta fuerte) a +1.0 (compra fuerte)
        zone_text: Descripción textual de la zona
        metadata: Detalles del cálculo
    """
    params = params or {}

    if strategy_name == "momentum":
        return calculate_momentum_signal(
            df,
            lookback_ticks=params.get("lookback_ticks", 10),
            entry_threshold=params.get("entry_threshold", 0.001),
            exit_threshold=params.get("exit_threshold", 0.0005),
        )
    elif strategy_name == "momentum_v2":
        return calculate_momentum_v2_signal(
            df,
            lookback_ticks=params.get("lookback_ticks", 15),
            entry_threshold=params.get("entry_threshold", 0.0005),
            exit_threshold=params.get("exit_threshold", 0.0003),
            min_volatility=params.get("min_volatility", 0.0001),
            max_volatility=params.get("max_volatility", 0.025),
        )
    elif strategy_name == "vwap_reversion":
        return calculate_vwap_reversion_signal(
            df,
            vwap_window=params.get("vwap_window", 50),
            z_entry=params.get("z_entry", 1.5),
            z_exit=params.get("z_exit", 0.5),
        )
    elif strategy_name == "vol_breakout":
        return calculate_vol_breakout_signal(
            df,
            lookback=params.get("lookback", 20),
            atr_period=params.get("atr_period", 14),
            atr_mult=params.get("atr_mult", 0.5),
        )
    else:
        return 0.0, "UNKNOWN", {"reason": f"estrategia desconocida: {strategy_name}"}

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradeRecord:
    """Trade individual (market data)."""

    timestamp: float
    symbol: str
    price: float
    qty: float
    is_buyer_maker: bool
    run_id: str | None = None


@dataclass
class BarRecord:
    """Barra OHLCV."""

    timestamp: float
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int
    dollar_value: float
    run_id: str | None = None


@dataclass
class FeatureRecord:
    """Features calculados (indicadores técnicos)."""

    timestamp: float
    symbol: str
    feature_name: str
    feature_value: float
    run_id: str | None = None


@dataclass
class SignalRecord:
    """Señal de trading (decisión de estrategia)."""

    timestamp: float
    symbol: str
    signal_type: str  # ENTRY, EXIT, STOP_LOSS, TAKE_PROFIT
    side: str  # BUY, SELL
    price: float
    qty: float
    reason: str
    metadata: str | None = None  # JSON with extra info
    run_id: str | None = None


@dataclass
class EquityRecord:
    """Punto de equity curve."""

    timestamp: float
    symbol: str
    price: float
    position_qty: float
    cash: float
    equity: float
    run_id: str | None = None

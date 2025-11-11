# src/data/bars.py
"""
Utilidades puras para construir velas temporales a partir de ticks (bookTicker o similares).
No hace I/O: recibe listas/dicts y devuelve estructuras listas para volcar a CSV.

Diseño:
- Trabajamos en segundos UNIX (float/int) y agrupamos por ventanas fijas (1s, 60s, etc.).
- El precio de referencia por defecto es el midpoint: mid = (bid + ask)/2.
- No redondeamos ni cuantizamos aquí: entregamos floats “crudos”; el formateo corresponde a quien escriba el CSV.

Tipos de entrada admitidos (por fila):
- dict con claves: ts, bid y ask   (opcionalmente mid).  ts es float/int en segundos UNIX.
- tu pipeline puede añadir campos extra; se ignoran.

Funciones públicas:
- build_time_bars(rows, timeframe_sec=1, symbol="BTCUSDT", price_field="mid")
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from typing import Any


@dataclass
class Bar:
    start_ts: int
    end_ts: int
    symbol: str
    open: float
    high: float
    low: float
    close: float
    n_ticks: int


def _get_price(row: dict[str, Any], price_field: str) -> float | None:
    """
    Obtiene el precio a usar. Si price_field == "mid", calcula (bid+ask)/2.
    Si price_field existe como campo (p.ej. 'close' ya precomputado), lo usa directamente.
    Devuelve None si faltan datos.
    """
    if price_field == "mid":
        bid = row.get("bid")
        ask = row.get("ask")
        if bid is None or ask is None:
            return None
        try:
            return (float(bid) + float(ask)) / 2.0
        except Exception:
            return None
    # Campo directo
    val = row.get(price_field)
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


def _bucket_bounds(ts: float, timeframe_sec: int) -> tuple[int, int]:
    """
    Devuelve (start_ts, end_ts) inclusivo-exclusivo para el bucket temporal.
    """
    t0 = int(ts) - (int(ts) % timeframe_sec)
    return t0, t0 + timeframe_sec - 1  # guardamos end_ts “visual”; el cierre real es t0+timeframe-1


def build_time_bars(
    rows: Iterable[dict[str, Any]],
    timeframe_sec: int = 1,
    symbol: str = "BTCUSDT",
    price_field: str = "mid",
) -> list[Bar]:
    """
    Construye velas OHLC con ventana fija `timeframe_sec` a partir de ticks.

    Parámetros
    ----------
    rows : iterable de dicts
        Cada fila debe contener 'ts' (segundos UNIX). Debe haber 'bid' y 'ask' si price_field='mid',
        o el campo directo indicado (p.ej. 'close').
    timeframe_sec : int
        Tamaño de la vela en segundos (1, 60, etc.).
    symbol : str
        Símbolo a etiquetar en las velas (no se infiere).
    price_field : str
        'mid' para midpoint calculado, o el nombre del campo a usar directamente.

    Retorno
    -------
    List[Bar]
    """
    buckets: dict[int, dict[str, Any]] = {}

    for row in rows:
        ts = row.get("ts")
        if ts is None:
            continue
        try:
            tsf = float(ts)
        except Exception:
            continue

        px = _get_price(row, price_field=price_field)
        if px is None or math.isnan(px):
            continue

        start_ts, end_ts = _bucket_bounds(tsf, timeframe_sec)
        b = buckets.get(start_ts)
        if b is None:
            buckets[start_ts] = {
                "start_ts": start_ts,
                "end_ts": end_ts,
                "open": px,
                "high": px,
                "low": px,
                "close": px,
                "n": 1,
            }
        else:
            b["high"] = max(b["high"], px)
            b["low"] = min(b["low"], px)
            b["close"] = px
            b["n"] += 1

    out: list[Bar] = []
    for k in sorted(buckets.keys()):
        b = buckets[k]
        out.append(
            Bar(
                start_ts=b["start_ts"],
                end_ts=b["end_ts"],
                symbol=symbol,
                open=float(b["open"]),
                high=float(b["high"]),
                low=float(b["low"]),
                close=float(b["close"]),
                n_ticks=int(b["n"]),
            )
        )
    return out

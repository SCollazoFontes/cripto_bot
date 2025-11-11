# src/core/datafeed.py
from __future__ import annotations

from collections.abc import Generator, Iterable

# Lectura y normalización de datos (CSV → barras).
# Responsabilidad:
# - Cargar un CSV con columnas típicas (t, open, high, low, close, volume) o mínimas (t, price/close).
# - Emitir un generador de dicts homogéneos con al menos: {"t": int, "price": float}.
# API pública:
# - iter_csv_bars(path, *, price_col="close") -> Generator[dict, None, None]
from pathlib import Path
from typing import Any

import pandas as pd

_TS_CANDS: tuple[str, ...] = ("t", "ts", "timestamp", "time", "datetime", "ts_ms")
_PRICE_CANDS: tuple[str, ...] = ("price", "close", "mid", "last", "c")


def _detect_ts_col(cols: Iterable[str]) -> str | None:
    for k in _TS_CANDS:
        if k in cols:
            return k
    return None


def _detect_price_col(cols: Iterable[str], prefer: str | None) -> str | None:
    if prefer and prefer in cols:
        return prefer
    for k in _PRICE_CANDS:
        if k in cols:
            return k
    return None


def _to_epoch_seconds(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    # Si parecen milisegundos (valores muy grandes), convierte a segundos.
    if s.dropna().gt(10_000_000_000).any():
        s = s / 1000.0
    return s.astype("Int64")  # enteros con NA seguros


def iter_csv_bars(
    path: str | Path, *, price_col: str = "close"
) -> Generator[dict[str, Any], None, None]:
    """Itera un CSV normalizado a dicts con al menos {"t": int, "price": float}.
    Incluye OHLCV si existen en el CSV.
    """
    p = Path(path).expanduser()
    df = pd.read_csv(p)

    # Detecta columnas
    ts_key = _detect_ts_col(df.columns)
    price_key = _detect_price_col(df.columns, price_col)

    if price_key is None:
        raise KeyError(
            f"No encuentro columna de precio entre {_PRICE_CANDS}; header={list(df.columns)}"
        )

    # Si no hay timestamp, genera un contador simple (1s por fila)
    if ts_key is None:
        df = df.copy()
        df["t"] = pd.RangeIndex(start=0, stop=len(df), step=1).astype("Int64")
    else:
        df = df.copy()
        df["t"] = _to_epoch_seconds(df[ts_key])

    price_series = pd.to_numeric(df[price_key], errors="coerce")

    for i, row in df.iterrows():
        t_val = row["t"]
        p_val = price_series.iat[i]
        if pd.isna(t_val) or pd.isna(p_val):
            continue
        out: dict[str, Any] = {"t": int(t_val), "price": float(p_val)}
        # Añade campos OHLCV si están presentes
        for k in ("open", "high", "low", "close", "volume"):
            if k in df.columns:
                v = row[k]
                if not pd.isna(v):
                    out[k] = float(v)
        yield out

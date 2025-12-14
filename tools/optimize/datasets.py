# tools/optimize/datasets.py
from __future__ import annotations

"""
Utilidades para cargar datasets de barras y recortar ventanas temporales.

El objetivo es reutilizar archivos `data.csv` o equivalentes generados por
los runners existentes y extraer subconjuntos coherentes (ej. último día,
última semana, rango explícito). Esto evita duplicar descargas de mercado y
garantiza que cada optimización trabaje exactamente con los mismos datos.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re

import pandas as pd

TS_CANDIDATES = ["timestamp", "ts", "t", "time", "datetime"]
PRICE_CANDIDATES = ["close", "price", "last", "c"]

WINDOW_RE = re.compile(r"(?P<value>\d+)(?P<unit>[smhdw])$", flags=re.IGNORECASE)
UNIT_TO_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


@dataclass
class DatasetSpec:
    """
    Describe la ubicación de un dataset y columnas principales.

    path: ruta a un CSV con velas (p.ej. runs/<id>/data.csv).
    ts_col / price_col: opcionales; si no se indican se auto-detectan.
    label: nombre amigable (usado en logs/resultados).
    """

    path: Path
    ts_col: str | None = None
    price_col: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        self.path = Path(self.path)


@dataclass
class WindowSlice:
    """
    Resultado de recortar un dataset a una ventana temporal concreta.

    label: identificador (ej. "1d", "custom_jan").
    data: DataFrame filtrado y ordenado por timestamp.
    start_ts / end_ts: límites usados (misma unidad que la columna original).
    ts_unit: "ms" o "s" según se infiera del dataset.
    """

    label: str
    data: pd.DataFrame
    start_ts: float
    end_ts: float
    ts_unit: str


class WindowSpec:
    """
    Describe una ventana temporal solicitada por el usuario.

    Soporta dos formas principales:
        - relativa: duration="1d" (1 día hacia atrás desde el final del dataset)
        - absoluta: start/end como timestamps o strings parseables.
    """

    def __init__(
        self,
        label: str,
        *,
        duration: str | None = None,
        start: str | float | int | None = None,
        end: str | float | int | None = None,
    ) -> None:
        self.label = label
        self.duration = duration
        self.start = start
        self.end = end

    @classmethod
    def from_raw(
        cls, raw: str | tuple[str | float | int, str | float | int] | dict, idx: int
    ) -> WindowSpec:
        if isinstance(raw, WindowSpec):
            return raw
        if isinstance(raw, str):
            key = raw.strip()
            if key.lower() == "all":
                return cls(label=key, start=None, end=None)
            if WINDOW_RE.match(key):
                return cls(label=key, duration=key)
            if "/" in key or ":" in key:
                splitter = "/" if "/" in key else ":"
                left, right = key.split(splitter, 1)
                return cls(label=key, start=left.strip(), end=right.strip())
            raise ValueError(f"No reconozco la ventana '{raw}'. Usa '1d', 'all' o 'start:end'.")
        if isinstance(raw, tuple) and len(raw) == 2:
            start, end = raw
            return cls(label=f"custom_{idx}", start=start, end=end)
        if isinstance(raw, dict):
            label = raw.get("label", f"custom_{idx}")
            duration = raw.get("duration")
            start = raw.get("start")
            end = raw.get("end")
            if duration is None and start is None and end is None:
                raise ValueError(f"Ventana dict inválida: {raw}")
            return cls(label=label, duration=duration, start=start, end=end)
        raise TypeError(f"Tipo de ventana no soportado: {type(raw)}")


def _auto_column(df: pd.DataFrame, preferred: str | None, candidates: list[str]) -> str:
    if preferred and preferred in df.columns:
        return preferred
    for cand in candidates:
        if cand in df.columns:
            return cand
    raise ValueError(f"No se encontró ninguna de las columnas {candidates} en {sorted(df.columns)}")


def _infer_unit(series: pd.Series) -> str:
    if series.empty:
        return "s"
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return "s"
    max_val = float(numeric.max())
    return "ms" if max_val > 10_000_000_000 else "s"


def _parse_relative_duration(spec: str) -> int:
    match = WINDOW_RE.match(spec.strip())
    if not match:
        raise ValueError(f"Ventana relativa inválida: {spec}")
    value = int(match.group("value"))
    unit = match.group("unit").lower()
    return value * UNIT_TO_SECONDS[unit]


def _to_epoch(value: str | float | int, target_unit: str) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    text = str(value).strip()
    if text.isdigit():
        return float(text)
    # Interpretar como ISO-8601
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    epoch = dt.replace(tzinfo=UTC).timestamp()
    return epoch * (1000 if target_unit == "ms" else 1)


def load_dataset(df_spec: DatasetSpec) -> tuple[pd.DataFrame, str, str]:
    if not df_spec.path.exists():
        raise FileNotFoundError(f"No existe el dataset: {df_spec.path}")
    df = pd.read_csv(df_spec.path, low_memory=False, engine="c")
    if df.empty:
        raise ValueError(f"Dataset vacío: {df_spec.path}")
    ts_col = _auto_column(df, df_spec.ts_col, TS_CANDIDATES)
    price_col = _auto_column(df, df_spec.price_col, PRICE_CANDIDATES)
    df = df.copy()
    df[ts_col] = pd.to_numeric(df[ts_col], errors="coerce")
    df = df.dropna(subset=[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)
    return df, ts_col, price_col


def slice_windows(
    df_spec: DatasetSpec,
    windows: Sequence[str | tuple[str | float | int, str | float | int] | dict | WindowSpec],
) -> list[WindowSlice]:
    df, ts_col, _ = load_dataset(df_spec)
    slices: list[WindowSlice] = []
    ts_unit = _infer_unit(df[ts_col])
    ts_values = df[ts_col].to_numpy(dtype="float64")
    min_ts = float(ts_values[0])
    max_ts = float(ts_values[-1])

    # Track current position for sequential duration-based windows (walk-forward)
    current_start = min_ts

    for idx, raw in enumerate(windows):
        ws = WindowSpec.from_raw(raw, idx)
        if ws.duration:
            seconds = _parse_relative_duration(ws.duration)
            span = seconds * (1000 if ts_unit == "ms" else 1)

            # Walk-forward: start from current position and advance by span
            start = current_start
            end = min(start + span, max_ts)

            # Update position for next window
            current_start = end
        else:
            start = ws.start
            end = ws.end
            if start is None:
                start = min_ts
            else:
                start = _to_epoch(start, ts_unit)
            if end is None:
                end = max_ts
            else:
                end = _to_epoch(end, ts_unit)
            if start > end:
                start, end = end, start
        mask = (ts_values >= float(start)) & (ts_values <= float(end))
        window_df = df.loc[mask].copy()
        if window_df.empty:
            continue
        slices.append(
            WindowSlice(
                label=ws.label,
                data=window_df,
                start_ts=float(start),
                end_ts=float(end),
                ts_unit=ts_unit,
            )
        )
    if not slices:
        raise ValueError("Ninguna ventana produjo datos. Revisa las fechas solicitadas.")
    return slices


def iter_windows(
    df_spec: DatasetSpec,
    windows: Sequence[str | tuple[str | float | int, str | float | int] | dict | WindowSpec],
) -> Iterable[WindowSlice]:
    for slc in slice_windows(df_spec, windows):
        yield slc

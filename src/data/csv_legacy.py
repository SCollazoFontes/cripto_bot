# src/data/feeds.py
"""
Feeds de datos unificados para el Engine.

Objetivo
--------
Proveer una interfaz simple de iterador que emite objetos Bar (o dicts compatibles)
desde distintas fuentes:
  - CSV (backtest/paper)
  - (futuro) Binance live

Diseño
------
- `BaseFeed`: protocolo mínimo iterable/sizable.
- `CSVFeed`: lee un CSV (o DataFrame) y emite Bar en orden. Permite:
    * slicing temporal por [t_start, t_end] (en ms epoch o datetime)
    * columnas flexibles: mapea lo que exista; exige por defecto OHLCV y tiempos
    * validación previa con `src.data.validate.validate` (opcional)
- (futuro) `BinanceLiveFeed`: stub con la misma interfaz.

Notas
-----
- Este feed no "duerme" ni simula tiempo; simplemente itera.
- Para live/replay con timing real, añadiremos un `TimedRunner` en el Engine.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import pandas as pd

from bars.base import Bar  # debe existir en tu proyecto
from data.validate import validate


def _to_datetime_ms(x: pd.Series | Sequence | int | float | str) -> pd.Series:
    """
    Normaliza entradas a *ms desde epoch* como Serie numérica.
    - Si es numérica, se asume ms.
    - Si es string/datetime, se parsea y se pasa a ms.
    """
    s = pd.Series(x)
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")  # ya en ms
    dt = pd.to_datetime(s, utc=False, errors="coerce")
    return (dt.view("int64") // 1_000_000).astype("float64")


@dataclass
class BaseFeed:
    """Protocolo mínimo. Subclases deben implementar `__iter__` y `__len__`."""

    def __iter__(self) -> Iterator[Bar]:  # pragma: no cover - interfaz
        raise NotImplementedError

    def __len__(self) -> int:  # pragma: no cover - interfaz
        raise NotImplementedError


class CSVFeed(BaseFeed):
    """
    Feed desde CSV/JSONL/Parquet/DF que emite `Bar` una a una en orden.

    Parámetros
    ----------
    source : str | pd.DataFrame
        Ruta a archivo (.csv, .jsonl, .parquet) o DataFrame ya cargado.
    require : list[str]
        Columnas que deben existir. Por defecto exigimos: t_open,t_close,open,high,low,close,volume
    allow_nan : list[str]
        Columnas en las que se permiten NaNs (por defecto ["gap_ms"]).
    validate_on_init : bool
        Si True, ejecuta `validate` al inicializar y lanza si `ok=False` (estricto).
    t_start, t_end : int | str | pd.Timestamp | None
        Filtro temporal (ms epoch o parseable a datetime). Inclusivo.
    columns_map : dict[str, str] | None
        Permite remapear nombres (p.ej. {"qty":"volume"}).
    """

    DEFAULT_REQUIRE = ["t_open", "t_close", "open", "high", "low", "close", "volume"]

    def __init__(
        self,
        source: str | pd.DataFrame,
        *,
        require: list[str] | None = None,
        allow_nan: list[str] | None = None,
        validate_on_init: bool = True,
        t_start: int | float | str | pd.Timestamp | None = None,
        t_end: int | float | str | pd.Timestamp | None = None,
        columns_map: dict[str, str] | None = None,
        sort_by_topen: bool = True,
    ) -> None:
        self._df = self._load_any(source)
        if columns_map:
            self._df = self._df.rename(columns=columns_map)

        # Filtro temporal (si se aporta)
        if t_start is not None or t_end is not None:
            if "t_open" not in self._df or "t_close" not in self._df:
                raise ValueError("Para filtrar por tiempo se requieren columnas t_open y t_close.")
            to_ms = _to_datetime_ms(self._df["t_open"])
            tc_ms = _to_datetime_ms(self._df["t_close"])
            mask = pd.Series(True, index=self._df.index)
            if t_start is not None:
                ts_ms = _to_datetime_ms([t_start]).iloc[0]
                mask &= tc_ms >= ts_ms  # barras que cierran después de inicio
            if t_end is not None:
                te_ms = _to_datetime_ms([t_end]).iloc[0]
                mask &= to_ms <= te_ms  # barras que abren antes de fin
            self._df = self._df.loc[mask].copy()

        # Orden por t_open si procede
        if sort_by_topen and "t_open" in self._df.columns:
            self._df = self._df.sort_values("t_open", kind="stable").reset_index(drop=True)

        # Validación
        req = list(require or self.DEFAULT_REQUIRE)
        allow = list(allow_nan or ["gap_ms"])
        if validate_on_init:
            res = validate(self._df, require_columns=req, allow_nan_in=allow)
            if not res["ok"]:
                # Mensaje compacto; deja al usuario ver issues con inspect_last si quiere detalle
                raise ValueError(
                    "CSVFeed: datos no válidos (validador ok=False). "
                    f"Faltan: {res.get('missing_columns', [])}; "
                    f"Issues: {[i['code'] for i in res.get('issues', [])][:5]}"
                )

        # Proyección a las columnas típicas de Bar si existen
        self._bar_columns = [
            c
            for c in [
                "t_open",
                "t_close",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "dollar_value",
                "trade_count",
                "duration_ms",
                "gap_ms",
                "overshoot",
                "overshoot_pct",
                "symbol",
                "rule",
                "limit",
                "session_id",
                "bar_index",
            ]
            if c in self._df.columns
        ]

    @staticmethod
    def _load_any(source: str | pd.DataFrame) -> pd.DataFrame:
        if isinstance(source, pd.DataFrame):
            return source.copy()
        path = str(source)
        if path.endswith(".csv"):
            return pd.read_csv(path)
        if path.endswith(".jsonl"):
            return pd.read_json(path, lines=True)
        if path.endswith(".parquet"):
            return pd.read_parquet(path)
        # fallback CSV por defecto
        return pd.read_csv(path)

    def __len__(self) -> int:
        return int(len(self._df))

    def __iter__(self) -> Iterator[Bar]:
        # Convertimos cada fila en Bar. Si tu Bar es un dataclass/NamedTuple, adapta aquí.
        # Asumimos que Bar(**row_dict) es válido si las keys casan con el dataclass.
        for _, row in self._df.iterrows():
            payload = row.to_dict()
            # Si Bar requiere exclusivamente un subconjunto, filtramos:
            if hasattr(Bar, "__annotations__"):  # dataclass/typing aware
                fields = set(getattr(Bar, "__annotations__", {}).keys())
                payload = {k: v for k, v in payload.items() if k in fields}
            yield Bar(**payload)


# === Stub para live (se implementará en el siguiente paso) ===================


class BinanceLiveFeed(BaseFeed):
    """
    Stub de feed live desde Binance manteniendo la misma interfaz.
    Implementación real en el siguiente archivo (binance client + stream).
    """

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("BinanceLiveFeed aún no implementado. Usa CSVFeed por ahora.")

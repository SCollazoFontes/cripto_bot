# src/core/decisions_log.py
"""Persistencia del log de decisiones.

Responsabilidad
---------------
- Normalizar y escribir decisions.csv con esquema estable.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from core.types import DecisionRow

# Orden canónico de columnas (para estabilidad y tests)
DECISIONS_COLUMNS: list[str] = [
    "t",  # timestamp (segundos UNIX)
    "price",  # precio de referencia en la decisión
    "side",  # BUY/SELL/None
    "qty",  # cantidad solicitada o 0.0 si no aplica
    "decision",  # texto corto: entry/exit/hold/...
    "reason",  # texto libre opcional
]


def decisions_to_dataframe(rows: Iterable[DecisionRow]) -> pd.DataFrame:
    """
    Convierte una secuencia de DecisionRow a DataFrame,
    asegurando columnas y tipos básicos.
    """
    data = list(rows)
    if not data:
        return pd.DataFrame(columns=DECISIONS_COLUMNS)

    df = pd.DataFrame(data)

    # Añade columnas que falten con valores por defecto
    default: dict[str, Any] = {
        "t": 0,
        "price": 0.0,
        "side": None,
        "qty": 0.0,
        "decision": "",
        "reason": "",
    }
    for col in DECISIONS_COLUMNS:
        if col not in df.columns:
            df[col] = default[col]

    # Casts suaves
    if "t" in df:
        df["t"] = pd.to_numeric(df["t"], errors="coerce").fillna(0).astype(int)
    for k in ("price", "qty"):
        if k in df:
            df[k] = pd.to_numeric(df[k], errors="coerce").fillna(0.0).astype(float)

    return df[DECISIONS_COLUMNS]


def write_decisions_csv(run_dir: Path | str, rows: Iterable[DecisionRow]) -> None:
    """
    Escribe decisions.csv en <run_dir>. Si no hay filas, no crea el archivo.
    """
    rows_list = list(rows)
    if not rows_list:
        return
    path = Path(run_dir)
    path.mkdir(parents=True, exist_ok=True)
    df = decisions_to_dataframe(rows_list)
    df.to_csv(path / "decisions.csv", index=False)

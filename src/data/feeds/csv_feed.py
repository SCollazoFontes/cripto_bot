"""
Carga y validación de datos desde un CSV para backtesting.

Convierte un archivo CSV con micro-barras en una lista de diccionarios con claves consistentes,
validando la presencia de campos mínimos como 't' (timestamp) y 'close' (precio de referencia).
"""

from __future__ import annotations

import csv
from typing import Any


def load_csv_feed(path: str) -> list[dict[str, Any]]:
    """
    Carga un CSV con micro-barras y devuelve una lista de dicts.

    Cada fila se convierte en un diccionario con claves según las cabeceras.
    El campo 't' (timestamp) se convierte a int; los campos numéricos a float si es posible.
    Se validan los campos mínimos necesarios para backtest.

    Args:
        path: Ruta al archivo CSV.

    Returns:
        Lista de barras como diccionarios.

    Raises:
        ValueError: Si faltan columnas mínimas requeridas.
    """
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []

        for row in reader:
            parsed: dict[str, Any] = {}
            for k, v in row.items():
                if k == "t":
                    parsed[k] = int(float(v))
                else:
                    try:
                        parsed[k] = float(v)
                    except (ValueError, TypeError):
                        parsed[k] = v
            rows.append(parsed)

    if not rows:
        raise ValueError(f"CSV vacío: {path}")

    required_fields = {"t", "close"}
    missing = required_fields - set(rows[0].keys())
    if missing:
        raise ValueError(f"Faltan columnas requeridas en {path}: {missing}")

    return rows

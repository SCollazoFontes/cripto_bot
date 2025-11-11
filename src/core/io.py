# src/core/io.py
from __future__ import annotations

"""
I/O de resultados del runner.

Responsabilidad
---------------
- Persistir equity.csv y trades.csv con esquema estable.
- Invocar el generador de métricas (summary.json) de forma segura.

API pública
-----------
- write_equity_and_trades_csv(run_dir, equity_rows, trades_rows) -> None
- maybe_write_summary(run_dir) -> None
"""

from collections.abc import Iterable
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pandas as pd

from core.types import EquityRow, TradeRow

_EQUITY_COLS: list[str] = ["t", "price", "qty", "cash", "equity"]
_TRADES_COLS: list[str] = ["t", "side", "price", "qty", "cash", "equity", "reason"]


def ensure_dir(path: str | Path) -> None:
    """
    Crea el directorio si no existe, equivalente a `mkdir -p`.
    No lanza error si ya existe.
    """
    Path(path).mkdir(parents=True, exist_ok=True)


def _to_df_equity(rows: Iterable[EquityRow]) -> pd.DataFrame:
    df = pd.DataFrame(list(rows))
    if df.empty:
        return pd.DataFrame(columns=_EQUITY_COLS)
    return df[_EQUITY_COLS]


def _to_df_trades(rows: Iterable[TradeRow]) -> pd.DataFrame:
    df = pd.DataFrame(list(rows))
    if df.empty:
        # ⚠️ Arreglado: antes había un typo "columns[_TRADES_COLS]" que rompía los tests
        return pd.DataFrame(columns=_TRADES_COLS)
    return df[_TRADES_COLS]


def write_equity_and_trades_csv(
    run_dir: Path | str,
    equity_rows: Iterable[EquityRow],
    trades_rows: Iterable[TradeRow],
) -> None:
    """
    Escribe equity.csv y trades.csv (si hay trades) en <run_dir>.
    """
    path = Path(run_dir)
    path.mkdir(parents=True, exist_ok=True)

    df_eq = _to_df_equity(equity_rows)
    df_eq.to_csv(path / "equity.csv", index=False)

    df_tr = _to_df_trades(trades_rows)
    if not df_tr.empty:
        df_tr.to_csv(path / "trades.csv", index=False)


def maybe_write_summary(run_dir: Path | str) -> None:
    """
    Lanza tools/make_metrics.py para generar summary.json.
    No propaga excepciones (modo robusto).
    """
    try:
        cmd = [sys.executable, "tools/make_metrics.py", "--run-dir", str(run_dir)]
        subprocess.run(cmd, check=True)
    except Exception:
        # Silencioso: el runner no debe fallar por métricas.
        pass


def save_summary_json(run_dir: str | Path, summary: dict[str, Any]) -> None:
    """
    Guarda el diccionario `summary` como JSON en el directorio de ejecución.
    Sobrescribe el archivo si ya existía.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

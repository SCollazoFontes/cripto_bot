# src/report/metrics_basic.py

"""
metrics_basic.py — cálculo de métricas básicas de backtests.
Lee equity.csv (y opcionalmente trades.csv) y calcula retornos por barra, drawdown y ratios básicos.

Compatibilidad:
- compute_metrics() acepta tanto un run_dir como una ruta al archivo equity.csv.

API expuesta (usada por tools.make_metrics):
- compute_metrics(path: str) -> dict
- to_dict(obj) -> Any
- write_summary_json(run_dir: str, metrics: dict) -> Path
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


# ----------------------- Utilidades de serialización ----------------------- #
def _np_float(x: Any) -> float | None:
    try:
        if x is None or (isinstance(x, float) and (np.isnan(x))):
            return None
        if isinstance(x, (np.floating,)):
            return float(x)
        if isinstance(x, (np.integer,)):
            return float(int(x))
        if isinstance(x, (pd.Timestamp, pd.Timedelta)):
            return x.isoformat()
        return float(x)
    except Exception:
        return None


def to_dict(obj: Any) -> Any:
    """Convierte np/pd a tipos nativos compatibles con JSON."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (np.floating, np.integer)):
        return _np_float(obj)
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (pd.Series, pd.Index)):
        return [to_dict(v) for v in obj.tolist()]
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    # dataclass
    try:
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    except Exception:
        return str(obj)


# --------------------------- Resolución de rutas --------------------------- #
def _resolve_paths(path_like: Path) -> tuple[Path, Path, Path]:
    """
    Acepta:
      - run_dir (directorio que contiene equity.csv y trades.csv), o
      - ruta directa a equity.csv
    Devuelve: (run_path, equity_csv, trades_csv)
    """
    p = path_like
    if p.is_dir():
        run_path = p
        equity_csv = run_path / "equity.csv"
        trades_csv = run_path / "trades.csv"
    else:
        # Si es archivo, asumimos equity.csv
        # run_dir es su padre
        run_path = p.parent
        equity_csv = p
        trades_csv = run_path / "trades.csv"

    return run_path, equity_csv, trades_csv


# --------------------------- Lectura y limpieza ---------------------------- #
def _read_equity(equity_csv: Path) -> pd.DataFrame:
    if not equity_csv.exists():
        raise FileNotFoundError(f"No existe equity.csv: {equity_csv}")
    # Lectura tolerante
    df = pd.read_csv(equity_csv, on_bad_lines="skip")

    cols = set(map(str, df.columns))
    expected = {"t", "price", "qty", "cash", "equity"}

    if expected.issubset(cols):
        pass
    elif {"t", "equity"}.issubset(cols):
        # Normalizamos nombres mínimos necesarios
        rename_map = {}
        if "price" not in cols:
            for cand in ["close", "price_close", "p"]:
                if cand in cols:
                    rename_map[cand] = "price"
                    break
        if "qty" not in cols:
            for cand in ["quantity", "position_size", "qty_pos"]:
                if cand in cols:
                    rename_map[cand] = "qty"
                    break
        if rename_map:
            df = df.rename(columns=rename_map)
        # Rellenos seguros
        if "price" not in df.columns:
            df["price"] = np.nan
        if "qty" not in df.columns:
            df["qty"] = 0.0
        if "cash" not in df.columns:
            if "price" in df.columns and np.isfinite(df["price"].fillna(np.nan)).any():
                df["cash"] = df["equity"] - df["qty"].fillna(0.0) * df["price"].fillna(0.0)
            else:
                df["cash"] = df["equity"]
    else:
        raise ValueError(f"Esquema de equity.csv no reconocido. Columnas: {list(df.columns)}")

    # Tipos
    for c in ["t", "price", "qty", "cash", "equity"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Orden por 't' si existe
    if "t" in df.columns:
        df = df.sort_values("t").reset_index(drop=True)

    # Filtrado de filas vacías
    df = df.dropna(subset=["equity"])

    return df[["t", "price", "qty", "cash", "equity"]]


def _read_trades(trades_csv: Path) -> pd.DataFrame | None:
    if not trades_csv.exists():
        return None
    df = pd.read_csv(trades_csv, on_bad_lines="skip")
    if df.empty or len(df.columns) == 0:
        return None
    # Normalizamos columnas mínimas
    cols = set(df.columns)
    rename = {}
    if "side" not in cols:
        for c in ["direction", "action"]:
            if c in cols:
                rename[c] = "side"
                break
    if "price" not in cols:
        for c in ["fill_price", "exec_price"]:
            if c in cols:
                rename[c] = "price"
                break
    if rename:
        df = df.rename(columns=rename)
    return df


# ------------------------------- Métricas --------------------------------- #
@dataclass
class BasicStats:
    bars: int
    start_equity: float
    end_equity: float
    total_return: float
    mean_ret_per_bar: float
    std_ret_per_bar: float
    sharpe_per_bar: float | None
    max_drawdown: float
    max_dd_start_idx: int | None
    max_dd_end_idx: int | None
    n_trades: int
    notes: Tuple[str, ...]


def _compute_drawdown(equity: pd.Series) -> Tuple[pd.Series, float, int | None, int | None]:
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    mdd = float(dd.min()) if len(dd) else 0.0
    end_idx = int(dd.idxmin()) if len(dd) else None
    start_idx = None
    if end_idx is not None:
        eq_slice = equity.iloc[: end_idx + 1]
        if len(eq_slice):
            start_idx = int(eq_slice.idxmax())
    return dd, mdd, start_idx, end_idx


def compute_metrics(path: str) -> Dict[str, Any]:
    """
    Calcula métricas a partir de:
      - path = run_dir (directorio)  o
      - path = ruta a equity.csv (archivo)

    Devuelve un dict listo para convertir a JSON.
    """
    run_path, equity_csv, trades_csv = _resolve_paths(Path(path))

    df = _read_equity(equity_csv)
    trades = _read_trades(trades_csv)

    bars = int(len(df))
    start_equity = float(df["equity"].iloc[0])
    end_equity = float(df["equity"].iloc[-1])

    # Retornos por barra
    ret = df["equity"].pct_change().fillna(0.0)
    mean_ret = float(ret.mean()) if bars > 0 else 0.0
    std_ret = float(ret.std(ddof=0)) if bars > 1 else 0.0
    sharpe = (mean_ret / std_ret) if (std_ret and std_ret > 0) else None

    # Drawdown
    dd, mdd, dd_start, dd_end = _compute_drawdown(df["equity"])

    # Trades
    n_trades = int(len(trades)) if trades is not None else 0

    notes = []
    if n_trades == 0:
        notes.append("no_trades")
    if end_equity == start_equity:
        notes.append("flat_equity")

    stats = BasicStats(
        bars=bars,
        start_equity=start_equity,
        end_equity=end_equity,
        total_return=(end_equity / start_equity - 1.0) if start_equity else 0.0,
        mean_ret_per_bar=mean_ret,
        std_ret_per_bar=std_ret,
        sharpe_per_bar=sharpe,
        max_drawdown=mdd,
        max_dd_start_idx=dd_start,
        max_dd_end_idx=dd_end,
        n_trades=n_trades,
        notes=tuple(notes),
    )

    out: Dict[str, Any] = {
        "ok": True,
        "issues": notes,
        "stats": to_dict(stats),
        "equity_head": to_dict(df.head(5)),
        "equity_tail": to_dict(df.tail(5)),
        "run_dir": str(run_path),
        "equity_path": str(equity_csv),
        "trades_path": str(trades_csv),
    }

    if trades is not None and not trades.empty:
        out["trades_head"] = to_dict(trades.head(5))
        out["trades_tail"] = to_dict(trades.tail(5))

    return out


# --------------------------- Escritura de resumen -------------------------- #
def write_summary_json(run_dir: str, metrics: Dict[str, Any]) -> Path:
    """Escribe summary.json en el run_dir con las métricas calculadas."""
    run_path = Path(run_dir)
    out_path = run_path / "summary.json"
    payload = to_dict(metrics)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path

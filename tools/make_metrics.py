# ruff: noqa: E402
from __future__ import annotations

"""
Generación de métricas y summary.json para un run.

Uso:
    PYTHONPATH=. python tools/make_metrics.py --run-dir runs/2025XXXXXXXXZ
    # Opcional:
    # --annualization-days 365

Convenciones de entrada (dentro de --run-dir):
- equity.csv  con columnas: t, price, qty, cash, equity
- trades.csv  con columnas: t, side, price, qty, cash, equity, reason (opcional)

Salida:
- summary.json con métricas agregadas (rendimiento, drawdown, sharpe,
  cagr, métricas de trades)

Notas:
- Si existe `report/metrics_basic.py` (o `report/metrics.py`) con API
  compatible, se usa como fuente de verdad.
- Si no existe o falla el import, se calculan métricas con fallback local.
- CAGR usa los timestamps (ms) de equity.csv para estimar años transcurridos.
- Sharpe anualiza con barras_por_año = n_bars / años (si años>0) o
  'annualization_days'.
"""

# --- Asegurar que 'src/' está en sys.path cuando se ejecuta desde CLI ---
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
# -------------------------------------------------------------------------

# === Imports estándar del script ===
import argparse
from collections.abc import Callable
from dataclasses import dataclass
import importlib
import json
from typing import Any, cast

import numpy as np
import pandas as pd


# === Resolver de make_summary con fallback local ===========================
def _resolve_make_summary() -> Callable[..., dict[str, Any]]:
    """
    Devuelve una función:
        make_summary(eq_df, tr_df=None, annualization_days=365) -> dict

    Intenta, en orden:
      1) report.metrics_basic.make_summary
      2) report.metrics.make_summary
      3) fallback local (_make_summary_local)
    """
    for mod_name in ("report.metrics_basic", "report.metrics"):
        try:
            mod = importlib.import_module(mod_name)
            func = getattr(mod, "make_summary", None)
            if callable(func):
                return cast(Callable[..., dict[str, Any]], func)
        except Exception:
            pass

    # Fallback local
    def _ms_local(
        eq_df: pd.DataFrame,
        tr_df: pd.DataFrame | None = None,
        *,
        annualization_days: int = 365,
    ) -> dict[str, Any]:
        df_tr = tr_df if tr_df is not None else pd.DataFrame()
        return _make_summary_local(eq_df, df_tr, annualization_days=annualization_days)

    return _ms_local


make_summary = _resolve_make_summary()


# =========================== Lectura de archivos ============================
def _read_equity_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    lower = {c.lower(): c for c in df.columns}
    # normalizamos nombres básicos
    rename: dict[str, str] = {}
    for k in ("t", "price", "qty", "cash", "equity"):
        if k in lower:
            rename[lower[k]] = k
    if rename:
        df = df.rename(columns=rename)
    required = {"t", "equity"}
    if not required.issubset(set(df.columns)):
        raise ValueError(
            f"equity.csv debe contener columnas {sorted(required)}; tiene {list(df.columns)}"
        )
    return df


def _read_trades_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        # devolver DataFrame vacío con columnas típicas
        return pd.DataFrame(columns=["t", "side", "price", "qty", "cash", "equity", "reason"])
    df = pd.read_csv(path)
    lower = {c.lower(): c for c in df.columns}
    rename: dict[str, str] = {}
    for k in ("t", "side", "price", "qty", "cash", "equity", "reason"):
        if k in lower:
            rename[lower[k]] = k
    if rename:
        df = df.rename(columns=rename)
    return df


# ========================= Métricas (fallback local) ========================
@dataclass
class _Drawdown:
    max_dd: float
    peak_idx: int
    trough_idx: int


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den not in (0.0, 0) else 0.0


def _compute_drawdown(equity: pd.Series) -> _Drawdown:
    # equity en valores (no returns)
    cummax = equity.cummax()
    dd = (equity - cummax) / cummax.replace(0, np.nan)
    dd = dd.fillna(0.0)  # por seguridad si hay NaN (p.ej. cummax 0)
    trough_idx = int(dd.idxmin()) if not dd.empty else 0
    max_dd = float(-dd.min()) if not dd.empty else 0.0  # positivo
    peak_idx = (
        int((equity[: trough_idx + 1]).idxmax()) if trough_idx >= 0 and not equity.empty else 0
    )
    return _Drawdown(max_dd=max_dd, peak_idx=peak_idx, trough_idx=trough_idx)


def _years_between_ms(t0_ms: int, t1_ms: int) -> float:
    if t1_ms <= t0_ms:
        return 0.0
    secs = (t1_ms - t0_ms) / 1000.0
    return secs / (365.0 * 24.0 * 3600.0)


def _compute_sharpe_from_series(returns: pd.Series, bars_per_year: float) -> float:
    mu = float(returns.mean()) if not returns.empty else 0.0
    sd = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    if sd == 0.0:
        return 0.0
    return mu / sd * float(np.sqrt(bars_per_year))


def _trades_metrics(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {
            "n_trades": 0.0,
            "winrate": 0.0,
            "profit_factor": 0.0,
            "avg_trade": 0.0,
        }
    # Asumimos que 'equity' en trades.csv es el equity tras cada ejecución
    eq = pd.to_numeric(trades["equity"], errors="coerce").dropna().reset_index(drop=True)
    if len(eq) == 0:
        return {
            "n_trades": 0.0,
            "winrate": 0.0,
            "profit_factor": 0.0,
            "avg_trade": 0.0,
        }
    pnl = eq.diff().dropna()
    n_trades = float(len(pnl))
    profits = pnl[pnl > 0].sum() if not pnl.empty else 0.0
    losses = -pnl[pnl < 0].sum() if not pnl.empty else 0.0
    winrate = float((pnl > 0).mean()) if not pnl.empty else 0.0
    profit_factor = (
        _safe_div(float(profits), float(losses))
        if losses > 0
        else (0.0 if profits == 0 else float("inf"))
    )
    avg_trade = float(pnl.mean()) if not pnl.empty else 0.0
    return {
        "n_trades": float(n_trades),
        "winrate": float(winrate),
        "profit_factor": float(profit_factor),
        "avg_trade": float(avg_trade),
    }


def _make_summary_local(
    eq_df: pd.DataFrame,
    tr_df: pd.DataFrame,
    *,
    annualization_days: int,
) -> dict[str, Any]:
    eq = pd.to_numeric(eq_df["equity"], errors="coerce").ffill().astype(float)
    n_bars = int(len(eq))
    start_equity = float(eq.iloc[0]) if n_bars > 0 else 0.0
    end_equity = float(eq.iloc[-1]) if n_bars > 0 else 0.0
    total_return = (
        _safe_div((end_equity - start_equity), start_equity) if start_equity != 0 else 0.0
    )

    # Drawdown
    dd = _compute_drawdown(eq)

    # Years transcurridos por timestamps
    t0 = int(eq_df["t"].iloc[0])
    t1 = int(eq_df["t"].iloc[-1])
    years = _years_between_ms(t0, t1)
    bars_per_year = _safe_div(float(n_bars), years) if years > 0 else float(annualization_days)

    # Sharpe a partir de returns por barra
    rets = eq.pct_change().dropna()
    sharpe = _compute_sharpe_from_series(rets, bars_per_year=bars_per_year)

    # CAGR geométrico
    cagr = 0.0
    if years > 0 and start_equity > 0 and end_equity > 0:
        cagr = float((end_equity / start_equity) ** (1.0 / years) - 1.0)

    # Trades
    tmetrics = _trades_metrics(tr_df)

    return {
        "n_bars": n_bars,
        "start_equity": start_equity,
        "end_equity": end_equity,
        "total_return": total_return,
        "max_drawdown": dd.max_dd,
        "sharpe": sharpe,
        "cagr": cagr,
        "peak_index": dd.peak_idx,
        "trough_index": dd.trough_idx,
        "trades": tmetrics,
    }


# ================================ CLI / Main ================================
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--run-dir",
        required=True,
        help="Directorio del run (equity.csv, trades.csv opcional).",
    )
    p.add_argument(
        "--annualization-days",
        type=int,
        default=365,
        help=("Días por año para anualizar Sharpe si no se puede inferir por timestamps."),
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    run_dir = Path(args.run_dir)
    eq_path = run_dir / "equity.csv"
    tr_path = run_dir / "trades.csv"

    if not eq_path.exists():
        raise FileNotFoundError(f"No existe {eq_path}")

    eq_df = _read_equity_csv(eq_path)
    tr_df = _read_trades_csv(tr_path)

    summary = make_summary(
        eq_df,
        tr_df if len(tr_df) else None,
        annualization_days=int(args.annualization_days),
    )
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"✅ summary.json escrito: {run_dir / 'summary.json'}")


if __name__ == "__main__":
    main()

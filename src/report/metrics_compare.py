# src/report/metrics_compare.py
"""
Módulo de métricas comunes para runs de backtest y stream.

Objetivo
--------
Calcular métricas clave y compararlas entre múltiples runs a partir de `equity.csv`
y (opcionalmente) `trades.csv`.

Métricas calculadas
- Retornos: retorno total; CAGR (si la duración mínima lo permite).
- Riesgo: volatilidad anualizada, max drawdown (y duración), Calmar.
- Ratios: Sharpe y Sortino (anualizados) cuando hay suficientes muestras.
- Trading: nº de operaciones, win rate por roundtrip (FIFO), tamaño medio, exposición.
- Costes: fees totales y en bps sobre notional, slippage medio en bps (si existe).
- Latencias: media/p50/p95/p99/max para columnas *_latency_ms (si existen).

Robustez y umbrales
- Detección flexible de columna temporal en `equity.csv`: ["t","ts","timestamp","time","t_close","t_open"]
  o la más monótona; si no parece epoch-ms, se intenta usar tiempos de `data.csv` del mismo run.
- Para evitar cifras engañosas en runs muy cortos:
  * Si la duración < MIN_ANNUALIZATION_DAYS (por defecto 7 días), **no** se reporta CAGR/Sharpe/Sortino/Calmar (NaN).
  * Sharpe/Sortino/vol_ann requieren al menos MIN_RISK_STEPS (por defecto 30) retornos paso a paso.
- Si `trades.csv` no tiene `fee`, se asume 0.0 de manera segura (sin `.fillna()` sobre escalares).

CLI
----
    python -m src.report.metrics_compare --glob "runs/2025110*T*" --print --markdown
    python -m src.report.metrics_compare --runs runs/A runs/B --sort-by sharpe --asc --out metrics.csv

Parámetros (como constantes)
- MIN_ANNUALIZATION_DAYS = 7
- MIN_RISK_STEPS = 30
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SECONDS_PER_YEAR = 365 * 24 * 60 * 60
MIN_ANNUALIZATION_DAYS = 7  # mínimo para calcular CAGR/Sharpe/Sortino/Calmar
MIN_RISK_STEPS = 30  # mínimo de retornos para volatilidad/Sharpe/Sortino


@dataclass
class Roundtrip:
    open_t: int
    close_t: int
    qty: float
    pnl: float  # neto de fees
    gross_pnl: float  # antes de fees
    fees: float
    open_price: float
    close_price: float


def _read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    return pd.read_csv(path, on_bad_lines="skip")


def _pick_time_column(df: pd.DataFrame) -> str:
    candidates = ["t", "ts", "timestamp", "time", "t_close", "t_open"]
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c in lower_map:
            return lower_map[c]
    # fallback: columna numérica aproximadamente monótona
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() >= 3:
            dif = s.diff().dropna()
            if (dif >= 0).mean() > 0.7:
                return c
    raise KeyError(
        "No se encontró columna temporal en equity.csv (esperado t/ts/timestamp/time/t_close/t_open)."
    )


def _looks_like_epoch_ms(series: pd.Series) -> bool:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return False
    return s.max() >= 1e11 and s.min() >= 1e9  # laxo: evita 1..N


def _backfill_time_from_data(run_dir: Path, n_rows: int) -> pd.Series | None:
    """Si equity.t no parece epoch-ms, intenta tiempos de data.csv con el mismo nº de filas."""
    data_path = run_dir / "data.csv"
    if not data_path.exists():
        return None
    df_data = _read_csv_safe(data_path)
    try:
        tcol = _pick_time_column(df_data)
    except Exception:
        return None
    t = pd.to_numeric(df_data[tcol], errors="coerce").dropna().astype("int64")
    if len(t) == n_rows:
        return t.reset_index(drop=True)
    if len(t) > 1:
        # interpolación lineal para ajustar conteo de filas
        t_idx = pd.Series(np.linspace(t.iloc[0], t.iloc[-1], num=n_rows)).astype("int64")
        return t_idx
    return None


def _ensure_equity(run_dir: Path, df_equity: pd.DataFrame) -> pd.DataFrame:
    time_col = _pick_time_column(df_equity)
    lower_map = {c.lower(): c for c in df_equity.columns}
    eq_col = lower_map.get("equity") or lower_map.get("nav") or lower_map.get("balance")
    if not eq_col:
        raise KeyError("No se encontró columna 'equity' (ni 'nav'/'balance') en equity.csv.")

    df = df_equity[[time_col, eq_col]].copy()
    df.columns = ["t", "equity"]
    df["equity"] = pd.to_numeric(df["equity"], errors="coerce")
    df["t"] = pd.to_numeric(df["t"], errors="coerce")

    # Si t no parece epoch-ms, intentar backfill desde data.csv
    if not _looks_like_epoch_ms(df["t"]):
        back = _backfill_time_from_data(run_dir, len(df))
        if back is not None:
            df["t"] = back
        else:
            # último recurso: 1s por paso desde un base fijo
            base = 1_700_000_000_000  # ~2023-11 aprox
            df["t"] = base + (df.reset_index().index.values.astype("int64")) * 1000

    df = df.dropna().drop_duplicates().sort_values("t")
    df = df[df["equity"].gt(0) & df["t"].notna()]
    if len(df) < 2:
        raise ValueError("equity.csv no tiene suficientes filas válidas.")
    return df.reset_index(drop=True).astype({"t": "int64"})


def _last_close_time_from_trades(run_dir: Path) -> int | None:
    """Devuelve el timestamp del último SELL si existe, o último trade si no hay SELL.

    Esto permite recortar equity.csv hasta el último cierre de estrategia, evitando
    que el retorno se calcule con el último valor del dataset cuando ya no hay
    posición abierta.
    """
    trades_path = run_dir / "trades.csv"
    if not trades_path.exists():
        return None
    try:
        td = _read_csv_safe(trades_path)
        tcol = _pick_time_column(td)
    except Exception:
        return None

    lower_map = {c.lower(): c for c in td.columns}
    side_col = lower_map.get("side")
    ts = pd.to_numeric(td[tcol], errors="coerce")

    # Priorizar el último SELL (cierre de posición). Si no hay, usar último trade.
    if side_col:
        sells = td[side_col].astype(str).str.upper() == "SELL"
        ts_sell = ts[sells]
        if ts_sell.notna().any():
            return int(ts_sell.max())

    return int(ts.max()) if ts.notna().any() else None


def _max_drawdown(equity: pd.Series) -> tuple[float, int]:
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    mdd = dd.min()
    duration = (dd < 0).astype(int).groupby((dd == 0).astype(int).cumsum()).sum().max()
    return float(mdd), int(duration if pd.notna(duration) else 0)


def _annualize_vol_from_irregular(returns: pd.Series, t_ms: pd.Series) -> float:
    if len(returns) < MIN_RISK_STEPS:
        return float("nan")
    dt_seconds = (t_ms.iloc[-1] - t_ms.iloc[0]) / 1000.0
    if dt_seconds <= 0:
        return float("nan")
    sigma_step = float(np.nanstd(returns, ddof=1))
    steps_per_year = SECONDS_PER_YEAR / (dt_seconds / len(returns))
    return float(sigma_step * np.sqrt(steps_per_year))


def _cagr(e0: float, e1: float, t0_ms: int, t1_ms: int) -> float:
    if e0 <= 0 or e1 <= 0:
        return float("nan")
    dt_seconds = max((t1_ms - t0_ms) / 1000.0, 1e-9)
    years = dt_seconds / SECONDS_PER_YEAR
    if years < MIN_ANNUALIZATION_DAYS / 365.0:
        return float("nan")  # no anualizamos runs demasiado cortos
    return float(np.exp(np.log(e1 / e0) / years) - 1.0)


def _sortino(returns: pd.Series, t_ms: pd.Series) -> float:
    if len(returns) < MIN_RISK_STEPS:
        return float("nan")
    dt_seconds = (t_ms.iloc[-1] - t_ms.iloc[0]) / 1000.0
    if dt_seconds <= 0:
        return float("nan")
    downside = returns[returns < 0]
    if len(downside) < 2:
        return float("nan")
    dd_sigma_step = float(np.nanstd(downside, ddof=1))
    steps_per_year = SECONDS_PER_YEAR / (dt_seconds / len(returns))
    dd_sigma_ann = dd_sigma_step * np.sqrt(steps_per_year)
    mean_step = float(np.nanmean(returns))
    mean_ann = mean_step * steps_per_year
    return float(mean_ann / dd_sigma_ann) if dd_sigma_ann > 0 else float("nan")


def _form_roundtrips(df_trades: pd.DataFrame) -> list[Roundtrip]:
    cols = {c.lower(): c for c in df_trades.columns}
    need = {"t", "side", "qty", "price"}
    if not need.issubset(set(cols.keys())):
        return []
    tcol, scol, qcol, pcol = (cols["t"], cols["side"], cols["qty"], cols["price"])
    fcol = cols.get("fee")

    use_cols = [tcol, scol, qcol, pcol] + ([fcol] if fcol else [])
    df = df_trades[use_cols].copy()
    df.columns = ["t", "side", "qty", "price"] + (["fee"] if fcol else [])

    if "fee" in df.columns:
        df["fee"] = pd.to_numeric(df["fee"], errors="coerce").fillna(0.0)
    else:
        df["fee"] = 0.0  # evitar .fillna() sobre un escalar

    df["t"] = pd.to_numeric(df["t"], errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["t", "qty", "price"]).sort_values("t").reset_index(drop=True)

    position = 0.0
    layers: list[tuple[float, float, int, float]] = []
    rts: list[Roundtrip] = []

    for row in df.itertuples(index=False):
        side = str(row.side).upper()
        qty = float(row.qty)
        price = float(row.price)
        fee = float(row.fee)
        t = int(row.t)

        signed_qty = qty if side.startswith("B") else -qty

        if abs(position) < 1e-12:
            layers.append((signed_qty, price, t, fee))
            position += signed_qty
            continue

        if np.sign(position) == np.sign(signed_qty):
            layers.append((signed_qty, price, t, fee))
            position += signed_qty
            continue

        remaining = abs(signed_qty)
        close_side = np.sign(signed_qty)
        gross_pnl = 0.0
        fees_sum = fee
        open_t = None
        open_price_weighted = 0.0
        closed_qty_total = 0.0

        while remaining > 1e-12 and layers:
            q0, p0, t0, f0 = layers[0]
            can_close = min(abs(q0), remaining)
            pnl_leg = can_close * (price - p0) * np.sign(q0)
            gross_pnl += pnl_leg
            fees_sum += f0
            open_t = t0 if open_t is None else open_t
            open_price_weighted += p0 * can_close
            closed_qty_total += can_close

            q0_new = np.sign(q0) * (abs(q0) - can_close)
            remaining -= can_close
            if abs(q0_new) < 1e-12:
                layers.pop(0)
            else:
                layers[0] = (q0_new, p0, t0, f0)

        position += signed_qty

        if np.sign(position) == 0 or np.sign(position) == close_side:
            if closed_qty_total > 0:
                open_price = open_price_weighted / closed_qty_total
                rts.append(
                    Roundtrip(
                        open_t=open_t if open_t is not None else t,
                        close_t=t,
                        qty=closed_qty_total,
                        pnl=gross_pnl - fees_sum,
                        gross_pnl=gross_pnl,
                        fees=fees_sum,
                        open_price=open_price,
                        close_price=price,
                    )
                )

    return rts


def _latency_metrics(df_trades: pd.DataFrame) -> dict[str, float]:
    cand_cols = [
        c for c in df_trades.columns if "latency" in c.lower() and c.lower().endswith("_ms")
    ]
    out: dict[str, float] = {}
    for c in cand_cols:
        s = pd.to_numeric(df_trades[c], errors="coerce").dropna()
        if len(s) == 0:
            continue
        out[f"{c}_mean"] = float(s.mean())
        out[f"{c}_p50"] = float(s.quantile(0.50))
        out[f"{c}_p95"] = float(s.quantile(0.95))
        out[f"{c}_p99"] = float(s.quantile(0.99))
        out[f"{c}_max"] = float(s.max())
    return out


def metrics_for_run(run_dir: str | Path) -> dict[str, float]:
    run_dir = Path(run_dir)
    eq_df = _ensure_equity(run_dir, _read_csv_safe(run_dir / "equity.csv"))

    # Recortar equity al último cierre de estrategia (último SELL) para evitar
    # que el retorno use el final del dataset cuando ya no hay posición.
    last_close_t = _last_close_time_from_trades(run_dir)
    if last_close_t is not None:
        trimmed = eq_df[eq_df["t"] <= last_close_t]
        if len(trimmed) >= 2:
            eq_df = trimmed.reset_index(drop=True)

    t0, t1 = int(eq_df["t"].iloc[0]), int(eq_df["t"].iloc[-1])
    e0, e1 = float(eq_df["equity"].iloc[0]), float(eq_df["equity"].iloc[-1])
    total_return = e1 / e0 - 1.0

    # Retornos log paso a paso
    log_ret = np.log(eq_df["equity"]).diff().dropna()
    vol_ann = _annualize_vol_from_irregular(log_ret, eq_df["t"].iloc[1:])

    # Duración y años
    dt_seconds = max((t1 - t0) / 1000.0, 1e-9)
    years = dt_seconds / SECONDS_PER_YEAR
    enough_time = years >= (MIN_ANNUALIZATION_DAYS / 365.0)
    enough_steps = len(log_ret) >= MIN_RISK_STEPS

    # Ratios condicionados por umbrales
    cagr = _cagr(e0, e1, t0, t1) if enough_time else float("nan")
    sharpe = (
        float((cagr) / vol_ann)
        if enough_time and np.isfinite(vol_ann) and vol_ann > 0
        else float("nan")
    )
    sortino = (
        _sortino(log_ret, eq_df["t"].iloc[1:]) if enough_time and enough_steps else float("nan")
    )

    # Drawdown
    mdd, dd_dur = _max_drawdown(eq_df["equity"])
    calmar = (
        float(cagr / abs(mdd)) if enough_time and mdd < 0 and np.isfinite(cagr) else float("nan")
    )

    # Trades (opcional)
    trades_path = run_dir / "trades.csv"
    n_trades = 0
    win_rate = float("nan")
    avg_trade_qty = float("nan")
    notional = 0.0
    fees_total = 0.0
    fee_bps = float("nan")
    slippage_bps = float("nan")
    latency: dict[str, float] = {}

    if trades_path.exists():
        td = _read_csv_safe(trades_path)
        cols = {c.lower(): c for c in td.columns}

        if {"qty", "price"}.issubset(cols.keys()):
            qty_col, price_col = cols["qty"], cols["price"]
            q_series = pd.to_numeric(td[qty_col], errors="coerce").abs()
            p_series = pd.to_numeric(td[price_col], errors="coerce")
            td["__notional__"] = q_series * p_series
            notional = float(td["__notional__"].sum())
            n_trades = int(len(td))
            avg_trade_qty = (
                float(q_series.dropna().mean()) if len(q_series.dropna()) else float("nan")
            )

        if "fee" in cols:
            fees_total = float(pd.to_numeric(td[cols["fee"]], errors="coerce").fillna(0.0).sum())
            fee_bps = float(1e4 * fees_total / notional) if notional > 0 else float("nan")

        slip_cols = [c for c in td.columns if "slip" in c.lower() and c.lower().endswith("bps")]
        if slip_cols:
            s = pd.to_numeric(td[slip_cols[0]], errors="coerce").dropna()
            if len(s) > 0:
                slippage_bps = float(s.mean())

        rts = _form_roundtrips(td)
        if len(rts) > 0:
            wins = sum(1 for rt in rts if rt.pnl > 0)
            win_rate = 100.0 * wins / len(rts)

        latency = _latency_metrics(td)

    # Exposición si equity.csv tiene qty
    exposure = float("nan")
    try:
        eq2 = _read_csv_safe(run_dir / "equity.csv")
        lower_map = {c.lower(): c for c in eq2.columns}
        if "qty" in lower_map:
            q = pd.to_numeric(eq2[lower_map["qty"]], errors="coerce").fillna(0.0).abs()
            exposure = float((q > 1e-12).mean()) * 100.0
    except Exception:
        pass

    hours = dt_seconds / 3600.0

    out = {
        "run_dir": str(run_dir),
        "t0": int(t0),
        "t1": int(t1),
        "hours": float(hours),
        "equity_start": float(e0),
        "equity_end": float(e1),
        "ret_total": float(total_return),
        "cagr": float(cagr),
        "vol_ann": float(vol_ann),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_dd": float(mdd),
        "dd_duration_steps": int(dd_dur),
        "calmar": float(calmar),
        "n_trades": int(n_trades),
        "win_rate_pct": float(win_rate),
        "avg_trade_qty": float(avg_trade_qty),
        "notional": float(notional),
        "fees_total": float(fees_total),
        "fee_bps": float(fee_bps),
        "slippage_bps": float(slippage_bps),
        "exposure_pct": float(exposure),
    }
    out.update(latency)
    return out


def compare_runs(
    run_dirs: Iterable[str | Path], sort_by: str = "cagr", descending: bool = True
) -> pd.DataFrame:
    rows = []
    for rd in run_dirs:
        try:
            rows.append(metrics_for_run(rd))
        except Exception as e:
            rows.append({"run_dir": str(rd), "error": str(e)})
    df = pd.DataFrame(rows)

    if "error" in df.columns:
        df["_ok_"] = df["error"].isna()
        if sort_by in df.columns:
            df = df.sort_values(["_ok_", sort_by], ascending=[False, not descending])
        else:
            df = df.sort_values(["_ok_"], ascending=[False])
        df = df.drop(columns=["_ok_"])
    else:
        if sort_by in df.columns:
            df = df.sort_values(sort_by, ascending=not descending)
    return df.reset_index(drop=True)


def _expand_glob(glob_pattern: str) -> list[str]:
    return [
        str(p) for p in sorted(Path(".").glob(glob_pattern)) if (Path(p) / "equity.csv").exists()
    ]


def _save_optional(df: pd.DataFrame, out: str | None, markdown: bool) -> None:
    if out:
        path = Path(out)
        if markdown:
            try:
                path.write_text(df.to_markdown(index=False))
            except Exception:
                df.to_csv(path, index=False)
        else:
            df.to_csv(path, index=False)


def main():
    p = argparse.ArgumentParser(description="Comparador de métricas para runs de backtest/stream.")
    p.add_argument(
        "--runs", nargs="*", default=[], help="Lista de run_dir (cada uno con equity.csv)."
    )
    p.add_argument("--glob", type=str, default=None, help='Patrón glob, p.ej. "runs/2025110*T*"')
    p.add_argument(
        "--sort-by", type=str, default="cagr", help="Columna para ordenar (por defecto: cagr)."
    )
    p.add_argument("--asc", action="store_true", help="Orden ascendente (por defecto descendente).")
    p.add_argument(
        "--out", type=str, default=None, help="Ruta de salida (CSV o Markdown si --markdown)."
    )
    p.add_argument(
        "--markdown",
        action="store_true",
        help="Si se especifica, guarda/print en formato Markdown.",
    )
    p.add_argument(
        "--print", dest="do_print", action="store_true", help="Imprime la tabla por stdout."
    )
    args = p.parse_args()

    run_list: list[str] = list(args.runs)
    if args.glob:
        run_list.extend(_expand_glob(args.glob))
    if not run_list:
        raise SystemExit("Debes proporcionar --runs ... o --glob ...")

    df = compare_runs(run_list, sort_by=args.sort_by, descending=not args.asc)

    if args.do_print or not args.out:
        if args.markdown:
            try:
                print(df.to_markdown(index=False))
            except Exception:
                print(df.to_string(index=False))
        else:
            print(df.to_string(index=False))

    _save_optional(df, args.out, args.markdown)


if __name__ == "__main__":
    main()

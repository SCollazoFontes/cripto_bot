# src/core/engine.py
"""
Engine "live-like" con soporte de:
- Barras externas (external_bars) -> consumo determinista (offline o micro-velas ya fabricadas)
- Generación de micro-velas en vivo a partir de un price_provider (bid/ask/ts)
- Reporting de equity (equity.csv) opcional y determinista

Interfaz principal:
    run_engine_live_like(
        strategy,
        symbol,
        broker,
        executor=None,
        *,
        # Opción A: pasar barras ya construidas
        external_bars: list[dict] | None = None,
        # Opción B: crear micro-velas en vivo a partir de un price_provider
        price_provider=None,      # callable (symbol) -> (bid, ask, ts)
        interval_sec: float = 1.0,
        ticks: int | None = None, # nº de barras a emitir en modo live
        report_dir: str | None = None,
        write_reports: bool = True,
    ) -> dict

Notas:
- Las barras externas deben ser dicts con llaves: ts, open, high, low, close (floats).
- El equity se marca a `close` de cada barra.
- No asumo estructura del executor: solo se lo pasamos a strategy.on_bar(...).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import csv
import os
import time
from typing import Any

# --------------------------------- Helpers ---------------------------------


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _write_equity_csv(path: str, rows: list[dict[str, Any]]) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["ts", "symbol", "close", "pos_qty", "cash_usdt", "equity_usdt"],
        )
        w.writeheader()
        w.writerows(rows)


def _mark_to_equity_row(broker, symbol: str, ts: float, close: float) -> dict[str, Any]:
    pos = broker.get_position(symbol)
    cash = broker.get_account()["balances"].get("USDT", {}).get("free", 0.0)
    return {
        "ts": float(ts),
        "symbol": symbol,
        "close": float(close),
        "pos_qty": float(pos),
        "cash_usdt": float(cash),
        "equity_usdt": float(cash + pos * close),
    }


# --------------------------------- Engine ----------------------------------


def _call_on_bar(strategy, broker, executor, symbol: str, bar: dict[str, Any]) -> None:
    """
    Llama a Strategy.on_bar, que internamente decide on_bar_live/on_bar_backtest.
    Se mantiene este wrapper por compatibilidad con tu código previo.
    """
    strategy.on_bar(broker, executor, symbol, bar)


def _iter_external_bars(bars: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """
    Normaliza/valida mínimamente las barras externas.
    """
    for b in bars:
        for k in ("ts", "open", "high", "low", "close"):
            if k not in b:
                raise ValueError(f"Bar externa sin clave '{k}': {b}")
        yield {
            "ts": float(b["ts"]),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
        }


def _iter_live_microbars(
    symbol: str,
    price_provider: Callable[[str], tuple[float, float, float]],
    interval_sec: float,
    ticks: int | None,
) -> Iterable[dict[str, Any]]:
    """
    Fabrica micro-velas en vivo agrupando mids por intervalos de `interval_sec`.
    - price_provider(symbol) -> (bid, ask, ts)
    - Emite una barra por "bucket" de tiempo.
    """
    if interval_sec <= 0:
        interval_sec = 1.0

    emitted = 0
    win_prices: list[float] = []
    last_bucket: int | None = None

    while True:
        bid, ask, ts = price_provider(symbol)
        mid = (bid + ask) / 2.0
        bucket = int(ts // interval_sec)

        if last_bucket is None:
            last_bucket = bucket

        if bucket == last_bucket:
            win_prices.append(mid)
        else:
            if win_prices:
                bar = {
                    "ts": float(last_bucket * interval_sec),
                    "open": float(win_prices[0]),
                    "high": float(max(win_prices)),
                    "low": float(min(win_prices)),
                    "close": float(win_prices[-1]),
                }
                yield bar
                emitted += 1
                if ticks is not None and emitted >= ticks:
                    return
            win_prices = [mid]
            last_bucket = bucket

        # pausamos ligeramente para no saturar la API
        time.sleep(0.05)


def run_engine_live_like(
    strategy,
    symbol: str,
    broker,
    executor=None,
    *,
    external_bars: Iterable[dict[str, Any]] | None = None,
    price_provider: Callable[[str], tuple[float, float, float]] | None = None,
    interval_sec: float = 1.0,
    ticks: int | None = None,
    report_dir: str | None = None,
    write_reports: bool = True,
) -> dict[str, Any]:
    """
    Ejecuta la estrategia contra:
      - A) barras externas (external_bars), o
      - B) micro-velas en vivo vía price_provider y interval_sec.

    Devuelve un dict con metadatos y, si procede, la ruta de equity.csv
    """
    if external_bars is None and price_provider is None:
        raise ValueError("Debes pasar external_bars o price_provider.")

    # Selección del iterador de barras
    if external_bars is not None:
        bar_iter = _iter_external_bars(external_bars)
    else:
        # modo en vivo
        assert price_provider is not None
        bar_iter = _iter_live_microbars(symbol, price_provider, interval_sec, ticks)

    equity_rows: list[dict[str, Any]] = []
    n = 0

    for bar in bar_iter:
        _call_on_bar(strategy, broker, executor, symbol, bar)
        # mark-to-close para equity
        equity_rows.append(_mark_to_equity_row(broker, symbol, ts=bar["ts"], close=bar["close"]))
        # refresco broker por si hay LIMIT GTC abiertos; inofensivo si no los hay
        if hasattr(broker, "refresh"):
            try:
                broker.refresh()
            except Exception:
                pass
        n += 1

    # Reporting
    equity_path = None
    if write_reports and report_dir:
        equity_path = os.path.join(report_dir, "equity.csv")
        _write_equity_csv(equity_path, equity_rows)

    return {
        "n_bars": n,
        "symbol": symbol,
        "equity_csv": equity_path,
    }

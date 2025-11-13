"""Output file writers for live trading runs."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import json
import pathlib


def write_decisions_csv(run_dir: pathlib.Path, decisions: list[dict]) -> None:
    """Guarda decisiones de estrategia."""
    if not decisions:
        return
    with (run_dir / "decisions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=decisions[0].keys())
        writer.writeheader()
        writer.writerows(decisions)


def write_summary(run_dir: pathlib.Path, stats: dict) -> None:
    """Guarda resumen de ejecución."""
    with (run_dir / "summary.json").open("w") as f:
        json.dump(stats, f, indent=2)


def write_returns_csv(run_dir: pathlib.Path, equity_rows: list[tuple]) -> None:
    """Escribe returns.csv con retornos por barra.

    equity_rows formato: (timestamp, symbol, price, qty, cash, equity)
    """
    if not equity_rows:
        return

    with (run_dir / "returns.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "equity", "return_pct", "cumulative_return_pct"])

        cumulative_return = 0.0
        for i, row in enumerate(equity_rows):
            equity = row[5]  # equity está en índice 5
            if i == 0:
                return_pct = 0.0
            else:
                prev_equity = equity_rows[i - 1][5]
                return_pct = (
                    ((equity - prev_equity) / prev_equity * 100) if prev_equity > 0 else 0.0
                )
                cumulative_return += return_pct

            writer.writerow(
                [
                    row[0],  # timestamp
                    f"{equity:.2f}",
                    f"{return_pct:.4f}",
                    f"{cumulative_return:.4f}",
                ]
            )


def write_equity_csv(run_dir: pathlib.Path, equity_rows: list[tuple]) -> None:
    """Guarda equity.csv a partir de filas (ts, symbol, price, qty, cash, equity)."""
    eq_path = run_dir / "equity.csv"
    with eq_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "symbol", "price", "qty", "cash", "equity"])
        for row in equity_rows:
            # row esperado: (bar_ts, symbol, bar_price, pos_qty, cash_now, equity_now)
            w.writerow(row)


def write_trades_csv(run_dir: pathlib.Path, trade_rows: list[dict]) -> None:
    """Guarda trades.csv. Crea el archivo aunque no haya operaciones."""
    tr_path = run_dir / "trades.csv"
    fields = ["timestamp", "side", "price", "qty", "cash", "equity", "reason"]
    with tr_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in trade_rows or []:
            out = {k: row.get(k) for k in fields}
            w.writerow(out)


def save_manifest(run_dir: pathlib.Path, args: argparse.Namespace, started_ts: float) -> None:
    """Guarda manifest.json con la configuración de la ejecución en vivo."""
    try:
        params = json.loads(args.params) if getattr(args, "params", None) else {}
    except Exception:
        params = {}
    manifest = {
        "run_id": datetime.fromtimestamp(started_ts, tz=UTC).strftime("%Y%m%dT%H%M%SZ"),
        "started_ts": started_ts,
        "symbol": getattr(args, "symbol", None),
        "testnet": bool(getattr(args, "testnet", False)),
        "duration_s": getattr(args, "duration", None),
        "cash": getattr(args, "cash", None),
        "fees_bps": getattr(args, "fees_bps", None),
        "slip_bps": getattr(args, "slip_bps", None),
        "strategy": getattr(args, "strategy", None),
        "params": params,
        # Opciones del panel (si existen en args)
        "dashboard": getattr(args, "dashboard", None),
        "panel": getattr(args, "panel", None),
        "dashboard_port": getattr(args, "dashboard_port", None),
        "script": "tools.live.run_binance",
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

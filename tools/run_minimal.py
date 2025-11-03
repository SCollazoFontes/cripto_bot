# tools/run_minimal.py

"""
Runner mínimo (orquestador) para procesar un run reproducible (runs/<ts>/).
- Lee runs/<ts>/data.csv
- Emite barras vía CSVFeed (validación incluida)
- Carga Strategy (Buy & Hold) y consume sus señales
- Integra SimBroker (submit_market) según contrato real; si no, cae a modo manual
- Escribe runs/<ts>/equity.csv y runs/<ts>/trades.csv
- Valida resultados básicos (filas y NaN)

Uso:
    python -m tools.run_minimal --run-dir runs/<ts> --ts-field t_close --symbol BTCUSDT --exit-at-end 1
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger
import pandas as pd

from src.core.strategy import Strategy

# Proyecto
from src.data.feeds import CSVFeed
from src.strategies.buy_and_hold import BuyAndHoldStrategy

# ---- Broker (contrato real) ----
_HAS_BROKER = False
OrderRequest = None
Side = None
SimBroker = None
try:
    from src.core.broker import OrderRequest as _OrderRequest, Side as _Side  # type: ignore
    from src.core.broker_sim import SimBroker as _SimBroker  # type: ignore

    OrderRequest = _OrderRequest
    Side = _Side
    SimBroker = _SimBroker
    _HAS_BROKER = True
except Exception:
    _HAS_BROKER = False


# ------------------------ utilidades ------------------------ #
def _get(obj: Any, name: str, default: Optional[Any] = None) -> Any:
    if hasattr(obj, name):
        return getattr(obj, name)
    try:
        return obj[name]  # type: ignore[index]
    except Exception:
        return default


def _pick_price(bar, mode: str) -> float:
    if mode == "close":
        return float(_get(bar, "close"))
    if mode == "open":
        return float(_get(bar, "open"))
    if mode == "mid":
        o = float(_get(bar, "open"))
        c = float(_get(bar, "close"))
        return (o + c) / 2.0
    raise ValueError(f"Modo de precio no soportado: {mode}")


def _to_iso(ts_val: Any) -> str:
    if ts_val is None:
        return ""
    if isinstance(ts_val, (pd.Timestamp,)):
        return pd.to_datetime(ts_val, utc=True).isoformat()
    try:
        v = float(ts_val)
        if v > 1e12:
            return pd.to_datetime(v, unit="ns", utc=True).isoformat()
        if v > 1e10:
            return pd.to_datetime(v, unit="us", utc=True).isoformat()
        if v > 1e9:
            return pd.to_datetime(v, unit="ms", utc=True).isoformat()
        if v > 1e8:
            return pd.to_datetime(v, unit="s", utc=True).isoformat()
    except Exception:
        pass
    return str(ts_val)


def _iso_or_index_timestamp(bar, fallback_idx: int, preferred: Optional[str]) -> str:
    if preferred:
        val = _get(bar, preferred, None)
        if val is not None:
            return _to_iso(val)
    for c in ("t_close", "t_close_ms", "t_open", "t", "ts", "timestamp", "time"):
        val = _get(bar, c, None)
        if val is not None:
            return _to_iso(val)
    return str(fallback_idx)


def _ts_ms_from_str(t_str: str) -> Optional[int]:
    if not t_str:
        return None
    try:
        v = float(t_str)
        if v > 1e12:  # ns
            return int(v / 1e6)
        if v > 1e10:  # us
            return int(v / 1e3)
        if v > 1e8:  # s
            return int(v * 1e3)
        return int(v)  # ya ms
    except Exception:
        pass
    try:
        return int(pd.to_datetime(t_str, utc=True).timestamp() * 1000.0)
    except Exception:
        return None


def _make_csv_feed(data_csv: Path) -> CSVFeed:
    try:
        return CSVFeed(str(data_csv))  # posicional
    except TypeError:
        pass
    for kw in ("filepath", "file", "filename", "csv_path", "path"):
        try:
            return CSVFeed(**{kw: str(data_csv)})
        except TypeError:
            continue
    raise TypeError("No pude instanciar CSVFeed; ajusta _make_csv_feed() a la firma real.")


def _write_csv(path: Path, rows: Iterable[dict], field_order: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=field_order, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _detect_symbol(run_dir: Path, cli_symbol: Optional[str]) -> str:
    if cli_symbol:
        return cli_symbol
    manifest = run_dir / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            sym = data.get("symbol")
            if isinstance(sym, str) and sym:
                return sym
            src = data.get("source_path", "") or data.get("source", "")
            m = re.search(r"([a-z0-9]{3,10}usdt)", str(src), flags=re.I)
            if m:
                return m.group(1).upper()
        except Exception:
            pass
    data_csv = run_dir / "data.csv"
    m = re.search(r"([a-z0-9]{3,10}usdt)", str(data_csv), flags=re.I)
    if m:
        return m.group(1).upper()
    return "BTCUSDT"


# ------------------------ runner ------------------------ #
def run_with_strategy(
    run_dir: Path,
    strategy: Strategy,
    cash_start: float,
    fees_bps: float,
    slip_bps: float,
    price_mode: str,
    ts_field: Optional[str],
    symbol: str,
    exit_at_end: bool,
) -> Tuple[Path, Path]:
    data_csv = run_dir / "data.csv"
    if not data_csv.exists():
        raise FileNotFoundError(f"No existe {data_csv}")

    feed = _make_csv_feed(data_csv)

    # Broker si está disponible, si no -> estado manual
    broker = None
    if _HAS_BROKER:
        try:
            broker = SimBroker(
                starting_cash=float(cash_start),
                fees_bps=float(fees_bps),
                slip_bps=float(slip_bps),
                allow_short=False,
            )
            logger.info("Usando SimBroker (cash inicial: {:.2f})", float(cash_start))
        except Exception as e:
            logger.info("ℹ️ No se pudo instanciar SimBroker ({}); usaré ejecución manual.", e)

    # Estado manual (fallback)
    cash = float(cash_start)
    qty = 0.0

    equity_rows: List[Dict[str, str]] = []
    trade_rows: List[Dict[str, str]] = []

    fee_mult = 1.0 + (fees_bps / 10_000.0)
    slip_mult = 1.0 + (slip_bps / 10_000.0)

    strategy.on_start(broker=broker)

    n_bars = 0
    first_t = None
    last_t = None
    last_px_report = None  # para salida al final
    did_buy = False

    for idx, bar in enumerate(feed, start=1):
        n_bars += 1
        t_str = _iso_or_index_timestamp(bar, idx, ts_field)
        if first_t is None:
            first_t = t_str
        last_t = t_str

        strategy.on_bar(bar, broker=broker)

        for sig in getattr(strategy, "drain_signals")():
            if sig.get("type") == "BUY_ALL" and not did_buy:
                pmode = sig.get("price_mode", price_mode)
                px_ref = _pick_price(bar, pmode)

                if broker is not None and OrderRequest is not None and Side is not None:
                    qty_est = broker.cash() / (
                        px_ref * (1.0 + fees_bps / 10_000.0) * (1.0 + slip_bps / 10_000.0)
                    )
                    qty_est = float(qty_est) if qty_est > 0 else 0.0
                    order = OrderRequest(
                        symbol=symbol,
                        side=Side.BUY,
                        qty=qty_est,
                        price_ref=px_ref,
                        ts_ms=_ts_ms_from_str(t_str),
                    )
                    try:
                        rep = broker.submit_market(order)
                        qty = float(rep.qty)
                        exec_price = float(rep.exec_price)
                        notional = float(rep.notional)
                        fees = float(rep.fee)
                        trade_rows.append(
                            {
                                "trade_id": int(rep.order_id),
                                "t": t_str,
                                "side": "BUY",
                                "qty": f"{qty:.12f}",
                                "price": f"{exec_price:.8f}",
                                "notional": f"{notional:.8f}",
                                "fees": f"{fees:.8f}",
                                "fees_bps": f"{fees_bps:.4f}",
                                "slip_bps": f"{slip_bps:.4f}",
                                "run_dir": str(run_dir),
                                "symbol": symbol,
                            }
                        )
                        did_buy = True
                    except Exception as e:
                        logger.info("ℹ️ submit_market falló ({}); caigo a ejecución manual.", e)
                        exec_price = px_ref * slip_mult
                        qty = cash / (exec_price * fee_mult)
                        notional = exec_price * qty
                        fees = notional * (fees_bps / 10_000.0)
                        cash = cash - notional - fees
                        trade_rows.append(
                            {
                                "trade_id": 1,
                                "t": t_str,
                                "side": "BUY",
                                "qty": f"{qty:.12f}",
                                "price": f"{exec_price:.8f}",
                                "notional": f"{notional:.8f}",
                                "fees": f"{fees:.8f}",
                                "fees_bps": f"{fees_bps:.4f}",
                                "slip_bps": f"{slip_bps:.4f}",
                                "run_dir": str(run_dir),
                                "symbol": symbol,
                            }
                        )
                        did_buy = True
                else:
                    exec_price = px_ref * slip_mult
                    if exec_price <= 0:
                        raise ValueError("Precio de ejecución no positivo.")
                    qty = cash / (exec_price * fee_mult)
                    notional = exec_price * qty
                    fees = notional * (fees_bps / 10_000.0)
                    cash = cash - notional - fees
                    trade_rows.append(
                        {
                            "trade_id": 1,
                            "t": t_str,
                            "side": "BUY",
                            "qty": f"{qty:.12f}",
                            "price": f"{exec_price:.8f}",
                            "notional": f"{notional:.8f}",
                            "fees": f"{fees:.8f}",
                            "fees_bps": f"{fees_bps:.4f}",
                            "slip_bps": f"{slip_bps:.4f}",
                            "run_dir": str(run_dir),
                            "symbol": symbol,
                        }
                    )
                    did_buy = True

        # Mark-to-market (reporting)
        px_report = _pick_price(bar, price_mode)
        last_px_report = px_report  # guardamos por si cerramos al final
        if broker is not None:
            equity_val = float(broker.equity(marks={symbol: px_report}))
            cash_val = float(broker.cash())
            pos = broker.positions().get(symbol)
            qty_pos = float(pos.qty) if pos is not None else 0.0
            equity_rows.append(
                {
                    "t": t_str,
                    "price": f"{px_report:.8f}",
                    "qty": f"{qty_pos:.12f}",
                    "cash": f"{cash_val:.8f}",
                    "equity": f"{equity_val:.8f}",
                }
            )
        else:
            equity = cash + qty * px_report
            equity_rows.append(
                {
                    "t": t_str,
                    "price": f"{px_report:.8f}",
                    "qty": f"{qty:.12f}",
                    "cash": f"{cash:.8f}",
                    "equity": f"{equity:.8f}",
                }
            )

    # ---- Cierre opcional al final ----
    if exit_at_end and did_buy and last_t is not None and last_px_report is not None:
        if broker is not None and OrderRequest is not None and Side is not None:
            pos = broker.positions().get(symbol)
            qty_to_sell = float(pos.qty) if pos is not None else 0.0
            if qty_to_sell > 0:
                order = OrderRequest(
                    symbol=symbol,
                    side=Side.SELL,
                    qty=qty_to_sell,
                    price_ref=float(last_px_report),
                    ts_ms=_ts_ms_from_str(last_t),
                )
                try:
                    rep = broker.submit_market(order)
                    trade_rows.append(
                        {
                            "trade_id": int(rep.order_id),
                            "t": last_t,
                            "side": "SELL",
                            "qty": f"{float(rep.qty):.12f}",
                            "price": f"{float(rep.exec_price):.8f}",
                            "notional": f"{float(rep.notional):.8f}",
                            "fees": f"{float(rep.fee):.8f}",
                            "fees_bps": f"{fees_bps:.4f}",
                            "slip_bps": f"{slip_bps:.4f}",
                            "run_dir": str(run_dir),
                            "symbol": symbol,
                        }
                    )
                except Exception as e:
                    logger.info("ℹ️ SELL final falló ({}); dejo posición abierta.", e)
        else:
            # Manual: cerramos al precio reportado
            exec_price = float(last_px_report) * (
                1.0 - slip_bps / 10_000.0
            )  # sesgo conservador en salida
            notional = exec_price * qty
            fees = notional * (fees_bps / 10_000.0)
            cash = cash + notional - fees
            trade_rows.append(
                {
                    "trade_id": (trade_rows[-1]["trade_id"] if trade_rows else 1),
                    "t": last_t,
                    "side": "SELL",
                    "qty": f"{qty:.12f}",
                    "price": f"{exec_price:.8f}",
                    "notional": f"{notional:.8f}",
                    "fees": f"{fees:.8f}",
                    "fees_bps": f"{fees_bps:.4f}",
                    "slip_bps": f"{slip_bps:.4f}",
                    "run_dir": str(run_dir),
                    "symbol": symbol,
                }
            )
            qty = 0.0
            # Actualizamos última equity con cash post-cierre
            equity_rows[-1]["qty"] = f"{qty:.12f}"
            equity_rows[-1]["cash"] = f"{cash:.8f}"
            equity_rows[-1]["equity"] = f"{cash:.8f}"

    strategy.on_end(broker=broker)

    if not equity_rows:
        raise RuntimeError("El feed no emitió ninguna barra; revisa data.csv")

    equity_csv = run_dir / "equity.csv"
    trades_csv = run_dir / "trades.csv"

    _write_csv(equity_csv, equity_rows, field_order=["t", "price", "qty", "cash", "equity"])
    _write_csv(
        trades_csv,
        trade_rows,
        field_order=[
            "trade_id",
            "t",
            "side",
            "qty",
            "price",
            "notional",
            "fees",
            "fees_bps",
            "slip_bps",
            "run_dir",
            "symbol",
        ],
    )

    # Validación post-run
    df_eq = pd.read_csv(equity_csv)
    if len(df_eq) != n_bars:
        raise AssertionError(f"equity.csv tiene {len(df_eq)} filas y el feed emitió {n_bars}.")
    if df_eq[["t", "price", "qty", "cash", "equity"]].isna().any().any():
        raise AssertionError("equity.csv contiene valores NaN.")

    logger.info(
        "✅ equity.csv escrito: {} (filas={}, t_first={}, t_last={})",
        equity_csv,
        n_bars,
        first_t,
        last_t,
    )
    logger.info("✅ trades.csv escrito: {} (n_trades={})", trades_csv, len(trade_rows))
    return equity_csv, trades_csv


# ------------------------ CLI ------------------------ #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Runner mínimo sobre un run existente.")
    p.add_argument("--run-dir", required=True, help="Carpeta del run (ej. runs/<ts>).")
    p.add_argument("--cash", type=float, default=10_000.0, help="Efectivo inicial.")
    p.add_argument("--fees-bps", type=float, default=2.5, help="Comisiones en bps.")
    p.add_argument("--slip-bps", type=float, default=1.0, help="Slippage en bps.")
    p.add_argument(
        "--price",
        choices=["close", "open", "mid"],
        default="close",
        help="Precio de valoración/reporting.",
    )
    p.add_argument(
        "--ts-field",
        default=None,
        help="Nombre del campo timestamp (ej. t_close, t_open, ts, time).",
    )
    p.add_argument(
        "--symbol",
        default=None,
        help="Símbolo del activo (ej. BTCUSDT). Si no se pasa, se intenta detectar.",
    )
    p.add_argument(
        "--exit-at-end",
        type=int,
        default=0,
        help="Si 1, cierra la posición al final con un SELL MARKET.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta del run: {run_dir}")

    symbol = _detect_symbol(run_dir, args.symbol)
    strategy = BuyAndHoldStrategy(price_mode=args.price)

    logger.info(
        "Runner mínimo → run_dir={}, cash={:.2f}, fees_bps={:.4f}, slip_bps={:.4f}, price={}, ts_field={}, symbol={}",
        run_dir,
        args.cash,
        args.fees_bps,
        args.slip_bps,
        args.price,
        args.ts_field,
        symbol,
    )
    run_with_strategy(
        run_dir=run_dir,
        strategy=strategy,
        cash_start=args.cash,
        fees_bps=args.fees_bps,
        slip_bps=args.slip_bps,
        price_mode=args.price,
        ts_field=args.ts_field,
        symbol=symbol,
        exit_at_end=bool(args.exit_at_end),
    )


if __name__ == "__main__":
    main()

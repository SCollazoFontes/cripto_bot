"""
Script de prueba en vivo para el pipeline completo de micro-velas.

Ejecución
---------
python -m tools.run_stream --symbol btcusdt --rule tick --limit 50
python -m tools.run_stream --symbol btcusdt --rule volume_qty --limit 0.5
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import signal
import time
from typing import Any, Dict

from bars.registry import create_builder
from exchange.binance_stream import iter_trades

__all__ = ["run"]


def _builder_kwargs(rule: str, limit: float) -> Dict[str, Any]:
    """
    Mapea --limit al parámetro correcto del builder.
    - tick, tick_count          -> tick_limit
    - volume, volume_qty        -> qty_limit
    - dollar, value             -> value_limit
    - imbalance, imbalance_qty  -> imbal_limit (modo qty)
    - imbalance_tick            -> imbal_limit (modo tick)
    """
    key = rule.strip().lower()
    if key in {"tick", "tick_count"}:
        return {"tick_limit": int(limit)}
    if key in {"volume", "volume_qty"}:
        return {"qty_limit": float(limit)}
    if key in {"dollar", "value"}:
        return {"value_limit": float(limit)}
    if key in {"imbalance", "imbalance_qty"}:
        return {"imbal_limit": float(limit), "mode": "qty"}
    if key in {"imbalance_tick"}:
        return {"imbal_limit": float(limit), "mode": "tick"}
    return {"tick_limit": int(limit)}


async def run(
    symbol: str,
    rule: str,
    limit: float,
    testnet: bool,
    max_bars: int | None,
    max_seconds: float | None,
) -> None:
    """
    Conecta al stream de Binance y muestra microvelas cerradas en tiempo real.
    """
    print(
        f"\n▶ Iniciando {symbol.upper()} | Regla: {rule} | Límite: {limit} | "
        f"testnet={testnet} | max_bars={max_bars} | max_seconds={max_seconds}\n"
    )

    kwargs = _builder_kwargs(rule, limit)
    builder = create_builder(rule, **kwargs)

    stop_event = asyncio.Event()

    def _set_stop_event(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _set_stop_event)
        loop.add_signal_handler(signal.SIGTERM, _set_stop_event)
    except NotImplementedError:
        pass

    bars_emitted = 0
    t0 = time.monotonic()

    try:
        async for trade in iter_trades(symbol, testnet=testnet):
            if stop_event.is_set():
                break

            if max_seconds is not None and (time.monotonic() - t0) >= max_seconds:
                print("⏱️  Límite de tiempo alcanzado. Cerrando…")
                break

            bar = builder.update(trade)
            if bar:
                bars_emitted += 1
                now = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{now}] BAR → open={bar.open:.2f}, close={bar.close:.2f}, "
                    f"high={bar.high:.2f}, low={bar.low:.2f}, "
                    f"vol={bar.volume:.6f}, n={bar.trade_count}"
                )

                if max_bars is not None and bars_emitted >= max_bars:
                    print(f"✅ Límite de barras alcanzado ({bars_emitted}). Cerrando…")
                    break

    except KeyboardInterrupt:
        print("\n🛑 Interrumpido por el usuario (CTRL+C). Cerrando…")

    print("👋 Stream finalizado.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ejecuta el stream de micro-velas.")
    parser.add_argument(
        "--symbol",
        type=str,
        required=True,
        help="Símbolo spot de Binance (p.ej. btcusdt)",
    )
    parser.add_argument(
        "--rule",
        type=str,
        default="tick",
        help="Regla (tick, tick_count, volume, volume_qty, ...)",
    )
    parser.add_argument(
        "--limit",
        type=float,
        default=100,
        help="Parámetro principal (trades o volumen por barra).",
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Usar Binance Testnet",
    )
    parser.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Corta tras emitir N barras",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="Corta tras T segundos",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        run(
            symbol=args.symbol,
            rule=args.rule,
            limit=args.limit,
            testnet=args.testnet,
            max_bars=args.max_bars,
            max_seconds=args.max_seconds,
        )
    )

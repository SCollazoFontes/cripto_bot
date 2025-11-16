"""
run_stream.py — Ingesta de trades vía WS y escritura de micro-barras a CSV en tiempo real.

Uso:
    # Ejecutar con PYTHONPATH apuntando a la carpeta `src` para resolver imports
    # EJEMPLO:
    #   PYTHONPATH=$(pwd)/src python -m tools.run_stream --symbol BTCUSDT --builder volume_qty \
    #         --qty-limit 0.25 --out data/bars_live/out.csv --max-trades 10000 --log-every 200

Soporta builders:
  - tick_count (--count)
  - volume_qty (--qty-limit)
  - dollar (--dollar-limit)
  - imbalance (--alpha)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from pathlib import Path
from typing import Any

from bars import builders
from bars.base import Trade
from data.feeds.binance_trades import iter_trades

logger = logging.getLogger(__name__)


# Cambia la firma así (añadimos -> Any):
def get_builder(name: str, params: dict[str, Any]) -> Any:
    """Devuelve instancia del builder correspondiente con sus parámetros."""
    name = name.lower()
    if name == "tick_count":
        return builders.TickCountBarBuilder(tick_limit=int(params.get("count", 100)))
    if name == "volume_qty":
        return builders.VolumeQtyBarBuilder(qty_limit=float(params.get("qty_limit", 1.0)))
    if name == "dollar":
        return builders.DollarBarBuilder(value_limit=float(params.get("dollar_limit", 1000.0)))
    if name == "imbalance":
        # Map "alpha" parameter to "imbal_limit" for ImbalanceBarBuilder
        return builders.ImbalanceBarBuilder(imbal_limit=float(params.get("alpha", 0.8)), mode="qty")
    raise ValueError(f"Builder '{name}' no reconocido")


async def stream_to_csv(
    symbol: str,
    builder_name: str,
    out_path: Path,
    max_trades: int | None = None,
    builder_params: dict[str, Any] | None = None,
    log_every: int = 1000,
) -> None:
    builder_params = builder_params or {}
    builder = get_builder(builder_name, builder_params)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    f_csv = out_path.open("a", newline="")
    writer = None
    trades_seen = 0
    bars_emitted = 0
    reconnects = 0

    while True:
        try:
            async for trade in iter_trades(symbol):
                trades_seen += 1
                bar = builder.update(Trade(**trade))
                if bar:
                    bars_emitted += 1
                    if writer is None:
                        writer = csv.DictWriter(f_csv, fieldnames=bar.__dict__.keys())
                        if out_path.stat().st_size == 0:
                            writer.writeheader()
                    writer.writerow(bar.__dict__)
                    f_csv.flush()

                if log_every and trades_seen % log_every == 0:
                    logger.info(
                        "Trades: %d | Barras: %d | Reconexiones: %d",
                        trades_seen,
                        bars_emitted,
                        reconnects,
                    )

                if max_trades and trades_seen >= max_trades:
                    f_csv.close()
                    return

            break  # si termina el stream, salimos del loop

        except Exception as e:
            reconnects += 1
            wait = min(60, 5 * reconnects)
            logger.warning(
                "Stream desconectado (%s). Reintentando en %ds (intento #%d)...",
                str(e),
                wait,
                reconnects,
            )
            await asyncio.sleep(wait)

    f_csv.close()
    logger.info(
        "Stream finalizado. Trades=%d, Barras=%d, Reconexiones=%d",
        trades_seen,
        bars_emitted,
        reconnects,
    )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="Símbolo de Binance (ej: BTCUSDT)")
    ap.add_argument(
        "--builder", required=True, choices=["tick_count", "volume_qty", "dollar", "imbalance"]
    )
    ap.add_argument("--count", type=int, help="N° de ticks por barra (tick_count)")
    ap.add_argument("--qty-limit", type=float, help="Volumen total por barra (volume_qty)")
    ap.add_argument("--dollar-limit", type=float, help="Valor $ por barra (dollar)")
    ap.add_argument("--alpha", type=float, help="Alpha para imbalance bars")
    ap.add_argument("--out", required=True, help="Ruta CSV de salida")
    ap.add_argument("--max-trades", type=int, help="Cortar tras N trades")
    ap.add_argument("--log-every", type=int, default=1000, help="Logs cada N trades")
    return ap.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    builder_params = {}
    if args.count:
        builder_params["count"] = args.count
    if args.qty_limit:
        builder_params["qty_limit"] = args.qty_limit
    if args.dollar_limit:
        builder_params["dollar_limit"] = args.dollar_limit
    if args.alpha:
        builder_params["alpha"] = args.alpha

    asyncio.run(
        stream_to_csv(
            symbol=args.symbol,
            builder_name=args.builder,
            out_path=Path(args.out),
            max_trades=args.max_trades,
            builder_params=builder_params,
            log_every=args.log_every,
        )
    )


if __name__ == "__main__":
    main()

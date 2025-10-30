"""
Script de prueba en vivo para el pipeline completo de micro-velas.

NOVEDADES:
- Parada grácil con CTRL+C (SIGINT) y SIGTERM.
- Opciones --max-bars y --max-seconds para cortar automáticamente.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import signal
import time

from bars.registry import create_builder
from exchange.binance_stream import iter_trades


async def run(
    symbol: str,
    rule: str,
    limit: int,
    testnet: bool,
    max_bars: int | None,
    max_seconds: float | None,
) -> None:
    """
    Conecta al stream de Binance y muestra microvelas cerradas en tiempo real.
    Permite cortar por número de barras o por tiempo transcurrido.
    """
    print(
        f"\n▶ Iniciando stream de {symbol.upper()}  |  Regla: {rule}  |  Límite: {limit} "
        f"| testnet={testnet} | max_bars={max_bars} | max_seconds={max_seconds}\n"
    )

    # Builder (por ahora solo tick_count usa tick_limit)
    builder = create_builder(rule, tick_limit=limit)

    # Señal de parada (para CTRL+C / SIGTERM) y topes
    stop_event = asyncio.Event()

    def _set_stop_event(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    # Handlers de señal
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

            # Salida por tiempo
            if max_seconds is not None and (time.monotonic() - t0) >= max_seconds:
                print("⏱️  Límite de tiempo alcanzado. Cerrando…")
                break

            bar = builder.update(trade)
            if bar:
                bars_emitted += 1
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"BAR → open={bar.open:.2f}, close={bar.close:.2f}, "
                    f"high={bar.high:.2f}, low={bar.low:.2f}, "
                    f"vol={bar.volume:.6f}, n={bar.trade_count}"
                )

                # Salida por número de barras
                if max_bars is not None and bars_emitted >= max_bars:
                    print(f"✅ Límite de barras alcanzado ({bars_emitted}). Cerrando…")
                    break

    except KeyboardInterrupt:
        # Soporte adicional por si no tenemos señales registrables en el loop
        print("\n🛑 Interrumpido por el usuario (CTRL+C). Cerrando…")

    print("👋 Stream finalizado.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ejecuta el stream de micro-velas.")
    parser.add_argument(
        "--symbol", type=str, required=True, help="Símbolo spot de Binance (p.ej. btcusdt)"
    )
    parser.add_argument(
        "--rule", type=str, default="tick", help="Regla de microvela (tick, tick_count, ...)"
    )
    parser.add_argument(
        "--limit", type=int, default=100, help="Parámetro de la regla (p.ej. trades por barra)"
    )

    parser.add_argument("--testnet", action="store_true", help="Usar Binance Testnet")
    parser.add_argument("--max-bars", type=int, default=None, help="Corta tras emitir N barras")
    parser.add_argument("--max-seconds", type=float, default=None, help="Corta tras T segundos")
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

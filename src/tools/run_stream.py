# tools/run_stream.py
"""
Script de prueba en vivo para el pipeline completo de micro-velas.
Permite validar el flujo en tiempo real con telemetría y persistencia opcional.

Ejemplos
--------
# Solo métricas (sin imprimir cada barra), 60 s
python -m tools.run_stream --symbol btcusdt --rule tick \
    --limit 50 --stats-every 2 --quiet --max-seconds 60
python -m tools.run_stream --symbol btcusdt --rule volume_qty \
    --limit 0.25 --stats-every 2 --quiet --max-seconds 60
python -m tools.run_stream --symbol btcusdt --rule dollar \
    --limit 10000 --stats-every 2 --quiet --max-seconds 60
python -m tools.run_stream --symbol btcusdt --rule imbalance \
    --limit 0.10 --stats-every 2 --quiet --max-seconds 60

# Desactivar persistencia (no guarda a disco)
python -m tools.run_stream --symbol btcusdt --rule tick \
    --limit 50 --no-persist
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import inspect
import logging
import signal
import time
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Registro de builders y fuente de datos (stream de Binance)
# ---------------------------------------------------------------------------
from bars.registry import create_builder
from exchange.binance_stream import iter_trades

# ---------------------------------------------------------------------------
# Métricas y logger del proyecto (con fallback si no existe src.*)
# ---------------------------------------------------------------------------

try:
    from core.metrics import BarsPerSecond, BlockTimer, LatencyStats
except Exception:
    from src.core.metrics import BarsPerSecond, BlockTimer, LatencyStats

try:
    from core.logger_config import init_logger as _init_logger
except Exception:
    try:
        from src.core.logger_config import init_logger as _init_logger
    except Exception:
        _init_logger = None

# ---------------------------------------------------------------------------
# Persistencia opcional (escritor asíncrono)
# ---------------------------------------------------------------------------

AsyncBarWriter: Optional[type] = None
try:
    from io.bar_writer import AsyncBarWriter as _AsyncBarWriter

    AsyncBarWriter = _AsyncBarWriter
except Exception:
    try:
        from src.io.bar_writer import AsyncBarWriter as _AsyncBarWriter

        AsyncBarWriter = _AsyncBarWriter
    except Exception:
        AsyncBarWriter = None


# ===========================================================================
# Utilidades auxiliares
# ===========================================================================


def _builder_kwargs(rule: str, limit: float) -> Dict[str, Any]:
    """Mapea --limit al parámetro correcto del builder según la regla."""
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


# ===========================================================================
# Loop principal asíncrono
# ===========================================================================


async def run(
    symbol: str,
    rule: str,
    limit: float,
    testnet: bool,
    max_bars: int | None,
    max_seconds: float | None,
    stats_every: float,
    quiet: bool,
    no_persist: bool,
    out_dir: str,
    fmt: str,
    flush_every_secs: float,
    flush_every_n: int,
    session: str | None,
) -> None:
    """
    Conecta al stream de Binance y genera micro-velas en tiempo real.
    - Mide latencias de update() y frecuencia de cierre de barras.
    - Permite persistir resultados a disco de forma asíncrona.
    """
    # Logger inicializado del proyecto
    if _init_logger:
        try:
            _init_logger()
        except Exception:
            pass

    logger = logging.getLogger("tools.run_stream")
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO)

    persist_str = (
        "NO-PERSIST"
        if no_persist or AsyncBarWriter is None
        else f"{fmt}@{out_dir} flush={flush_every_secs}s/{flush_every_n}"
    )

    print(
        f"\n▶ Iniciando {symbol.upper()} | Regla: {rule} | Límite: {limit} | "
        f"testnet={testnet} | max_bars={max_bars} | max_seconds={max_seconds} | "
        f"stats_every={stats_every}s | quiet={quiet} | persist={persist_str}\n"
    )

    kwargs = _builder_kwargs(rule, limit)
    builder = create_builder(rule, **kwargs)

    # ----------------------------------------------------------------------
    # Inicializa escritor asíncrono si existe
    # ----------------------------------------------------------------------
    writer = None
    if not no_persist:
        if AsyncBarWriter is None:
            logger.warning("Persistencia deshabilitada (no se encontró AsyncBarWriter).")
        else:
            try:
                ctor_sig = inspect.signature(AsyncBarWriter)
                supported = set(ctor_sig.parameters.keys())
                candidate_kwargs = {
                    "symbol": symbol,
                    "rule": rule,
                    "limit": limit,
                    "out_dir": out_dir,
                    "fmt": fmt,
                    "flush_every_secs": flush_every_secs,
                    "flush_every_n": flush_every_n,
                    "session": session,
                }
                filtered_kwargs = {k: v for k, v in candidate_kwargs.items() if k in supported}
                writer = AsyncBarWriter(**filtered_kwargs)
                if hasattr(writer, "start") and callable(writer.start):
                    writer.start()

                session_name = getattr(writer, "session_name", None) or filtered_kwargs.get(
                    "session", "auto"
                )
                logger.info(
                    "Persistiendo barras en %s (%s). Sesión=%s",
                    getattr(writer, "out_path", out_dir),
                    fmt,
                    session_name,
                )
            except Exception as ex:
                logger.exception("No se pudo inicializar AsyncBarWriter: %s", ex)
                writer = None

    # ----------------------------------------------------------------------
    # Control de señales (permite parar con CTRL+C)
    # ----------------------------------------------------------------------
    stop_event = asyncio.Event()

    def _set_stop_event(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _set_stop_event)
        loop.add_signal_handler(signal.SIGTERM, _set_stop_event)
    except NotImplementedError:
        pass

    # ----------------------------------------------------------------------
    # Inicializa telemetría
    # ----------------------------------------------------------------------
    lat = LatencyStats(maxlen=10000)
    bps = BarsPerSecond(window_s=10.0, ema_alpha=0.3)

    bars_emitted = 0
    t0 = time.monotonic()
    last_stats = t0

    # ----------------------------------------------------------------------
    # Bucle principal: recibe trades y actualiza builder
    # ----------------------------------------------------------------------
    try:
        async for trade in iter_trades(symbol, testnet=testnet):
            if stop_event.is_set():
                break

            now = time.monotonic()
            if max_seconds is not None and (now - t0) >= max_seconds:
                print("⏱️  Límite de tiempo alcanzado. Cerrando…")
                break

            # Mide latencia del update()
            with BlockTimer(lat):
                bar = builder.update(trade)

            if bar:
                bars_emitted += 1
                bps.mark_bar()

                # Persistencia sin bloqueo
                if writer is not None:
                    try:
                        writer.write(bar)
                    except Exception as ex:
                        logger.exception("Error en writer.write(bar): %s", ex)

                # Salida en consola si no está en modo quiet
                if not quiet:
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(
                        f"[{ts}] BAR → open={bar.open:.2f}, close={bar.close:.2f}, "
                        f"high={bar.high:.2f}, low={bar.low:.2f}, "
                        f"vol={getattr(bar, 'volume', 0.0):.6f}, "
                        f"n={getattr(bar, 'trade_count', 0)}"
                    )

                if max_bars is not None and bars_emitted >= max_bars:
                    print(f"✅ Límite de barras alcanzado ({bars_emitted}). Cerrando…")
                    break

            # Informe periódico de rendimiento
            now2 = time.monotonic()
            if now2 - last_stats >= stats_every:
                last_stats = now2
                s = lat.snapshot()
                r = bps.snapshot()
                logger.info(
                    (
                        "lat(ms) p50=%.3f p95=%.3f max=%.3f | n=%d | "
                        "barras/s ema=%.2f win=%.2f (win=%ds, n=%d)"
                    ),
                    s.p50_ms,
                    s.p95_ms,
                    s.max_ms,
                    s.count,
                    r.ema_bars_per_sec,
                    r.window_bars_per_sec,
                    int(r.window_span_s),
                    r.bars_in_window,
                )

    except KeyboardInterrupt:
        print("\n🛑 Interrumpido por el usuario. Cerrando…")
    finally:
        # Cierre ordenado del writer
        if writer is not None:
            try:
                if hasattr(writer, "close") and callable(writer.close):
                    writer.close()
                logger.info("Writer cerrado correctamente.")
            except Exception as ex:
                logger.exception("Error al cerrar writer: %s", ex)

    print("👋 Stream finalizado.")


# ===========================================================================
# CLI
# ===========================================================================


def parse_args() -> argparse.Namespace:
    """Parser de argumentos CLI."""
    parser = argparse.ArgumentParser(description="Ejecuta el stream de micro-velas.")
    parser.add_argument("--symbol", required=True, help="Símbolo spot de Binance (p.ej. btcusdt)")
    parser.add_argument(
        "--rule",
        type=str,
        default="tick",
        help="Regla (tick, volume_qty, dollar, imbalance, ...)",
    )
    parser.add_argument("--limit", type=float, default=100, help="Límite de la regla.")
    parser.add_argument("--testnet", action="store_true", help="Usar Binance Testnet")
    parser.add_argument("--max-bars", type=int, default=None, help="Corta tras N barras")
    parser.add_argument("--max-seconds", type=float, default=None, help="Corta tras T segundos")
    parser.add_argument(
        "--stats-every",
        type=float,
        default=2.0,
        help="Intervalo entre informes de métricas (seg).",
    )
    parser.add_argument("--quiet", action="store_true", help="No imprimir cada barra cerrada")
    parser.add_argument("--no-persist", action="store_true", help="No guardar a disco")
    parser.add_argument(
        "--out-dir",
        type=str,
        default="data/bars_live",
        help="Directorio donde guardar las barras (si aplica).",
    )
    parser.add_argument(
        "--fmt",
        type=str,
        default="csv",
        choices=["csv", "jsonl", "parquet"],
        help="Formato de salida (requiere pyarrow para parquet).",
    )
    parser.add_argument(
        "--flush-every-secs",
        type=float,
        default=2.0,
        help="Frecuencia de vaciado de buffer (seg).",
    )
    parser.add_argument(
        "--flush-every-n",
        type=int,
        default=500,
        help="Vaciado del buffer cada N barras.",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        help="Nombre de sesión personalizado (opcional).",
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
            stats_every=args.stats_every,
            quiet=args.quiet,
            no_persist=args.no_persist,
            out_dir=args.out_dir,
            fmt=args.fmt,
            flush_every_secs=args.flush_every_secs,
            flush_every_n=args.flush_every_n,
            session=args.session,
        )
    )

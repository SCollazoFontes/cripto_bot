#!/usr/bin/env python3
"""
Trading en vivo con datos reales de Binance (testnet o mainnet).
Conecta al WebSocket de Binance, procesa trades en tiempo real,
construye micro-velas y ejecuta la estrategia.

Uso:
    source activate.sh
    python -m tools.live.run_binance \
        --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_live \
        --symbol BTCUSDT \
        --testnet \
        --duration 600 \
        --cash 10000 \
        --fees-bps 2.5 \
        --slip-bps 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import time

from tools.live.dashboard_launcher import launch_dashboard, should_launch_dashboard
from tools.live.output_writers import save_manifest
from tools.live.trading_loop import run_live_trading


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Trading en vivo con Binance WebSocket")
    p.add_argument("--run-dir", required=True, help="Directorio para guardar resultados")
    p.add_argument("--symbol", default="BTCUSDT", help="Símbolo a tradear")
    p.add_argument("--testnet", action="store_true", help="Usar Binance Testnet")
    p.add_argument("--duration", type=int, default=600, help="Duración en segundos")
    p.add_argument("--cash", type=float, default=10000.0, help="Capital inicial (USDT)")
    p.add_argument(
        "--fees-bps", type=float, default=10.0, help="Comisiones en bps (Binance: 10 bps = 0.1%%)"
    )
    p.add_argument(
        "--slip-bps",
        type=float,
        default=None,
        help="Slippage en bps (None = dinámico basado en spread)",
    )
    p.add_argument("--strategy", default=None, help="Nombre de estrategia (ej: momentum)")
    p.add_argument("--params", default=None, help="Parámetros de estrategia en JSON")
    # Opciones del dashboard
    p.add_argument(
        "--no-dashboard",
        action="store_true",
        help="No lanzar el dashboard (obsoleto si se usa --dashboard)",
    )
    p.add_argument(
        "--dashboard",
        choices=["auto", "yes", "no"],
        default="no",
        help="Controla si lanzar el dashboard: yes|no|auto (por defecto no)",
    )
    p.add_argument(
        "--panel",
        type=int,
        choices=[0, 1],
        default=None,
        help="Control binario del panel: 1 = sí, 0 = no (prioridad máxima)",
    )
    p.add_argument(
        "--dashboard-port", type=int, default=8501, help="Puerto del dashboard Streamlit"
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = pathlib.Path(args.run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    # Lanzar dashboard (opcional)
    dashboard_proc = None
    if should_launch_dashboard(args):
        port = int(args.dashboard_port)
        dashboard_proc = launch_dashboard(str(run_dir), port)

    started_ts = time.time()
    save_manifest(run_dir, args, started_ts)

    try:
        asyncio.run(
            run_live_trading(
                symbol=args.symbol,
                run_dir=run_dir,
                duration=args.duration,
                cash=args.cash,
                fees_bps=args.fees_bps,
                slip_bps=args.slip_bps,
                testnet=args.testnet,
                strategy_name=args.strategy,
                strategy_params=args.params,
            )
        )
    finally:
        if dashboard_proc is not None:
            dashboard_proc.terminate()
            try:
                dashboard_proc.wait(timeout=3)
            except Exception:
                pass


if __name__ == "__main__":
    main()

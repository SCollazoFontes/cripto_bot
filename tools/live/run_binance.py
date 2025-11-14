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
import json
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
    # Parámetros específicos vol_breakout
    p.add_argument(
        "--vb-lookback", type=int, default=None, help="vol_breakout: tamaño canal (lookback)"
    )
    p.add_argument("--vb-atr-period", type=int, default=None, help="vol_breakout: periodo ATR")
    p.add_argument(
        "--vb-atr-mult",
        type=float,
        default=None,
        help="vol_breakout: multiplicador ATR para ruptura",
    )
    p.add_argument(
        "--vb-stop-mult", type=float, default=None, help="vol_breakout: multiplicador ATR para stop"
    )
    p.add_argument(
        "--vb-qty-frac",
        type=float,
        default=None,
        help="vol_breakout: fracción de capital por trade",
    )
    p.add_argument("--vb-debug", action="store_true", help="vol_breakout: activar logs debug")
    # Parámetros específicos vwap_reversion
    p.add_argument(
        "--vr-vwap-window", type=int, default=None, help="vwap_reversion: ventana VWAP/Z-score"
    )
    p.add_argument(
        "--vr-z-entry", type=float, default=None, help="vwap_reversion: umbral entrada z"
    )
    p.add_argument("--vr-z-exit", type=float, default=None, help="vwap_reversion: umbral salida z")
    p.add_argument(
        "--vr-take-profit-pct", type=float, default=None, help="vwap_reversion: take profit %"
    )
    p.add_argument(
        "--vr-stop-loss-pct", type=float, default=None, help="vwap_reversion: stop loss %"
    )
    p.add_argument(
        "--vr-qty-frac", type=float, default=None, help="vwap_reversion: fracción capital"
    )
    p.add_argument("--vr-warmup", type=int, default=None, help="vwap_reversion: barras warmup")
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
    # Opciones de Bar Builder
    p.add_argument(
        "--bar-tick-limit",
        type=int,
        default=None,
        help="Número de trades por barra (ej: 100)",
    )
    p.add_argument(
        "--bar-qty-limit",
        type=float,
        default=None,
        help="Volumen BTC acumulado por barra (ej: 5.0)",
    )
    p.add_argument(
        "--bar-value-limit",
        type=float,
        default=None,
        help="Valor USDT negociado por barra (ej: 50000.0)",
    )
    p.add_argument(
        "--bar-imbal-limit",
        type=float,
        default=None,
        help="Desequilibrio compra/venta por barra (ej: 10.0)",
    )
    p.add_argument(
        "--bar-policy",
        choices=["any", "all"],
        default="any",
        help="Política de cierre: 'any' (OR) cierra cuando cualquier umbral se alcanza, 'all' (AND) cuando todos",
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

    # Construir params combinados si se especifican flags específicos de estrategia
    combined_params: dict = {}
    if args.params:
        try:
            parsed = json.loads(args.params)
            if isinstance(parsed, dict):
                combined_params.update(parsed)
            else:
                print("⚠️  --params debe ser un objeto JSON (dict)")
        except Exception as e:
            print(f"⚠️  Error parseando --params JSON: {e}")

    if args.strategy == "vol_breakout":
        # vol_breakout espera kwargs planos
        if args.vb_lookback is not None:
            combined_params["lookback"] = args.vb_lookback
        if args.vb_atr_period is not None:
            combined_params["atr_period"] = args.vb_atr_period
        if args.vb_atr_mult is not None:
            combined_params["atr_mult"] = args.vb_atr_mult
        if args.vb_stop_mult is not None:
            combined_params["stop_mult"] = args.vb_stop_mult
        if args.vb_qty_frac is not None:
            combined_params["qty_frac"] = args.vb_qty_frac
        if args.vb_debug:
            combined_params["debug"] = True

    elif args.strategy == "vwap_reversion":
        # vwap_reversion espera un único parámetro 'params' con un dict interno
        if "params" not in combined_params or not isinstance(combined_params.get("params"), dict):
            # Si el usuario pasó claves planas, las anidamos automáticamente
            combined_params = {"params": combined_params if combined_params else {}}

        vr = combined_params["params"]
        if args.vr_vwap_window is not None:
            vr["vwap_window"] = args.vr_vwap_window
        if args.vr_z_entry is not None:
            vr["z_entry"] = args.vr_z_entry
        if args.vr_z_exit is not None:
            vr["z_exit"] = args.vr_z_exit
        if args.vr_take_profit_pct is not None:
            vr["take_profit_pct"] = args.vr_take_profit_pct
        if args.vr_stop_loss_pct is not None:
            vr["stop_loss_pct"] = args.vr_stop_loss_pct
        if args.vr_qty_frac is not None:
            vr["qty_frac"] = args.vr_qty_frac
        if args.vr_warmup is not None:
            vr["warmup"] = args.vr_warmup

    # Serializar params combinados (o None si vacío)
    params_json = json.dumps(combined_params) if combined_params else None

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
                strategy_params=params_json,
                bar_tick_limit=args.bar_tick_limit,
                bar_qty_limit=args.bar_qty_limit,
                bar_value_limit=args.bar_value_limit,
                bar_imbal_limit=args.bar_imbal_limit,
                bar_policy=args.bar_policy,
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

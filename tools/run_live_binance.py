#!/usr/bin/env python3
# tools/run_live_binance.py
"""
DEPRECATED: Wrapper de compatibilidad. Usa tools.live.run_binance en su lugar.

Trading en vivo con datos reales de Binance (testnet o mainnet).

Uso:
    source activate.sh
    python -m tools.run_live_binance \
        --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_live \
        --symbol BTCUSDT \
        --testnet \
        --duration 600 \
        --cash 10000 \
        --fees-bps 2.5 \
        --slip-bps 1.0
"""

from __future__ import annotations

# Reexportar todo desde el nuevo módulo modular
from tools.live.run_binance import main


# Alias para compatibilidad
def _env_to_bool01(val):
    from tools.live.dashboard_launcher import _env_to_bool01 as impl

    return impl(val)


def _should_launch_dashboard(args):
    from tools.live.dashboard_launcher import should_launch_dashboard

    return should_launch_dashboard(args)


if __name__ == "__main__":
    main()

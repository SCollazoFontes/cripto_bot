import argparse
from pathlib import Path
import sys

# Intentar imports de paquete 'visual'; fallback a modificar sys.path si es necesario
try:
    from visual.kill_switch import handle_kill_switch
    from visual.layout import render_layout
    from visual.ohlc_candles import render_ohlc
except Exception:
    sys.path.append(str(Path(__file__).parent / "visual"))
    from kill_switch import handle_kill_switch
    from layout import render_layout
    from ohlc_candles import render_ohlc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=str, default="runs/latest")
    args, _ = parser.parse_known_args()
    run_dir = args.run_dir

    # Layout principal
    render_layout(run_dir)
    # Kill-switch (sidebar)
    handle_kill_switch(run_dir)
    # Sección principal: velas OHLC
    render_ohlc(run_dir)


if __name__ == "__main__":
    main()

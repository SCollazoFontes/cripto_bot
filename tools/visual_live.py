"""Streamlit entrypoint for live trading dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tools.visual.layout import render_dashboard

# Ensure project root is on sys.path when streamlit launches the script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dashboard de trading en vivo")
    parser.add_argument("--run-dir", required=True, help="Directorio con archivos de la corrida")
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser()
    render_dashboard(str(run_dir))


if __name__ == "__main__":
    main()

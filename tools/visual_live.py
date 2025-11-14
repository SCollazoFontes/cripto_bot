import argparse
from pathlib import Path
import sys

# Intentar imports de paquete 'visual'; fallback a modificar sys.path si es necesario
try:
    from visual.layout import render_dashboard
except Exception:
    sys.path.append(str(Path(__file__).parent / "visual"))
    from layout import render_dashboard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=str, default="runs/latest")
    args, _ = parser.parse_known_args()
    run_dir = args.run_dir

    # Renderiza todo el dashboard completo
    render_dashboard(run_dir)


if __name__ == "__main__":
    main()

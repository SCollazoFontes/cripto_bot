"""Dashboard launcher utilities for live trading visualization."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
import webbrowser


def _env_to_bool01(val: str | None) -> bool | None:
    """Convierte env var a bool solo si es '1' o '0'."""
    if val is None:
        return None
    s = val.strip()
    if s == "1":
        return True
    if s == "0":
        return False
    return None


def should_launch_dashboard(args: argparse.Namespace) -> bool:
    """Determina si se debe lanzar el dashboard según args y env vars.

    Prioridad: --panel (1/0) > --dashboard yes/no > --no-dashboard > env (1/0) > False
    """
    if getattr(args, "panel", None) is not None:
        return bool(args.panel)
    if getattr(args, "dashboard", "auto") in ("yes", "no"):
        return args.dashboard == "yes"
    if getattr(args, "no_dashboard", False):
        return False
    for env_name in ("CRIPTOBOT_PANEL", "PANEL", "CRIPTOBOT_DASHBOARD", "DASHBOARD"):
        opt = _env_to_bool01(os.getenv(env_name))
        if opt is not None:
            return opt
    return False


def launch_dashboard(run_dir: str, port: int) -> subprocess.Popen | None:
    """Lanza el dashboard de Streamlit y abre el navegador.

    Returns:
        Proceso del dashboard o None si falla.
    """
    dashboard_cmd = [
        "streamlit",
        "run",
        "tools/visual_live.py",
        "--server.headless=true",
        "--server.address=localhost",
        f"--server.port={port}",
        "--",
        f"--run-dir={run_dir}",
    ]
    try:
        dashboard_proc = subprocess.Popen(dashboard_cmd)
    except Exception as e:
        print(f"⚠️  No se pudo lanzar el dashboard: {e}")
        return None

    # Esperar readiness y abrir navegador
    url = f"http://localhost:{port}"
    try:
        import requests
    except Exception:
        requests = None  # type: ignore

    start_wait = time.time()
    ready = False
    if requests is not None:
        while time.time() - start_wait < 15:
            try:
                r = requests.get(url + "/healthz", timeout=0.5)  # type: ignore
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.3)
    else:
        time.sleep(3)
        ready = True

    if ready:
        if not webbrowser.open(url):
            try:
                subprocess.Popen(["open", url])  # macOS fallback
            except Exception:
                print(f"🔗 Abre manualmente: {url}")
    else:
        print("⚠️ No se pudo detectar el dashboard de Streamlit en el tiempo esperado.")

    return dashboard_proc

# ============================================================
# check_system.py — Autodiagnóstico de cripto_bot
# ------------------------------------------------------------
# Ejecuta tres checks:
#   1) Carga de configuración (YAML + .env overrides)
#   2) Logger central (consola y archivo con rotación)
#   3) Ejecución del Engine (3 ciclos)
#
# Úsalo desde la raíz del proyecto:
#   python check_system.py
# ============================================================

from pathlib import Path
import sys
import time

# Asegurar que ./src está en sys.path antes de importar core.*
PROJECT_ROOT = Path(__file__).parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from loguru import logger  # noqa: E402

from core.config_loader import get_config  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.logger_config import init_logger  # noqa: E402


def ok(msg: str) -> None:
    print(f"✅ {msg}")


def fail(msg: str, e: Exception) -> None:
    print(f"❌ {msg}\n   → {type(e).__name__}: {e}")


def check_config() -> bool:
    try:
        cfg = get_config()
        ok(
            f"Config cargada — use_testnet={cfg['environment']['use_testnet']}, "
            f"log_level={cfg['environment']['log_level']}, "
            f"symbol={cfg['trading']['symbol']}, "
            f"cycle_delay={cfg['trading']['cycle_delay']}"
        )
        return True
    except Exception as e:
        fail("Fallo cargando configuración", e)
        return False


def check_logger() -> bool:
    try:
        init_logger()
        logger.info("Logger OK (info)")
        logger.debug("Logger OK (debug)")
        log_file = Path("data/logs/bot.log")
        # Dar un respiro para que el handler escriba a disco
        time.sleep(0.05)
        if log_file.exists() and log_file.stat().st_size > 0:
            ok(f"Logger escribe en archivo: {log_file}")
            return True
        raise FileNotFoundError("No se encontró data/logs/bot.log o está vacío")
    except Exception as e:
        fail("Fallo en logger", e)
        return False


def check_engine() -> bool:
    try:
        engine = Engine()
        # Acelerar el test si tu cycle_delay es alto
        try:
            engine.cycle_delay = min(0.2, float(engine.cycle_delay))
        except Exception:
            pass
        engine.run()
        ok("Engine ejecutó 3 ciclos correctamente")
        return True
    except Exception as e:
        fail("Fallo ejecutando Engine", e)
        return False


if __name__ == "__main__":
    print("=== Autodiagnóstico cripto_bot ===")
    all_ok = True
    all_ok &= check_config()
    all_ok &= check_logger()
    all_ok &= check_engine()
    print("==================================")
    print("✅ TODO OK" if all_ok else "❌ Hay fallos arriba; revisa mensajes.")

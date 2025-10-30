# ============================================================
# main.py — Punto de entrada del bot de trading "cripto_bot"
# ------------------------------------------------------------
# Arregla el problema de imports añadiendo /src al sys.path
# ANTES de importar módulos del paquete "core.*".
#
# Además:
#  - Carga .env pronto (por si quieres leer otras variables)
#  - Lanza el Engine
# ============================================================

from pathlib import Path
import sys

# --- 1) AÑADIR ./src AL sys.path ANTES DE NADA ----------------
# Esto hace que "core.*" sea importable desde cualquier módulo.
PROJECT_ROOT = Path(__file__).parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

# --- 2) CARGAR .env (opcional, pero útil pronto) --------------
from dotenv import load_dotenv  # noqa: E402 (import tardío por orden lógico)

load_dotenv()

# --- 3) IMPORTAR Y EJECUTAR EL ENGINE -------------------------
from core.engine import Engine  # ya funciona el import corto gracias al paso 1

if __name__ == "__main__":
    engine = Engine()
    engine.run()

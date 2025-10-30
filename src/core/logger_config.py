# ============================================================
# src/core/logger_config.py — Configuración central del logger
# ------------------------------------------------------------
# Este módulo define una función init_logger() que configura
# el logger global de Loguru según las variables del entorno (.env)
#
# Ventajas de centralizar el logging:
#   ✅ Consistencia: todos los módulos usan el mismo formato y nivel
#   ✅ Limpieza: engine.py, data_feed.py, etc. no repiten configuración
#   ✅ Seguridad: niveles, rutas y rotación controlados desde aquí
#
# El logger escribe en:
#   - Consola (colorizada, nivel configurable)
#   - Archivo de logs (rotación diaria en /data/logs/)
#
# ============================================================

import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger


# ============================================================
# Función: init_logger
# ============================================================
def init_logger() -> None:
    """
    Inicializa la configuración global del logger según el entorno.
    Llama a esta función una sola vez al inicio del programa
    (por ejemplo, desde main.py o desde Engine.__init__()).
    """

    # --- Cargar variables del .env ---
    load_dotenv()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # --- Crear carpeta para logs (si no existe) ---
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir / "bot.log"

    # --- Eliminar configuración previa ---
    logger.remove()

    # --- Formato de salida ---
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # --- Añadir salida a consola (colorizada) ---
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=log_level,
        colorize=True,
        format=log_format,
    )

    # --- Añadir salida a archivo (rotación diaria) ---
    logger.add(
        sink=log_file_path,
        level=log_level,
        rotation="1 day",  # crea un archivo nuevo cada día
        retention="7 days",  # mantiene 7 días de logs
        enqueue=True,  # thread-safe
        backtrace=True,  # traza completa de errores
        diagnose=True,  # detalles extendidos de excepciones
        format=log_format,
    )

    logger.info(f"Logger inicializado (nivel {log_level})")
    logger.debug(f"Logs guardados en: {log_file_path}")


# ============================================================
# Ejemplo de uso (solo si se ejecuta este módulo directamente)
# ============================================================
if __name__ == "__main__":
    init_logger()
    logger.info("Prueba de logger: info")
    logger.debug("Prueba de logger: debug")
    logger.warning("Prueba de logger: warning")
    logger.error("Prueba de logger: error")

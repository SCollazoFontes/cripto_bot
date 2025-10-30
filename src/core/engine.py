# ============================================================
# src/core/engine.py — Motor principal del bot cripto_bot
# ------------------------------------------------------------
# Versión que usa:
#   - Logger centralizado (init_logger)
#   - Config central (config_loader.get_config)
# ============================================================

from time import sleep

from loguru import logger

from core.config_loader import get_config

# Importes de nuestro propio paquete (funcionan porque main.py
# mete ./src en sys.path ANTES de importar este módulo)
from core.logger_config import init_logger


class Engine:
    def __init__(self) -> None:
        """
        Inicializa el motor con logger y configuración central.
        """
        # Inicializa el logger global (con nivel desde .env/ YAML)
        init_logger()

        # Lee configuración (YAML + overrides de .env)
        cfg = get_config()

        # Guardamos lo esencial en atributos
        self.use_testnet: bool = bool(cfg["environment"]["use_testnet"])
        self.symbol: str = str(cfg["trading"]["symbol"])
        self.cycle_delay: float = float(cfg["trading"]["cycle_delay"])

        logger.info("Inicializando motor de trading...")
        logger.debug(f"Modo testnet: {self.use_testnet}")
        logger.debug(f"Símbolo activo: {self.symbol}")
        logger.debug(f"cycle_delay: {self.cycle_delay}")

        self.is_running: bool = False

        logger.debug("Motor configurado en modo base (sin conexiones todavía).")

    def run(self) -> None:
        """
        Bucle principal del motor (simulado: 3 ciclos).
        """
        logger.info("Ejecutando ciclo principal...")

        self.is_running = True
        cycle_count = 0

        try:
            while self.is_running:
                cycle_count += 1
                logger.debug(f"Ejecutando ciclo #{cycle_count}")

                # (futuro) 1) recibir datos -> 2) estrategia -> 3) decidir -> 4) ejecutar
                sleep(self.cycle_delay)

                if cycle_count >= 3:
                    logger.info("Simulación terminada (3 ciclos completados).")
                    self.is_running = False

        except KeyboardInterrupt:
            logger.warning("Ejecución interrumpida manualmente (Ctrl+C).")
            self.is_running = False

        except Exception as e:
            logger.exception(f"Error en el motor: {e}")
            self.is_running = False

        finally:
            logger.info("Motor detenido correctamente.")

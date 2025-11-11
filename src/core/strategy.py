# core/strategy.py

"""
Interfaz mínima de estrategia para el motor de ejecución.

Objetivo
--------
Estandarizar cómo el runner (o el Engine) interactúa con las estrategias,
sin acoplarse a una implementación concreta. De este modo podremos:
- Cambiar/añadir estrategias sin tocar el runner.
- Añadir un broker real (SimBroker) sin reescribir estrategias.
- Testear estrategias en aislamiento.

Ciclo de vida
-------------
- on_start(broker):      se llama una vez antes de consumir barras.
- on_bar(bar, broker):   se llama por cada barra.
- on_end(broker):        se llama una vez al terminar (flush, cierres, etc.).

Notas
-----
- `broker` es una abstracción: en el siguiente paso usaremos `SimBroker`,
  pero aquí no lo exigimos para evitar dependencias circulares.
- Las estrategias pueden emitir órdenes a través de `broker` si éste
  implementa `send(order_request)` (lo conectaremos tras Buy&Hold).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Strategy(ABC):
    """
    Base abstracta para cualquier estrategia.

    Subclases deben implementar al menos `on_bar`. Los métodos `on_start`
    y `on_end` son opcionales y sirven para inicialización/flush.
    """

    def __init__(self, name: str | None = None) -> None:
        self._name = name or self.__class__.__name__

    @property
    def name(self) -> str:
        return self._name

    # ---- Hooks de ciclo de vida ----
    def on_start(self, broker: Any) -> None:  # noqa: ANN401 - broker genérico
        """
        Hook opcional: inicialización antes del primer bar.
        """
        # Por defecto, no hace nada.
        return None

    @abstractmethod
    def on_bar(self, bar: Any, broker: Any) -> None:  # noqa: ANN401
        """
        Hook obligatorio: se ejecuta en cada barra.

        Recomendación: la estrategia debe ser *pura* respecto al estado externo,
        y usar el `broker` para:
          - consultar posiciones/cash/precios,
          - enviar órdenes (cuando esté integrado),
          - registrar métricas/diagnósticos si el broker lo expone.

        En este primer paso, el runner podrá pasar un broker "dummy" o un
        objeto mínimo; en el siguiente paso inyectaremos `SimBroker`.
        """
        raise NotImplementedError

    def on_end(self, broker: Any) -> None:  # noqa: ANN401
        """
        Hook opcional: limpieza/cierres al finalizar el stream de barras.
        """
        # Por defecto, no hace nada.
        return None

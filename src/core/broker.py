"""
Broker: protocolo para ejecución de órdenes de mercado (simulada o real).

Define la interfaz mínima que debe cumplir cualquier broker compatible con el motor Engine,
ya sea en modo simulación (SimBroker) o en vivo (ej. conexión a Binance en el futuro).
"""

from __future__ import annotations

from typing import Any, Protocol


class Broker(Protocol):
    """
    Protocolo para brokers de ejecución.

    Métodos obligatorios:
    - submit_order: ejecuta una orden de mercado y retorna un dict con los detalles del trade.
    - equity: retorna equity total (cash + posición marcada al precio actual).
    - cash: efectivo disponible.
    - position_qty: cantidad de la posición actual (positiva = long, negativa = short).

    Atributos opcionales (no todos los brokers reales los exponen):
    - allow_short: indica si se permite operar en corto.
    """

    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Ejecuta una orden de mercado."""
        ...

    def equity(self, mark_price: float | None = None) -> float:
        """Devuelve el equity total (cash + posición al precio de marcado)."""
        ...

    @property
    def cash(self) -> float:
        """Efectivo disponible."""
        ...

    @property
    def position_qty(self) -> float:
        """Cantidad de la posición actual."""
        ...

    @property
    def allow_short(self) -> bool:
        """Indica si se permite operar en corto."""
        ...

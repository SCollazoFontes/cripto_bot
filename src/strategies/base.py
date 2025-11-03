# ============================================================
# src/strategies/base.py — Interfaz mínima de estrategias
# ------------------------------------------------------------
# Contrato:
#   - on_price(price, ts_ms, position_qty, position_avg_price) -> Signal
#   - La estrategia decide BUY/SELL/HOLD; el sizing lo hace el Engine.
# ============================================================

from __future__ import annotations

from enum import Enum, auto
from typing import Optional


class Signal(Enum):
    HOLD = auto()
    BUY = auto()
    SELL = auto()


class Strategy:
    """
    Interfaz mínima: dado el precio actual y el estado de posición,
    devuelve una señal BUY/SELL/HOLD. El Engine se encarga del tamaño.
    """

    def on_price(
        self,
        price: float,
        ts_ms: Optional[int],
        position_qty: float,
        position_avg_price: float,
    ) -> Signal:
        raise NotImplementedError("Debe implementar on_price() en la subclase")

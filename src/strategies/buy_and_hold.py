# strategies/buy_and_hold.py
"""
Estrategia Buy & Hold mínima basada en la interfaz Strategy.

Diseño
------
- Señal única: comprar TODO el efectivo disponible en la primera barra que llegue.
- No asume ningún broker concreto. En lugar de enviar órdenes directamente,
  la estrategia expone una "signal queue" muy simple que el runner puede leer.
- En el siguiente paso, el runner sustituirá su ejecución manual por `SimBroker`,
  pero esta estrategia NO necesitará cambios.

Uso esperado desde el runner (pseudocódigo):
--------------------------------------------
strategy = BuyAndHoldStrategy(price_mode="close")
strategy.on_start(broker)
for bar in feed:
    strategy.on_bar(bar, broker)
    for sig in strategy.drain_signals():
        if sig["type"] == "BUY_ALL":
            # el runner ejecuta la compra con su lógica actual (o vía broker)
            ...
strategy.on_end(broker)

Señales emitidas
----------------
- {"type": "BUY_ALL", "price_mode": <"close"|"open"|"mid">, "t": "<iso|raw>"}

Notas
-----
- `price_mode` indica qué precio prefiere usar la estrategia para la ejecución.
- `t` es informativo: el runner puede usar su propio timestamp.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from src.core.strategy import Strategy


class BuyAndHoldStrategy(Strategy):
    """
    Estrategia que compra una única vez (con todo el efectivo disponible)
    y mantiene la posición hasta el final.
    """

    def __init__(self, price_mode: str = "close", name: Optional[str] = None) -> None:
        """
        Parameters
        ----------
        price_mode : str
            "close" | "open" | "mid". Indica el precio de referencia deseado
            para ejecutar la compra (el runner decide cómo aplicarlo).
        name : Optional[str]
            Nombre opcional de la estrategia.
        """
        super().__init__(name=name or "BuyAndHold")
        if price_mode not in {"close", "open", "mid"}:
            raise ValueError("price_mode debe ser 'close', 'open' o 'mid'")
        self.price_mode = price_mode

        self._did_buy = False
        self._signals: List[Dict[str, Any]] = []

    # -------------------- ciclo de vida -------------------- #
    def on_start(self, broker: Any) -> None:  # noqa: ANN401
        self._did_buy = False
        self._signals.clear()

    def on_bar(self, bar: Any, broker: Any) -> None:  # noqa: ANN401
        """
        En la primera barra, empuja una señal de 'comprar todo'.
        En el resto, no hace nada.
        """
        if not self._did_buy:
            self._signals.append(
                {
                    "type": "BUY_ALL",
                    "price_mode": self.price_mode,
                    "t": _to_iso(_maybe_get_time(bar)),
                }
            )
            self._did_buy = True

    def on_end(self, broker: Any) -> None:  # noqa: ANN401
        # No necesita flush adicional.
        return None

    # -------------------- API de señales -------------------- #
    def drain_signals(self) -> List[Dict[str, Any]]:
        """
        Devuelve y vacía la cola de señales acumuladas desde el último `drain`.
        """
        out = list(self._signals)
        self._signals.clear()
        return out


# -------------------- utilidades locales -------------------- #
def _maybe_get_time(bar: Any) -> Any:
    """
    Intenta obtener un campo temporal típico del objeto `bar`.
    (El runner puede ignorarlo y usar su propio timestamp si lo prefiere.)
    """
    for key in ("t_close", "t_open", "ts", "t", "timestamp", "time"):
        if hasattr(bar, key):
            return getattr(bar, key)
        try:
            return bar[key]  # type: ignore[index]
        except Exception:
            pass
    return None


def _to_iso(ts_val: Any) -> str:
    """Convierte a ISO los formatos comunes (epoch ns/us/ms/s, Timestamp, str)."""
    if ts_val is None:
        return ""
    if isinstance(ts_val, (pd.Timestamp,)):
        return pd.to_datetime(ts_val, utc=True).isoformat()
    try:
        v = float(ts_val)
        if v > 1e12:  # ns
            return pd.to_datetime(v, unit="ns", utc=True).isoformat()
        if v > 1e10:  # us
            return pd.to_datetime(v, unit="us", utc=True).isoformat()
        if v > 1e9:  # ms
            return pd.to_datetime(v, unit="ms", utc=True).isoformat()
        if v > 1e8:  # s
            return pd.to_datetime(v, unit="s", utc=True).isoformat()
    except Exception:
        pass
    return str(ts_val)

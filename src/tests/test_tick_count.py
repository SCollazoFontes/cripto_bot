"""
Tests unitarios para TickCountBarBuilder (tick bars).

Cobertura principal:
- Cierre correcto al alcanzar `tick_limit`.
- Cálculo de OHLCV y timestamps (start_time / end_time).
- Reinicio del estado interno tras cada cierre (`reset` implícito en `update`).
- Validaciones de construcción (tick_limit inválido).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from bars.base import Trade
from bars.tick_count import TickCountBarBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(i: int) -> datetime:
    """
    Genera una marca de tiempo en UTC separada por i milisegundos desde un t0 fijo.
    Usamos tiempos crecientes para poder verificar start_time y end_time.
    """
    t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return t0 + timedelta(milliseconds=i)


def _trade(price: float, qty: float, i: int, is_buyer_maker: bool = False) -> Trade:
    """
    Construye un Trade sintético.
    - price, qty parametrizables
    - timestamp ordenado (con _ts(i))
    - is_buyer_maker por defecto False (no afecta al builder de ticks)
    """
    return Trade(price=price, qty=qty, timestamp=_ts(i), is_buyer_maker=is_buyer_maker)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tick_count_closes_on_limit_and_builds_correct_ohlcv() -> None:
    """
    Dado un tick_limit=3 y 5 trades:
      - La 1ª barra cierra al 3er trade
      - La 2ª barra cierra al 5º trade (2 trades restantes + 1 faltante → no cierra)
    Verificamos OHLCV y tiempos.
    """
    builder = TickCountBarBuilder(tick_limit=3)

    # Alimentamos 3 trades → debe cerrar 1ª barra
    t1 = _trade(100.0, 0.5, i=0)
    t2 = _trade(101.0, 0.7, i=1)
    t3 = _trade(99.5, 0.3, i=2)

    assert builder.update(t1) is None
    assert builder.update(t2) is None
    bar1 = builder.update(t3)
    assert bar1 is not None, "La barra debería cerrar exactamente en el 3er trade."

    # Verificamos OHLCV y tiempos de la 1ª barra
    assert bar1.open == 100.0
    assert bar1.high == 101.0
    assert bar1.low == 99.5
    assert bar1.close == 99.5
    assert bar1.volume == pytest.approx(0.5 + 0.7 + 0.3)
    assert bar1.trade_count == 3
    assert bar1.start_time == t1.timestamp
    assert bar1.end_time == t3.timestamp

    # Ahora metemos 2 trades más → aún no debería cerrar (faltan 3 para el límite)
    t4 = _trade(100.2, 0.4, i=3)
    t5 = _trade(100.8, 0.2, i=4)
    assert builder.update(t4) is None
    assert builder.update(t5) is None

    # Añadimos un sexto para completar otra barra
    t6 = _trade(101.5, 0.6, i=5)
    bar2 = builder.update(t6)
    assert bar2 is not None, "La 2ª barra debe cerrar con los trades 4,5,6."

    # Verificamos OHLCV y tiempos de la 2ª barra
    assert bar2.open == 100.2
    assert bar2.high == 101.5
    assert bar2.low == 100.2
    assert bar2.close == 101.5
    assert bar2.volume == pytest.approx(0.4 + 0.2 + 0.6)
    assert bar2.trade_count == 3
    assert bar2.start_time == t4.timestamp
    assert bar2.end_time == t6.timestamp


def test_tick_limit_validation() -> None:
    """Debe fallar con tick_limit inválidos."""
    with pytest.raises(ValueError):
        TickCountBarBuilder(tick_limit=0)

    # Para evitar advertencias de mypy sobre tipos en tiempo de chequeo,
    # pasamos el valor erróneo como Any.
    bad_value: Any = "3"
    with pytest.raises(TypeError):
        TickCountBarBuilder(tick_limit=bad_value)


def test_reset_clears_internal_state() -> None:
    """
    `reset` debe vaciar el buffer y contador. Probamos llamándolo explícitamente.
    (En uso normal, `update` ya llama a `reset` tras construir la barra).
    """
    builder = TickCountBarBuilder(tick_limit=2)
    builder.update(_trade(100.0, 1.0, i=0))
    assert len(builder.get_current_trades()) == 1

    builder.reset()
    assert len(builder.get_current_trades()) == 0

    # Tras reset, debe volver a contar desde cero
    assert builder.update(_trade(101.0, 1.0, i=1)) is None
    bar = builder.update(_trade(102.0, 1.0, i=2))
    assert bar is not None
    assert bar.trade_count == 2

# src/core/metrics.py
# -----------------------------------------------------------------------------
# Telemetría ligera para el pipeline de micro-velas.
# - Latencias: añade una muestra (ms) por cada llamada a builder.update(event)
#   y obtén p50/p95/contadores mediante snapshot().
# - Ritmo de barras: marca cada barra cerrada y obtén barras/s (EMA y por ventana).
#
# Diseño:
# - Sin dependencias externas (ni numpy).
# - Cálculo de percentiles simple: mantenemos un buffer acotado (deque) y
#   calculamos p50/p95 sobre una copia ordenada cuando se pide snapshot() (coste O(n log n)).
#   Para telemetría cada 1-2 s, es suficiente y muy robusto.
# - Barras/s se estima de dos formas:
#     1) EMA (suave, rápido de leer).
#     2) Ventana deslizante (exacta en los últimos W segundos).
# -----------------------------------------------------------------------------

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
from typing import Deque, List, Optional

# ------------------------------ Utilidades -----------------------------------


def _percentile(sorted_values: List[float], q: float) -> float:
    """
    Percentil simple (q en [0,1]) sobre una lista YA ORDENADA.
    Interpolación lineal entre posiciones.
    """
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_values[0])
    # Posición "teórica"
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac)


class EWMA:
    """
    Media móvil exponencial para tasas (por ejemplo, barras/s).
    alpha en (0,1]; cuanto mayor, más reactiva.
    """

    def __init__(self, alpha: float, initial: Optional[float] = None) -> None:
        if not (0 < alpha <= 1):
            raise ValueError("alpha debe estar en (0, 1].")
        self.alpha = alpha
        self.value: Optional[float] = initial

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value

    def get(self) -> float:
        return float(self.value or 0.0)


# ---------------------------- Latencias (ms) ---------------------------------


@dataclass
class LatencySnapshot:
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float


class LatencyStats:
    """
    Acumula latencias (en ms) y genera snapshots con p50 y p95.

    Uso típico:
        lat = LatencyStats(maxlen=5000)
        ...
        t0 = time.perf_counter()
        bar = builder.update(event)
        t1 = time.perf_counter()
        lat.add_sample((t1 - t0) * 1000.0)
        ...
        snap = lat.snapshot()
        print(snap.count, snap.p50_ms, snap.p95_ms)
    """

    def __init__(self, maxlen: int = 5000) -> None:
        if maxlen < 100:
            raise ValueError("maxlen demasiado pequeño (mín 100).")
        self._buf: Deque[float] = deque(maxlen=maxlen)
        self._count_total = 0
        self._max_seen = 0.0

    def add_sample(self, latency_ms: float) -> None:
        x = float(max(0.0, latency_ms))
        self._buf.append(x)
        self._count_total += 1
        if x > self._max_seen:
            self._max_seen = x

    def snapshot(self) -> LatencySnapshot:
        arr = list(self._buf)
        arr.sort()
        p50 = _percentile(arr, 0.50)
        p95 = _percentile(arr, 0.95)
        max_ms = arr[-1] if arr else 0.0
        # El max_ms del snapshot refleja la ventana actual (no el absoluto histórico).
        return LatencySnapshot(count=len(arr), p50_ms=p50, p95_ms=p95, max_ms=max_ms)

    @property
    def count_total(self) -> int:
        """Número total de muestras incorporadas desde el inicio (histórico)."""
        return self._count_total

    @property
    def max_seen(self) -> float:
        """Máxima latencia observada históricamente (ms)."""
        return self._max_seen


# ------------------------- Ritmo de barras (barras/s) -------------------------


@dataclass
class BarsRateSnapshot:
    ema_bars_per_sec: float
    window_bars_per_sec: float
    window_span_s: float
    bars_in_window: int


class BarsPerSecond:
    """
    Estima barras/seg mediante:
      - EMA reactiva.
      - Ventana deslizante de últimos W segundos (exacta en esa ventana).

    Uso:
        br = BarsPerSecond(window_s=10.0, ema_alpha=0.3)
        ...
        if bar_cerrada:
            br.mark_bar()
        ...
        snap = br.snapshot()
        print(snap.ema_bars_per_sec, snap.window_bars_per_sec)
    """

    def __init__(self, window_s: float = 10.0, ema_alpha: float = 0.3) -> None:
        if window_s <= 0:
            raise ValueError("window_s debe ser > 0.")
        self.window_s = float(window_s)
        self._ts: Deque[float] = deque()
        self._ema = EWMA(alpha=ema_alpha, initial=0.0)
        self._last_mark_ts: Optional[float] = None

    def _now(self) -> float:
        return time.perf_counter()

    def mark_bar(self) -> None:
        """
        Llamar cada vez que se cierra y publica una barra.
        Actualiza EMA a partir del tiempo desde el último cierre.
        """
        now = self._now()
        self._ts.append(now)

        # Purga viejos fuera de ventana
        cutoff = now - self.window_s
        while self._ts and self._ts[0] < cutoff:
            self._ts.popleft()

        # Actualiza EMA según delta t entre marcas (si hay previa)
        if self._last_mark_ts is not None:
            dt = now - self._last_mark_ts
            if dt > 0:
                inst_rate = 1.0 / dt  # barras por segundo instantánea
                self._ema.update(inst_rate)
        self._last_mark_ts = now

    def snapshot(self) -> BarsRateSnapshot:
        now = self._now()
        cutoff = now - self.window_s
        # Asegura que la ventana está purgada para un conteo exacto
        while self._ts and self._ts[0] < cutoff:
            self._ts.popleft()
        bars_in_window = len(self._ts)
        window_rate = bars_in_window / self.window_s
        return BarsRateSnapshot(
            ema_bars_per_sec=self._ema.get(),
            window_bars_per_sec=window_rate,
            window_span_s=self.window_s,
            bars_in_window=bars_in_window,
        )


# --------------------------- Cronómetro de bloques ----------------------------


class BlockTimer:
    """
    Cronómetro de conveniencia (context manager) para medir bloques de código:

        with BlockTimer(latency_stats) as t:
            bar = builder.update(event)
        # añade la latencia automáticamente

    También usable manualmente:
        t = BlockTimer(latency_stats)
        t.start()
        ...
        t.stop()  # añade muestra
    """

    def __init__(self, sink: LatencyStats) -> None:
        self._sink = sink
        self._t0: Optional[float] = None

    def __enter__(self) -> "BlockTimer":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[object],
    ) -> None:
        """Cierra el cronómetro al salir del bloque `with`."""
        self.stop()

    def start(self) -> None:
        """Inicia la medición del bloque."""
        self._t0 = time.perf_counter()

    def stop(self) -> float:
        """Detiene el cronómetro y añade la muestra al registro."""
        if self._t0 is None:
            return 0.0
        dt_ms = (time.perf_counter() - self._t0) * 1000.0
        self._sink.add_sample(dt_ms)
        self._t0 = None
        return dt_ms

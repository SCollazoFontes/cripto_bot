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

# ------------------------------ Utilidades -----------------------------------


def _percentile(sorted_values: list[float], q: float) -> float:
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

    def __init__(self, alpha: float, initial: float | None = None) -> None:
        if not (0 < alpha <= 1):
            raise ValueError("alpha debe estar en (0, 1].")
        self.alpha = alpha
        self.value: float | None = initial

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
        self._buf: deque[float] = deque(maxlen=maxlen)
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
        self._ts: deque[float] = deque()
        self._ema = EWMA(alpha=ema_alpha, initial=0.0)
        self._last_mark_ts: float | None = None

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
        self._t0: float | None = None

    def __enter__(self) -> BlockTimer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
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


# -----------------------------------------------------------------------------
# Métricas de Performance para Trading (Sharpe, Sortino, MaxDD, etc.)
# -----------------------------------------------------------------------------


def calculate_returns(equity_curve: list[float]) -> list[float]:
    """Calcula retornos porcentuales entre barras."""
    if len(equity_curve) < 2:
        return []
    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] == 0:
            returns.append(0.0)
        else:
            ret = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            returns.append(ret)
    return returns


def calculate_sharpe(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """
    Sharpe Ratio = (Retorno promedio - Tasa libre riesgo) / Desviación estándar

    Interpretación:
    - > 1.0: Bueno
    - > 2.0: Muy bueno
    - > 3.0: Excelente
    """
    if not returns or len(returns) < 2:
        return 0.0
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    std_return = variance**0.5
    if std_return == 0:
        return 0.0
    return (mean_return - risk_free_rate) / std_return


def calculate_sortino(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """
    Sortino Ratio = (Retorno - RF) / Downside Deviation
    Similar a Sharpe pero solo penaliza volatilidad negativa.

    Interpretación: Similar a Sharpe, pero más generoso
    """
    if not returns or len(returns) < 2:
        return 0.0
    mean_return = sum(returns) / len(returns)
    downside_returns = [r for r in returns if r < 0]
    if not downside_returns:
        return float("inf") if mean_return > 0 else 0.0
    downside_mean = sum(downside_returns) / len(downside_returns)
    downside_variance = sum((r - downside_mean) ** 2 for r in downside_returns) / (
        len(downside_returns) - 1
    )
    downside_std = downside_variance**0.5
    if downside_std == 0:
        return 0.0
    return (mean_return - risk_free_rate) / downside_std


def calculate_max_drawdown(equity_curve: list[float]) -> tuple[float, int, int]:
    """
    Maximum Drawdown = Máxima pérdida desde peak hasta trough.

    Returns:
        (max_dd, peak_idx, trough_idx) en formato (%, index, index)
    """
    if not equity_curve or len(equity_curve) < 2:
        return 0.0, 0, 0

    peak = equity_curve[0]
    peak_idx = 0
    max_dd = 0.0
    max_dd_peak_idx = 0
    max_dd_trough_idx = 0

    for i, value in enumerate(equity_curve):
        if value > peak:
            peak = value
            peak_idx = i
        dd = (peak - value) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            max_dd_peak_idx = peak_idx
            max_dd_trough_idx = i

    return max_dd, max_dd_peak_idx, max_dd_trough_idx


def calculate_profit_factor(trades_pnl: list[float]) -> float:
    """
    Profit Factor = Gross Profit / Gross Loss

    Interpretación:
    - > 1.0: Estrategia rentable
    - > 1.5: Buena
    - > 2.0: Excelente
    """
    winning_trades = [pnl for pnl in trades_pnl if pnl > 0]
    losing_trades = [pnl for pnl in trades_pnl if pnl < 0]

    gross_profit = sum(winning_trades) if winning_trades else 0.0
    gross_loss = abs(sum(losing_trades)) if losing_trades else 0.0

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_win_rate(trades_pnl: list[float]) -> tuple[float, int, int]:
    """
    Win Rate = Winning Trades / Total Trades

    Returns:
        (win_rate, num_wins, num_losses)
    """
    if not trades_pnl:
        return 0.0, 0, 0

    num_wins = sum(1 for pnl in trades_pnl if pnl > 0)
    num_losses = sum(1 for pnl in trades_pnl if pnl < 0)
    total_trades = len(trades_pnl)

    win_rate = num_wins / total_trades if total_trades > 0 else 0.0
    return win_rate, num_wins, num_losses


def calculate_avg_win_loss(trades_pnl: list[float]) -> tuple[float, float]:
    """
    Calcula ganancia promedio y pérdida promedio.

    Returns:
        (avg_win, avg_loss)
    """
    winning_trades = [pnl for pnl in trades_pnl if pnl > 0]
    losing_trades = [pnl for pnl in trades_pnl if pnl < 0]

    avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0.0
    avg_loss = sum(losing_trades) / len(losing_trades) if losing_trades else 0.0

    return avg_win, avg_loss


def calculate_all_metrics(
    equity_curve: list[float], trades_pnl: list[float], risk_free_rate: float = 0.0
) -> dict:
    """
    Calcula todas las métricas de una vez.

    Args:
        equity_curve: Lista de valores de equity por barra
        trades_pnl: Lista de PnL por trade (positivo o negativo)
        risk_free_rate: Tasa libre de riesgo (default 0)

    Returns:
        Dict con todas las métricas calculadas
    """
    returns = calculate_returns(equity_curve)
    max_dd, dd_peak_idx, dd_trough_idx = calculate_max_drawdown(equity_curve)
    win_rate, num_wins, num_losses = calculate_win_rate(trades_pnl)
    avg_win, avg_loss = calculate_avg_win_loss(trades_pnl)

    return {
        "sharpe_ratio": calculate_sharpe(returns, risk_free_rate),
        "sortino_ratio": calculate_sortino(returns, risk_free_rate),
        "max_drawdown": max_dd,
        "max_drawdown_peak_idx": dd_peak_idx,
        "max_drawdown_trough_idx": dd_trough_idx,
        "profit_factor": calculate_profit_factor(trades_pnl),
        "win_rate": win_rate,
        "num_winning_trades": num_wins,
        "num_losing_trades": num_losses,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_trade": (sum(trades_pnl) / len(trades_pnl)) if trades_pnl else 0.0,
        "total_return": (
            (equity_curve[-1] - equity_curve[0]) / equity_curve[0]
            if equity_curve and equity_curve[0] > 0
            else 0.0
        ),
    }

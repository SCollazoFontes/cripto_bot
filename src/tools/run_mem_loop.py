"""
Loop en memoria para validar estabilidad de los builders de micro-velas.
- No persiste en disco.
- Inyecta ticks sintéticos (Poisson) o puede adaptarse a feed real.
- Telemetría: p50/p95 de update() y barras/s (EMA/ventana).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import math
import random
import signal
import sys
import time
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

# Telemetría y métricas
from src.core.metrics import BarsPerSecond, BlockTimer, LatencyStats

# -----------------------------------------------------------------------------
# Logger del proyecto (opcional). Fallback a logging básico si la import falla.
# -----------------------------------------------------------------------------
_init_logger: Optional[Callable[[], None]] = None
try:
    from src.core.logger_config import init_logger as _init_logger
except Exception:
    pass

# -----------------------------------------------------------------------------
# Registry de builders (opcional; fallback dinámico si no se encuentra)
# -----------------------------------------------------------------------------
bars_registry: Optional[ModuleType] = None
try:
    from src.bars import registry as bars_registry
except Exception:
    pass


# ===========================================================================
# Generador sintético de ticks (Poisson con bursts)
# ===========================================================================


@dataclass
class SyntheticConfig:
    """Parámetros de simulación del generador sintético."""

    rate_eps: float = 1000.0
    price0: float = 100.0
    drift_pps: float = 0.0
    vol_pps: float = 5.0
    min_qty: float = 1.0
    max_qty: float = 10.0
    burst_prob: float = 0.02
    burst_mult: float = 4.0
    burst_len_ticks: int = 200


class PoissonTickSource:
    """Genera ticks aleatorios con llegada Poisson y posibles bursts."""

    def __init__(self, cfg: SyntheticConfig, seed: int = 42) -> None:
        self.cfg = cfg
        self._rng = random.Random(seed)
        self._price = cfg.price0
        self._burst_ticks_left = 0
        self._last_ts_ns = time.time_ns()

    def _interarrival_s(self) -> float:
        """Tiempo (s) hasta el siguiente tick."""
        rate = self.cfg.rate_eps
        if self._burst_ticks_left > 0:
            rate *= self.cfg.burst_mult
            self._burst_ticks_left -= 1
        elif self._rng.random() < self.cfg.burst_prob:
            self._burst_ticks_left = max(
                1, int(self.cfg.burst_len_ticks * max(0.2, self._rng.random()))
            )
        u = max(sys.float_info.min, self._rng.random())
        return -math.log(u) / max(1e-9, rate)

    def _next_price(self, dt_s: float) -> float:
        """Movimiento del precio (Browniano con deriva)."""
        drift = self.cfg.drift_pps * dt_s
        sigma = self.cfg.vol_pps * math.sqrt(max(1e-9, dt_s))
        delta = self._rng.gauss(drift, sigma)
        self._price = max(0.0001, self._price + delta)
        return self._price

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """Genera ticks indefinidamente."""
        while True:
            sleep_s = self._interarrival_s()
            if sleep_s > 0:
                time.sleep(sleep_s)

            now_ns = time.time_ns()
            dt_s = (now_ns - self._last_ts_ns) / 1e9
            self._last_ts_ns = now_ns
            px = self._next_price(dt_s)
            qty = self._rng.uniform(self.cfg.min_qty, self.cfg.max_qty)
            side = 1 if self._rng.random() < 0.5 else -1
            yield {"ts": now_ns, "price": px, "qty": qty, "side": side}


# ===========================================================================
# Factory: creación dinámica de builders (registry + fallback por nombre)
# ===========================================================================


def _create_builder(name: str, params: Dict[str, Any]) -> Any:
    """
    Intenta crear un builder por nombre.

    Orden:
      1) Si existe bars_registry con create(name, **params), usarlo.
      2) Fallback: importar src.bars.<name> y elegir una clase cuyo nombre
         contenga 'builder' y las piezas de <name> (p.ej. VolumeQtyBarBuilder).
    """
    # 1) Registry si está disponible
    if bars_registry and hasattr(bars_registry, "create"):
        try:
            return bars_registry.create(name, **params)
        except Exception as e:  # pragma: no cover
            logging.getLogger("src.tools.run_mem_loop").debug(
                "registry.create(%s) falló: %s. Probando fallback.", name, e
            )

    # 2) Fallback: import dinámico + heurística de nombre
    import importlib
    import inspect

    try:
        mod = importlib.import_module(f"src.bars.{name}")
    except ModuleNotFoundError as e:  # pragma: no cover
        raise RuntimeError(f"No se pudo importar src.bars.{name}: {e}") from e

    cands = []
    keys = name.lower().split("_")
    for cls_name, cls_obj in inspect.getmembers(mod, inspect.isclass):
        lname = cls_name.lower()
        if "builder" in lname and all(k in lname for k in keys):
            cands.append(cls_obj)

    if not cands:
        raise RuntimeError(f"No se encontró clase Builder en src.bars.{name}")

    # Preferir nombres más específicos (más largos)
    cands.sort(key=lambda c: len(c.__name__), reverse=True)
    cls = cands[0]
    return cls(**params)


# ===========================================================================
# Adaptación de eventos y detección de cierre de barra
# ===========================================================================


def _event_to_builder_args(event: Dict[str, Any]) -> Tuple[object]:
    """
    Adapta un evento sintético a formato compatible con builder.update().
    Añade alias estilo Binance (timestamp ms, quote_qty, is_buyer_maker, ...).
    """
    ts_ns = int(event.get("ts", time.time_ns()))
    side = int(event.get("side", 1))
    price = float(event.get("price") or 0.0)
    qty = float(event.get("qty", 0.0))
    quote_qty = price * qty
    is_buyer_maker = side < 0  # True si agresor fue SELL (convención Binance)

    obj = SimpleNamespace(
        price=price,
        qty=qty,
        side=side,
        ts=ts_ns,
        timestamp=int(round(ts_ns / 1_000_000)),  # ms
        ts_ns=ts_ns,
        ts_ms=ts_ns / 1_000_000.0,
        ts_s=ts_ns / 1_000_000_000.0,
        is_buyer_maker=is_buyer_maker,
        buyer_is_maker=is_buyer_maker,
        quote_qty=quote_qty,
    )
    return (obj,)


def _maybe_mark_bar(builder: Any, bar_obj: Any, bps: BarsPerSecond) -> None:
    """Detecta cierres de barra y marca el ritmo en métricas."""
    if bar_obj is not None:
        bps.mark_bar()
        return

    # pop_closed_bar()
    pop_cb = getattr(builder, "pop_closed_bar", None)
    if callable(pop_cb):
        try:
            bar = pop_cb()
            if bar is not None:
                bps.mark_bar()
                return
        except Exception:
            pass

    # last_closed_bar
    if hasattr(builder, "last_closed_bar"):
        bar = getattr(builder, "last_closed_bar")
        if bar is not None:
            try:
                setattr(builder, "last_closed_bar", None)
            except Exception:
                pass
            bps.mark_bar()
            return

    # has_closed_bar()
    has_cb = getattr(builder, "has_closed_bar", None)
    if callable(has_cb):
        try:
            if has_cb():
                bps.mark_bar()
                return
        except Exception:
            pass


# ===========================================================================
# CLI utils y normalizador de parámetros
# ===========================================================================


def _parse_kv_params(kvs: list[str]) -> Dict[str, Any]:
    """Convierte parámetros CLI 'k=v' en diccionario tipado."""
    out: Dict[str, Any] = {}
    for item in kvs:
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"--param debe ser k=v (recibido: {item})")
        k, v = item.split("=", 1)
        if v.lower() in {"true", "false"}:
            out[k] = v.lower() == "true"
        else:
            try:
                out[k] = float(v) if "." in v else int(v)
            except ValueError:
                out[k] = v
    return out


def _normalize_params(builder_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Homogeneiza nombres de parámetros según el tipo de builder."""
    name = builder_name.lower()
    p = dict(params)

    if name in {"tick", "tick_count"} and "n_ticks" in p:
        p.setdefault("tick_limit", p.pop("n_ticks"))

    if name in {"volume", "volume_qty"}:
        for key in ("volume_limit", "qty"):
            if key in p and "qty_limit" not in p:
                p["qty_limit"] = p.pop(key)

    if name in {"dollar", "notional"}:
        for key in ("dollar_limit", "notional_limit", "notional"):
            if key in p and "value_limit" not in p:
                p["value_limit"] = p.pop(key)

    if name in {"imbalance", "imbal"}:
        for key in ("imbalance_threshold", "threshold"):
            if key in p and "imbal_limit" not in p:
                p["imbal_limit"] = p.pop(key)

    return p


# ===========================================================================
# Loop principal de validación
# ===========================================================================


def run(
    builder_name: str,
    builder_params: Dict[str, Any],
    rate: float,
    stats_every: float,
    duration: Optional[float],
    *,
    burst_prob: Optional[float] = None,
    burst_mult: Optional[float] = None,
    burst_len: Optional[int] = None,
    seed: int = 42,
    price0: Optional[float] = None,
    vol_pps: Optional[float] = None,
    qty_min: Optional[float] = None,
    qty_max: Optional[float] = None,
) -> None:
    """
    Ejecuta un bucle en memoria que alimenta un builder con ticks sintéticos.
    - Evalúa latencias (p50/p95) de update().
    - Calcula barras/s (EMA y ventana deslizante).
    """
    if callable(_init_logger):
        try:
            _init_logger()
        except Exception:
            pass

    logger = logging.getLogger("src.tools.run_mem_loop")
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO)

    logger.info(
        "Iniciando run_mem_loop: builder=%s params=%s rate≈%s/s stats_every=%ss duration=%s",
        builder_name,
        builder_params,
        rate,
        stats_every,
        duration or "∞",
    )

    builder_params = _normalize_params(builder_name, builder_params)
    builder = _create_builder(builder_name, builder_params)

    lat = LatencyStats(maxlen=10000)
    bps = BarsPerSecond(window_s=10.0, ema_alpha=0.3)

    cfg = SyntheticConfig(rate_eps=rate)
    if burst_prob is not None:
        cfg.burst_prob = float(burst_prob)
    if burst_mult is not None:
        cfg.burst_mult = float(burst_mult)
    if burst_len is not None:
        cfg.burst_len_ticks = int(burst_len)
    if price0 is not None:
        cfg.price0 = float(price0)
    if vol_pps is not None:
        cfg.vol_pps = float(vol_pps)
    if qty_min is not None:
        cfg.min_qty = float(qty_min)
    if qty_max is not None:
        cfg.max_qty = float(qty_max)

    src = PoissonTickSource(cfg, seed=seed)
    stop = False

    def _sig_handler(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    start = time.perf_counter()
    last_stats = start

    try:
        for event in src:
            with BlockTimer(lat):
                args = _event_to_builder_args(event)
                bar = builder.update(*args)

            _maybe_mark_bar(builder, bar, bps)

            now = time.perf_counter()
            if now - last_stats >= stats_every:
                last_stats = now
                s = lat.snapshot()
                r = bps.snapshot()
                logger.info(
                    (
                        "lat(ms) p50=%.3f p95=%.3f max=%.3f | n=%d | "
                        "barras/s ema=%.2f win=%.2f (win=%ds, n=%d)"
                    ),
                    s.p50_ms,
                    s.p95_ms,
                    s.max_ms,
                    s.count,
                    r.ema_bars_per_sec,
                    r.window_bars_per_sec,
                    int(r.window_span_s),
                    r.bars_in_window,
                )

            if duration is not None and (now - start) >= duration:
                break
            if stop:
                logger.info("Interrumpido por señal.")
                break
    finally:
        s = lat.snapshot()
        r = bps.snapshot()
        logger.info(
            (
                "Fin. lat(ms) p50=%.3f p95=%.3f max=%.3f | n=%d | "
                "barras/s ema=%.2f win=%.2f (win=%ds, n=%d)"
            ),
            s.p50_ms,
            s.p95_ms,
            s.max_ms,
            s.count,
            r.ema_bars_per_sec,
            r.window_bars_per_sec,
            int(r.window_span_s),
            r.bars_in_window,
        )


# ===========================================================================
# CLI
# ===========================================================================


def main(argv: Optional[list[str]] = None) -> int:
    """Interfaz de línea de comandos para el loop en memoria."""
    p = argparse.ArgumentParser(description="Loop en memoria para validar builders de micro-velas.")
    p.add_argument("--builder", required=True, help="Nombre del builder (tick_count, volume_qty)")
    p.add_argument("--param", action="append", default=[], help="Parámetros del builder (k=v)")
    p.add_argument("--rate", type=float, default=1000.0, help="Tasa media de eventos/segundo")
    p.add_argument("--stats-every", type=float, default=2.0, help="Frecuencia métricas (s)")
    p.add_argument("--duration", type=float, default=None, help="Duración (seg, ∞ si None)")
    p.add_argument("--burst-prob", type=float, default=None, help="Probabilidad de burst")
    p.add_argument("--burst-mult", type=float, default=None, help="Multiplicador de burst")
    p.add_argument("--burst-len", type=int, default=None, help="Duración media burst (ticks)")
    p.add_argument("--seed", type=int, default=42, help="Semilla RNG")
    p.add_argument("--price0", type=float, default=None, help="Precio inicial override")
    p.add_argument("--vol-pps", type=float, default=None, help="Volatilidad override")
    p.add_argument("--qty-min", type=float, default=None, help="Cantidad mínima override")
    p.add_argument("--qty-max", type=float, default=None, help="Cantidad máxima override")

    args = p.parse_args(argv)
    params = _parse_kv_params(args.param)

    try:
        run(
            builder_name=args.builder,
            builder_params=params,
            rate=args.rate,
            stats_every=args.stats_every,
            duration=args.duration,
            burst_prob=args.burst_prob,
            burst_mult=args.burst_mult,
            burst_len=args.burst_len,
            seed=args.seed,
            price0=args.price0,
            vol_pps=args.vol_pps,
            qty_min=args.qty_min,
            qty_max=args.qty_max,
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

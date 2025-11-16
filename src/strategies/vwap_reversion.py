# src/strategies/vwap_reversion.py
"""
Estrategia: VWAP Reversion (intra/micro-barras)

Idea
----
Opera desvíos transitorios del precio respecto a un VWAP de ventana corta
utilizando bandas por z-score (entrada si |z| > z_entry; cierre si |z| < z_exit),
con protección por take-profit / stop-loss relativos.

Compatibilidad
--------------
- Usa el runtime común (`src.core.strategy_runtime`).
- Devuelve `OrderRequest` del paquete `src.strategies.base`.
- No depende de enums `Side/Decision` (usa literales 'BUY'/'SELL' y
  'OPEN_LONG'/'OPEN_SHORT'/'CLOSE'), para ser compatible con tu base actual.

Parámetros (por defecto entre paréntesis)
-----------------------------------------
- vwap_window (50):     tamaño de ventana para VWAP y z-score.
- z_entry (1.5):        umbral de entrada por z-score (|z| > z_entry).
- z_exit (0.5):         umbral de salida por vuelta a banda (|z| < z_exit).
- take_profit_pct (0.006): cierre por beneficio relativo al entry_price.
- stop_loss_pct (0.004):  cierre por pérdida relativa al entry_price.
- qty_frac (1.0):       fracción del equity en apertura (0..1).
- min_vol (1e-12):      volumen mínimo efectivo para VWAP (si no, usa 1.0).
- warmup (vwap_window): mínimo de barras antes de activar señales.

Entrada esperada (bar)
----------------------
dict con claves estándar: {'t','open','high','low','close','qty'/'volume','price','symbol'}.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from brokers.base import OrderRequest
from strategies.base import PositionState, Strategy, register_strategy, will_exit_non_negative


@register_strategy("vwap_reversion")
class VWAPReversionStrategy(Strategy):
    def __init__(
        self,
        vwap_window: int = 50,
        z_entry: float = 1.5,
        z_exit: float = 0.5,
        take_profit_pct: float = 0.006,
        stop_loss_pct: float = 0.004,
        qty_frac: float = 1.0,
        risk_pct: float | None = None,
        min_vol: float = 1e-12,
        warmup: int | None = None,
        debug: bool = False,
        **kwargs: Any,
    ):
        # Soporte de compatibilidad: permitir `params={...}` además de kwargs directos
        params = kwargs.get("params")
        if isinstance(params, dict):
            vwap_window = int(params.get("vwap_window", vwap_window))
            z_entry = float(params.get("z_entry", z_entry))
            z_exit = float(params.get("z_exit", z_exit))
            take_profit_pct = float(params.get("take_profit_pct", take_profit_pct))
            stop_loss_pct = float(params.get("stop_loss_pct", stop_loss_pct))
            qty_frac = float(params.get("qty_frac", qty_frac))
            min_vol = float(params.get("min_vol", min_vol))
            warmup = int(params.get("warmup", warmup if warmup is not None else vwap_window))

        # Asignar atributos finales tras fusionar configs
        self.vwap_window: int = int(vwap_window)
        self.z_entry: float = float(z_entry)
        self.z_exit: float = float(z_exit)
        self.take_profit_pct: float = float(take_profit_pct)
        self.stop_loss_pct: float = float(stop_loss_pct)
        # Tamaño histórico (compat) y riesgo porcentual basado en equity (nuevo)
        self.qty_frac: float = float(qty_frac)
        self.risk_pct: float | None = float(risk_pct) if risk_pct is not None else None
        self.min_vol: float = float(min_vol)
        self.warmup: int = int(warmup if warmup is not None else self.vwap_window)
        self.debug: bool = bool(debug)

        # Inicializar buffers usando el vwap_window definitivo
        self._prices: deque[float] = deque(maxlen=self.vwap_window)
        self._vols: deque[float] = deque(maxlen=self.vwap_window)
        self._sum_pv = 0.0
        self._sum_v = 0.0
        self._sum_p = 0.0
        self._sum_p2 = 0.0
        self._n = 0
        self.position = PositionState()

    # ---- utilidades internas ----
    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[VWAPReversion] {msg}")

    def _push(self, price: float, vol: float) -> None:
        if len(self._prices) == self._prices.maxlen:
            old_p = self._prices[0]
            old_v = self._vols[0]
            self._sum_pv -= old_p * old_v
            self._sum_v -= old_v
            self._sum_p -= old_p
            self._sum_p2 -= old_p * old_p
        self._prices.append(price)
        self._vols.append(vol)
        self._sum_pv += price * vol
        self._sum_v += vol
        self._sum_p += price
        self._sum_p2 += price * price

    def _vwap(self) -> float | None:
        if self._sum_v <= self.min_vol:
            return None
        return self._sum_pv / self._sum_v

    def _mean_std(self) -> tuple[float | None, float | None]:
        m = len(self._prices)
        if m < 2:
            return None, None
        mean = self._sum_p / m
        var = max(0.0, (self._sum_p2 / m) - (mean * mean))
        std = var**0.5
        return mean, std if std > 0.0 else None

    def _zscore(self, price: float) -> float | None:
        mean, std = self._mean_std()
        if mean is None or std is None or std <= 0.0:
            return None
        return (price - mean) / std

    def _tp_sl_signal(self, price: float, state: PositionState) -> OrderRequest | None:
        if not state.has_position or state.avg_price <= 0.0:
            return None
        pnl = (price - state.avg_price) / state.avg_price
        if state.side == "SHORT":
            pnl = -pnl
        if pnl >= self.take_profit_pct:
            return OrderRequest(
                decision="CLOSE",
                side="SELL" if state.side == "LONG" else "BUY",
                qty=1.0,
                price=None,
                reason="take_profit",
                meta={"pnl": pnl},
            )
        if pnl <= -self.stop_loss_pct:
            return OrderRequest(
                decision="CLOSE",
                side="SELL" if state.side == "LONG" else "BUY",
                qty=1.0,
                price=None,
                reason="stop_loss",
                meta={"pnl": pnl},
            )
        return None

    # ---- callbacks de Strategy ----
    def on_start(self, context: dict) -> None:
        pass

    def on_bar(self, bar: dict, state: PositionState) -> OrderRequest | None:
        price = float(bar.get("close") or bar.get("price") or 0.0)
        vol = float(bar.get("qty") or bar.get("volume") or 1.0)
        symbol = bar.get("symbol")
        if vol < self.min_vol:
            vol = 1.0

        self._push(price, vol)
        self._n += 1

        if self._n < self.warmup:
            return None

        # TP/SL si hay posición
        tp_sl = self._tp_sl_signal(price, state)
        if tp_sl is not None:
            return tp_sl

        vwap = self._vwap()
        z = self._zscore(price)
        if vwap is None or z is None:
            return None

        if not state.has_position:
            # Para backtests: qty_frac sigue representando fracción 0..1 (compatibilidad)
            if z <= -abs(self.z_entry):
                return OrderRequest(
                    decision="OPEN_LONG",
                    side="BUY",
                    qty=max(0.0, min(1.0, self.qty_frac)),
                    price=None,
                    reason="z_entry_long",
                    meta={"z": z, "vwap": vwap, "price": price},
                    symbol=symbol,
                )
            if z >= abs(self.z_entry):
                return OrderRequest(
                    decision="OPEN_SHORT",
                    side="SELL",
                    qty=max(0.0, min(1.0, self.qty_frac)),
                    price=None,
                    reason="z_entry_short",
                    meta={"z": z, "vwap": vwap, "price": price},
                    symbol=symbol,
                )
            return None

        if abs(z) <= abs(self.z_exit):
            return OrderRequest(
                decision="CLOSE",
                side="SELL" if state.side == "LONG" else "BUY",
                qty=1.0,
                price=None,
                reason="z_exit_close",
                meta={"z": z, "vwap": vwap, "price": price},
                symbol=symbol,
            )

        return None

    def on_end(self, context: dict) -> None:
        pass

    def on_bar_live(self, broker, executor, symbol: str, bar: dict[str, Any]) -> None:
        current_price = float(bar.get("close", 0.0))
        vol = float(bar.get("qty") or bar.get("volume") or 1.0)
        if vol < self.min_vol:
            vol = 1.0
        self._push(current_price, vol)
        self._n += 1

        if self._n < self.warmup:
            self._log(f"Warmup {self._n}/{self.warmup}")
            return

        vwap = self._vwap()
        z = self._zscore(current_price)

        if vwap is None or z is None:
            self._log(f"VWAP/Z-score inválidos: vwap={vwap}, z={z}")
            return

        self._log(
            f"Price=${current_price:.2f} | VWAP=${vwap:.2f} | Z={z:+.2f} | "
            f"InPos={self.position.has_position}"
        )

        # Entrada
        if not self.position.has_position:
            # Sizing live: usar risk_pct si está definido (porcentaje del equity)
            qty_position = max(0.0, min(1.0, self.qty_frac))
            if self.risk_pct is not None and hasattr(broker, "cash"):
                try:
                    cash = float(broker.cash)
                    if current_price > 0:
                        qty_risk = (cash * self.risk_pct) / current_price
                        # Evitar tamaños excesivos por error numérico
                        if qty_risk > 0:
                            qty_position = min(qty_risk, cash / current_price)
                except Exception:
                    pass
            if z <= -abs(self.z_entry):
                self._log(
                    f"ENTRY LONG @ ${current_price:.2f} (z={z:.2f} <= {-abs(self.z_entry):.2f})"
                )
                executor.market_buy(symbol, qty_position)
                self.position.side = "LONG"
                self.position.qty = qty_position
                self.position.entry_price = current_price
                return
            if z >= abs(self.z_entry):
                self._log(
                    f"ENTRY SHORT @ ${current_price:.2f} (z={z:.2f} >= {abs(self.z_entry):.2f})"
                )
                executor.market_sell(symbol, qty_position)
                self.position.side = "SHORT"
                self.position.qty = qty_position
                self.position.entry_price = current_price
                return

        # Salidas (Take-profit / Stop-loss / Reversión banda)
        if (
            self.position.has_position
            and self.position.entry_price
            and self.position.entry_price > 0
        ):
            pnl_pct = (current_price - self.position.entry_price) / self.position.entry_price
            if self.position.side == "SHORT":
                pnl_pct = -pnl_pct

            # TP
            if pnl_pct >= self.take_profit_pct:
                self._log(f"🎯 TAKE PROFIT @ ${current_price:.2f} (pnl={pnl_pct*100:.2f}%)")
                if self.position.side == "LONG":
                    executor.market_sell(symbol, self.position.qty)
                else:
                    executor.market_buy(symbol, self.position.qty)
                self.position.qty = 0.0
                self.position.side = None
                return
            # SL
            if pnl_pct <= -self.stop_loss_pct:
                self._log(f"🛑 STOP LOSS @ ${current_price:.2f} (pnl={pnl_pct*100:.2f}%)")
                if self.position.side == "LONG":
                    executor.market_sell(symbol, self.position.qty)
                else:
                    executor.market_buy(symbol, self.position.qty)
                self.position.qty = 0.0
                self.position.side = None
                return

        # Cierre por vuelta a banda — proteger contra salidas no rentables
        if self.position.has_position and abs(z) <= abs(self.z_exit):
            side = "LONG" if self.position.side == "LONG" else "SHORT"
            qty = abs(self.position.qty)
            if will_exit_non_negative(
                broker,
                entry_side=side,
                entry_price=self.position.entry_price,
                current_price=current_price,
                qty=qty,
            ):
                self._log(f"EXIT {side} @ ${current_price:.2f} (z={z:.2f} revirtió a banda)")
                if self.position.side == "LONG":
                    executor.market_sell(symbol, self.position.qty)
                else:
                    executor.market_buy(symbol, self.position.qty)
                self.position.qty = 0.0
                self.position.side = None
            else:
                self._log(f"⏸️  Skip EXIT {side} por coste (no rentable)")
                return

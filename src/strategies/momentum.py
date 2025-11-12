# src/strategies/momentum.py
from __future__ import annotations

from collections import deque
from typing import Any

from core.costs import CostModel
from strategies.base import (
    Strategy,
    register_strategy,
)


@register_strategy("momentum")
class MomentumStrategy(Strategy):
    """
    Momentum muy ligero sobre la desviación del precio actual respecto a la
    media simple de los últimos `lookback_ticks`.

    NOTA IMPORTANTE (live/paper):
    - Esta implementación usa la firma preferida por el engine:
        on_bar(broker, executor, symbol, bar)
      y ejecuta órdenes vía `executor`, para que el engine actualice Portfolio
      y escriba equity/trades con el pipeline existente.
    """

    name = "momentum"

    def __init__(
        self,
        lookback_ticks: int = 6,
        entry_threshold: float = 5e-5,
        exit_threshold: float = 2e-5,
        qty_frac: float = 0.10,
        debug: bool = False,
        min_edge_bps: float = 0.0,  # Umbral mínimo de edge vs coste (en bps sobre notional)
        cost_model: CostModel | None = None,
        **_: Any,
    ) -> None:
        self.lookback_ticks = int(lookback_ticks)
        self.entry_threshold = float(entry_threshold)
        self.exit_threshold = float(exit_threshold)
        self.qty_frac = float(qty_frac)
        self.debug = bool(debug)
        self.min_edge_bps = float(min_edge_bps)
        self._cost_model: CostModel | None = cost_model

        self._win: deque[float] = deque(maxlen=self.lookback_ticks)
        self._in_pos: bool = False  # estado interno simple, opcional
        self._pos_qty: float = 0.0  # tracking opcional (el portfolio real lo lleva el broker)

    # -------------------------- utilidades internas -------------------------

    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[Momentum] {msg}")

    # --------------------------- firma live/paper ----------------------------

    def on_bar_live(self, broker, executor, symbol: str, bar: dict[str, Any]) -> None:
        """
        El engine live/paper llama primero a esta firma. Aquí debemos:
          1) Actualizar ventana y calcular mom.
          2) Decidir entrada/salida.
          3) Ejecutar vía executor (NO devolver OrderRequest).
        """
        price = float(bar["close"])
        self._win.append(price)

        if len(self._win) < self.lookback_ticks:
            self._log(f"warmup {len(self._win)}/{self.lookback_ticks}, price={price:.2f}")
            return

        mean = sum(self._win) / len(self._win)
        if mean <= 0.0:
            return

        mom = (price - mean) / mean

        # Estado real: usamos broker para cash/posición actual
        try:
            cash: float = float(broker.cash)
        except Exception:
            # fallback razonable si el broker no expone 'cash'
            cash = float(getattr(self, "_available_usdt", 10_000.0))

        try:
            current_qty: float = float(broker.position_qty)
        except Exception:
            current_qty = self._pos_qty  # fallback a nuestro tracking

        # Logs de diagnóstico
        self._log(
            f"price={price:.2f} mean={mean:.2f} mom={mom:+.6f} "
            f"in_pos={self._in_pos} broker_qty={current_qty:.6f} cash={cash:.2f}"
        )

        # ------------------------- Reglas de trading -------------------------

        # Entrada: no en posición y momentum por encima del umbral
        if (not self._in_pos) and (mom > self.entry_threshold):
            notional = max(0.0, cash * self.qty_frac)
            qty = 0.0 if price <= 0.0 else (notional / price)
            if qty > 0.0:
                if self._is_profitable(side="BUY", price=price, qty=qty, mom=mom):
                    self._log(f"ENTRY {symbol} qty={qty:.6f} notional≈{notional:.2f}")
                    executor.market_buy(symbol, qty)
                    self._in_pos = True
                    self._pos_qty = qty  # tracking opcional
                else:
                    self._log("SKIP ENTRY por coste >= edge")

            return  # importante: no seguimos evaluando salida en el mismo tick

        # Salida: en posición y momentum por debajo del umbral (en negativo)
        if self._in_pos and (mom < -self.exit_threshold):
            qty_to_close = current_qty if current_qty > 0.0 else self._pos_qty
            if qty_to_close > 0.0:
                if self._is_profitable(side="SELL", price=price, qty=qty_to_close, mom=-mom):
                    self._log(f"EXIT {symbol} qty={qty_to_close:.6f}")
                    executor.market_sell(symbol, qty_to_close)
                else:
                    self._log("SKIP EXIT por coste >= edge")
            self._in_pos = False
            self._pos_qty = 0.0
            return

        # Si no hay acción, terminamos silenciosamente
        return

    # ------------------- firma opcional para backtests simples ---------------

    def on_bar_bar(self, bar: dict[str, Any]) -> None:
        """
        Para backtests antiguos que esperan on_bar(bar) devolviendo OrderRequest.
        Aquí no devolvemos nada para forzar el uso del pipeline live/paper.
        (Puedes implementar la misma lógica y devolver un OrderRequest si te
        resulta útil en otro runner).
        """
        return None

    # ------------------- coste vs edge ---------------------------------

    def _estimate_cost_abs(self, side: str, price: float, qty: float) -> float:
        cm = self._cost_model
        notional = abs(price * qty)
        if cm is None:
            # Fallback: asumir fee+slip aproximado 8 bps
            return notional * 0.0008
        role = "taker"  # mercado para entries/exits
        side_norm = "buy" if side.upper() == "BUY" else "sell"
        try:
            eff_px = cm.effective_price(base_price=price, side=side_norm, role=role)
            fee = cm.fee_amount(notional=notional, role=role)
        except Exception:
            return notional * 0.0008
        # Slippage abs = |eff_px - price| * qty
        slip_abs = abs(eff_px - price) * qty
        return fee + slip_abs

    def _is_profitable(self, side: str, price: float, qty: float, mom: float) -> bool:
        """Decide si el trade propuesto supera el coste estimado.

        Heurística: edge bruto ≈ |mom| * notional. (mom es desviación relativa).
        Compara edge_abs vs coste_abs y min_edge_bps.

        TEMPORALMENTE DESACTIVADO: Siempre devuelve True para permitir trades.
        """
        # FILTRO DESACTIVADO - Permitir todos los trades
        return True

        # Código original comentado:
        # if qty <= 0 or price <= 0:
        #     return False
        # notional = price * qty
        # edge_abs = abs(mom) * notional
        # cost_abs = self._estimate_cost_abs(side, price, qty)
        # if self.min_edge_bps > 0:
        #     edge_bps = (edge_abs / notional) * 10_000 if notional > 0 else 0.0
        #     if edge_bps < self.min_edge_bps:
        #         return False
        # return edge_abs > cost_abs

    @property
    def cost_model(self) -> CostModel | None:
        return self._cost_model

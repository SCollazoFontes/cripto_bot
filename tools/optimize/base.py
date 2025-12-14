"""
Optimizador base abstracto para estrategias.

Cada estrategia define su propio optimizador heredando de BaseStrategyOptimizer.
La clase base maneja:
- Broker de simulación con costes dinámicos
- Ejecución de backtest sobre ventanas
- Cálculo de métricas (return, sharpe, trades, etc.)
- Logging y persistencia de trials

Cada estrategia solo debe implementar:
- search_space: dict con los parámetros optimizables
- create_strategy: factory que construye la estrategia con params dados
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd

from bars.base import Bar, Trade
from bars.builders import CompositeBarBuilder
from strategies.base import Strategy
from tools.optimize.runner import TrialResult


@dataclass
class BrokerConfig:
    """Configuración del broker para simulación."""

    fees_bps: float = 10.0  # Maker/taker fees (10 bps = 0.1%)
    slip_bps: float = 5.0  # Slippage base (5 bps = 0.05%)
    starting_cash: float = 100.0  # Capital inicial
    use_dynamic_slip: bool = True  # Activar slippage dinámico (vol + size)


class SimulatedBroker:
    """
    Broker mínimo para backtests de optimización.

    Características:
    - Costes dinámicos: fees + slippage que aumenta con volatilidad y tamaño
    - No order book, ejecución inmediata al precio de barra
    - Tracking de PnL, fees, posiciones
    """

    def __init__(self, cfg: BrokerConfig) -> None:
        self.cfg = cfg
        self.cash: float = cfg.starting_cash
        self.position_qty: float = 0.0
        self.avg_price: float = 0.0
        self.fees_paid: float = 0.0
        self._ctx_volatility: float = 0.0

    def set_context(self, *, volatility: float | None = None) -> None:
        """Actualiza contexto de mercado (volatilidad) para slippage dinámico."""
        if volatility is not None:
            self._ctx_volatility = max(0.0, float(volatility))

    def _apply_slippage(self, price: float, side: str, qty: float) -> float:
        """Calcula precio efectivo con slippage dinámico."""
        base_rate = max(0.0, self.cfg.slip_bps) / 10_000.0

        if self.cfg.use_dynamic_slip:
            # Componente por volatilidad (más vol = más slip)
            alpha = 1.5
            vol_term = alpha * self._ctx_volatility

            # Componente por tamaño de orden (más notional = más impacto)
            beta = 2e-5
            notional = price * max(0.0, qty)
            size_term = beta * min(5.0, notional / 10_000.0)  # cap en 5x

            dyn_rate = base_rate + vol_term + size_term
            rate = min(dyn_rate, 0.008)  # cap en 80 bps
        else:
            rate = base_rate

        if side.upper() == "BUY":
            return price * (1.0 + rate)
        return price * (1.0 - rate)

    def _fee(self, notional: float) -> float:
        """Calcula fee sobre notional."""
        return abs(notional) * (max(0.0, self.cfg.fees_bps) / 10_000.0)

    def submit_order(
        self, symbol: str, side: str, qty: float, price: float, reason: str = ""
    ) -> tuple[float, float, float]:
        """
        Envía orden y devuelve (qty_ejecutada, precio_efectivo, fee).

        Args:
            symbol: Símbolo (no usado en sim, solo firma compatible)
            side: "BUY" o "SELL"
            qty: Cantidad a operar
            price: Precio base (mid de barra)
            reason: Razón de la orden (logging)

        Returns:
            (qty_ejec, precio_ejec, fee)
        """
        _ = symbol, reason  # no-op
        if qty <= 0.0 or price <= 0.0:
            return 0.0, 0.0, 0.0

        side = side.upper()
        eff_price = self._apply_slippage(price, side, qty)
        fee = self._fee(eff_price * qty)

        if side == "BUY":
            cost = eff_price * qty + fee
            if cost > self.cash:
                # Ajustar qty al cash disponible
                qty = max(0.0, (self.cash - fee) / eff_price)
                cost = eff_price * qty + fee
            if qty <= 0:
                return 0.0, 0.0, 0.0

            total_qty = self.position_qty + qty
            if total_qty > 0:
                self.avg_price = (self.avg_price * self.position_qty + eff_price * qty) / total_qty
            self.position_qty = total_qty
            self.cash -= cost
            self.fees_paid += fee
            return qty, eff_price, fee

        else:  # SELL
            qty = min(qty, self.position_qty)
            if qty <= 0:
                return 0.0, 0.0, 0.0

            proceeds = eff_price * qty - fee
            self.position_qty -= qty
            self.cash += proceeds
            self.fees_paid += fee

            if self.position_qty <= 1e-9:
                self.position_qty = 0.0
                self.avg_price = 0.0

            return qty, eff_price, fee

    def nav(self, mark_price: float) -> float:
        """Net Asset Value: cash + posición valorada a mark_price."""
        return self.cash + self.position_qty * mark_price


class BaseStrategyOptimizer(ABC):
    """
    Clase base abstracta para optimizadores de estrategia.

    Cada estrategia implementa:
    - search_space: dict con definición de parámetros optimizables
    - create_strategy: factory que construye Strategy dado params dict
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        broker_config: BrokerConfig | None = None,
    ):
        self.symbol = symbol
        self.broker_config = broker_config or BrokerConfig()

    @property
    @abstractmethod
    def search_space(self) -> dict[str, Any]:
        """
        Define el espacio de búsqueda para optimización.

        Ejemplo:
            {
                "lookback_ticks": Integer(min=20, max=80, step=10),
                "entry_threshold": StepContinuous(min=0.0008, max=0.003, step=0.0001),
                ...
            }
        """

    @abstractmethod
    def create_strategy(self, params: dict[str, Any]) -> Strategy:
        """
        Factory para crear instancia de Strategy con params dados.

        Args:
            params: dict con valores de parámetros optimizables

        Returns:
            Instancia de Strategy configurada
        """

    def evaluate(
        self,
        params: dict[str, Any],
        trades_df: pd.DataFrame,
        builder_config: dict[str, Any],
        min_trades: int = 3,
    ) -> TrialResult:
        """
        Evalúa un conjunto de parámetros ejecutando backtest completo.

        Args:
            params: Parámetros de la estrategia
            trades_df: DataFrame con trades históricos
            builder_config: Config del bar builder (tipo, thresholds, etc)
            min_trades: Mínimo de trades para considerar resultado válido

        Returns:
            TrialResult con métricas y resultado de evaluación
        """
        # Crear broker y estrategia
        broker = SimulatedBroker(self.broker_config)
        strategy = self.create_strategy(params)

        # Crear bar builder
        builder = CompositeBarBuilder.from_dict(builder_config)

        # Estado
        bars_list: list[Bar] = []
        equity_series: list[tuple[float, float]] = []  # (timestamp, nav)
        trade_log: list[dict] = []
        bar_count = 0

        # Procesar trades
        for _, row in trades_df.iterrows():
            trade = Trade(
                timestamp=float(row["timestamp"]),
                price=float(row["price"]),
                qty=float(row["qty"]),
                is_buyer_maker=bool(row["is_buyer_maker"]),
            )

            # Actualizar builder
            bar = builder.update(trade)
            if bar is None:
                continue

            bar_count += 1
            bars_list.append(bar)

            # Actualizar contexto de volatilidad para broker
            if hasattr(strategy, "volatility") and strategy.volatility is not None:
                broker.set_context(volatility=strategy.volatility)

            # Calcular señal
            decision = strategy.on_bar(bar)

            # Ejecutar decisión
            if decision.action == "buy" and broker.position_qty == 0:
                qty = decision.quantity or 0.01
                qty_exec, price_exec, fee = broker.submit_order(
                    self.symbol, "BUY", qty, bar.close, decision.reason or "entry"
                )
                if qty_exec > 0:
                    trade_log.append(
                        {
                            "bar_idx": bar_count,
                            "timestamp": bar.t_close,
                            "side": "BUY",
                            "qty": qty_exec,
                            "price": price_exec,
                            "fee": fee,
                            "reason": decision.reason or "entry",
                        }
                    )

            elif decision.action == "sell" and broker.position_qty > 0:
                qty = broker.position_qty
                qty_exec, price_exec, fee = broker.submit_order(
                    self.symbol, "SELL", qty, bar.close, decision.reason or "exit"
                )
                if qty_exec > 0:
                    trade_log.append(
                        {
                            "bar_idx": bar_count,
                            "timestamp": bar.t_close,
                            "side": "SELL",
                            "qty": qty_exec,
                            "price": price_exec,
                            "fee": fee,
                            "reason": decision.reason or "exit",
                        }
                    )

            # Registrar equity
            nav = broker.nav(bar.close)
            equity_series.append((bar.t_close, nav))

        # Liquidar posición final si existe
        if broker.position_qty > 0 and bars_list:
            last_bar = bars_list[-1]
            qty_exec, price_exec, fee = broker.submit_order(
                self.symbol, "SELL", broker.position_qty, last_bar.close, "final_liquidation"
            )
            if qty_exec > 0:
                trade_log.append(
                    {
                        "bar_idx": bar_count,
                        "timestamp": last_bar.t_close,
                        "side": "SELL",
                        "qty": qty_exec,
                        "price": price_exec,
                        "fee": fee,
                        "reason": "final_liquidation",
                    }
                )
                nav = broker.nav(last_bar.close)
                equity_series.append((last_bar.t_close, nav))

        # Calcular métricas
        metrics = self._compute_metrics(
            equity_series=equity_series,
            trade_log=trade_log,
            starting_cash=self.broker_config.starting_cash,
            fees_paid=broker.fees_paid,
        )

        # Validar mínimo de trades
        n_trades = metrics.get("trades", 0)
        if n_trades < min_trades:
            return TrialResult(
                params=params,
                score=-1_000_000.0,  # Penalización
                metrics=metrics,
                status="insufficient_trades",
            )

        # Score: retorno total (simple pero efectivo)
        score = metrics.get("total_return", -1.0)

        return TrialResult(
            params=params,
            score=score,
            metrics=metrics,
            status="ok",
        )

    def _compute_metrics(
        self,
        equity_series: list[tuple[float, float]],
        trade_log: list[dict],
        starting_cash: float,
        fees_paid: float,
    ) -> dict[str, Any]:
        """Calcula métricas de performance del backtest."""
        if not equity_series:
            return {
                "total_return": -1.0,
                "trades": 0,
                "fees_paid": fees_paid,
                "final_nav": starting_cash,
            }

        # Equity curve
        timestamps, navs = zip(*equity_series)
        final_nav = navs[-1]
        total_return = (final_nav / starting_cash) - 1.0

        # Trades
        n_trades = len([t for t in trade_log if t["side"] == "SELL"])

        # Win rate (FIFO pairing)
        wins = 0
        losses = 0
        buys = [t for t in trade_log if t["side"] == "BUY"]
        sells = [t for t in trade_log if t["side"] == "SELL"]

        for buy, sell in zip(buys, sells):
            pnl = (sell["price"] - buy["price"]) * buy["qty"] - buy["fee"] - sell["fee"]
            if pnl > 0:
                wins += 1
            else:
                losses += 1

        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0

        # Max drawdown
        max_dd = 0.0
        peak = navs[0]
        for nav in navs:
            if nav > peak:
                peak = nav
            dd = (nav - peak) / peak if peak > 0 else 0.0
            if dd < max_dd:
                max_dd = dd

        return {
            "total_return": total_return,
            "trades": n_trades,
            "win_rate": win_rate,
            "max_drawdown": max_dd,
            "fees_paid": fees_paid,
            "final_nav": final_nav,
        }

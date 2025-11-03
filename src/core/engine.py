# ============================================================
# src/core/engine.py — Motor principal (v0.3.1)
# ------------------------------------------------------------
# Cambios vs v0.3:
#   - Resolver E501 (líneas >100) dividiendo logs/llamadas.
#   - Guardas adicionales para mypy con Optionals.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from time import sleep

from loguru import logger

from src.core.broker import OrderRequest, Side
from src.core.broker_sim import SimBroker
from src.core.config_loader import get_config
from src.core.logger_config import init_logger
from src.strategies.base import Strategy
from src.strategies.momentum import MomentumStrategy


# -----------------------------
# Feed de precios mínimo (toy)
# -----------------------------
@dataclass
class MiniPriceFeed:
    price: float
    up_tick: float = 10.0
    down_tick: float = 5.0
    step: int = 0

    def next_price(self) -> float:
        self.step += 1
        if self.step % 3 == 0:
            self.price = max(0.01, self.price - self.down_tick)
        else:
            self.price = self.price + self.up_tick
        return self.price


class Engine:
    def __init__(self) -> None:
        # 1) Logger
        init_logger()

        # 2) Config
        cfg = get_config()

        env_cfg = cfg.get("environment", {})
        trd_cfg = cfg.get("trading", {})
        strat_cfg = cfg.get("strategy", {})

        self.use_testnet: bool = bool(env_cfg.get("use_testnet", True))
        self.symbol: str = str(trd_cfg.get("symbol", "BTCUSDT"))
        self.cycle_delay: float = float(trd_cfg.get("cycle_delay", 1.0))

        # parámetros de sizing y costes
        self.min_notional: float = float(trd_cfg.get("min_notional", 5.0))
        self.max_position_usd: float = float(trd_cfg.get("max_position_usd", 200.0))
        fees_bps: float = float(trd_cfg.get("trade_fee_bps", 2.0))
        slip_bps: float = float(trd_cfg.get("slippage_bps", 5.0))

        # parámetros utilitarios
        self.max_cycles: int = int(trd_cfg.get("max_cycles", 12))
        price0 = float(trd_cfg.get("price0", 60_000.0))

        logger.info("Inicializando motor de trading...")
        logger.debug(f"Modo testnet: {self.use_testnet}")

        logger.debug(
            "Params trading: min_notional=%s, max_position_usd=%s, fee_bps=%s, slip_bps=%s",
            self.min_notional,
            self.max_position_usd,
            fees_bps,
            slip_bps,
        )

        # 3) Broker simulado (usa fees/slippage de trading.*)
        self.broker = SimBroker(
            starting_cash=float(trd_cfg.get("starting_cash", 10_000.0)),
            fees_bps=fees_bps,
            slip_bps=slip_bps,
            allow_short=bool(trd_cfg.get("allow_short", False)),
        )

        # 4) Feed de precios toy
        self.feed = MiniPriceFeed(price=price0)

        # 5) Estrategia desde config
        self.strategy = self._build_strategy(strat_cfg)

        self.is_running: bool = False
        logger.debug("Engine listo (SimBroker + MiniPriceFeed + Strategy).")

    # -----------------------------
    # Construcción de la estrategia
    # -----------------------------
    def _build_strategy(self, strat_cfg: dict) -> Strategy:
        name = str(strat_cfg.get("name", "momentum")).lower()
        if name == "momentum":
            return MomentumStrategy(
                lookback_ticks=int(strat_cfg.get("lookback_ticks", 50)),
                entry_threshold=float(strat_cfg.get("entry_threshold", 0.001)),
                exit_threshold=float(strat_cfg.get("exit_threshold", 0.0005)),
                stop_loss_pct=float(strat_cfg.get("stop_loss_pct", 0.002)),
                take_profit_pct=float(strat_cfg.get("take_profit_pct", 0.004)),
            )
        raise ValueError(f"Estrategia no soportada: {name}")

    # -----------------------------
    # Helpers de sizing / límites
    # -----------------------------
    def _calc_min_qty(self, price: float) -> float:
        qty = self.min_notional / max(price, 1e-9)
        return max(qty, 0.0)

    def _can_add_position(self, price: float, add_qty: float) -> bool:
        views = self.broker.positions()
        pos_view = views.get(self.symbol)
        current_qty = pos_view.qty if pos_view is not None else 0.0
        future_usd = (current_qty + add_qty) * price
        return future_usd <= self.max_position_usd + 1e-9

    # -----------------------------
    # Bucle principal
    # -----------------------------
    def run(self) -> None:
        logger.info("Ejecutando ciclo principal...")
        self.is_running = True
        cycle = 0

        try:
            while self.is_running:
                cycle += 1
                price_ref = self.feed.next_price()

                # Estado actual
                pos_view = self.broker.positions().get(self.symbol)
                pos_qty = pos_view.qty if pos_view is not None else 0.0
                pos_avg = pos_view.avg_price if pos_view is not None else 0.0

                # Señal de la estrategia
                sig = self.strategy.on_price(price_ref, None, pos_qty, pos_avg)

                # Cálculo de qty mínima por notional y control de límites
                min_qty = self._calc_min_qty(price_ref)

                if sig.name == "BUY":
                    if min_qty > 0.0 and self._can_add_position(price_ref, min_qty):
                        rep = self.broker.submit_market(
                            OrderRequest(
                                symbol=self.symbol,
                                side=Side.BUY,
                                qty=min_qty,
                                price_ref=price_ref,
                            )
                        )
                        logger.info(
                            "EXEC BUY %.6f %s @ %.2f (fee=%.6f)",
                            rep.qty,
                            rep.symbol,
                            rep.exec_price,
                            rep.fee,
                        )
                    else:
                        logger.debug("BUY bloqueado por límites (min_notional o max_position_usd).")

                elif sig.name == "SELL":
                    sell_qty = min(min_qty, pos_qty)
                    if sell_qty > 0.0:
                        rep = self.broker.submit_market(
                            OrderRequest(
                                symbol=self.symbol,
                                side=Side.SELL,
                                qty=sell_qty,
                                price_ref=price_ref,
                            )
                        )
                        logger.info(
                            "EXEC SELL %.6f %s @ %.2f (fee=%.6f)",
                            rep.qty,
                            rep.symbol,
                            rep.exec_price,
                            rep.fee,
                        )
                    else:
                        logger.debug("SELL ignorado: no hay posición suficiente para reducir.")

                else:
                    logger.debug("HOLD")

                # MTM y logs de estado
                equity = self.broker.equity(marks={self.symbol: price_ref})
                pos_view = self.broker.positions().get(self.symbol)

                if pos_view is not None:
                    logger.debug(
                        "[ciclo %d] price=%.2f cash=%.2f equity=%.2f pos "
                        "qty=%.6f avg=%.2f realized=%.2f fees=%.4f",
                        cycle,
                        price_ref,
                        self.broker.cash(),
                        equity,
                        pos_view.qty,
                        pos_view.avg_price,
                        pos_view.realized_pnl,
                        pos_view.fees_paid,
                    )
                else:
                    logger.debug(
                        "[ciclo %d] price=%.2f cash=%.2f equity=%.2f pos (sin posición)",
                        cycle,
                        price_ref,
                        self.broker.cash(),
                        equity,
                    )

                if cycle >= self.max_cycles:
                    logger.info(
                        "Simulación terminada (%d ciclos completados).",
                        self.max_cycles,
                    )
                    self.is_running = False

                sleep(self.cycle_delay)

        except KeyboardInterrupt:
            logger.warning("Ejecución interrumpida manualmente (Ctrl+C).")
            self.is_running = False
        except Exception as e:
            logger.exception(f"Error en el motor: {e}")
            self.is_running = False
        finally:
            final_mark = self.feed.price
            equity_final = self.broker.equity(marks={self.symbol: final_mark})
            logger.info(
                "Motor detenido. Equity final (mark @ %.2f): %.2f",
                final_mark,
                equity_final,
            )
            self.broker.shutdown()


if __name__ == "__main__":
    engine = Engine()
    engine.run()

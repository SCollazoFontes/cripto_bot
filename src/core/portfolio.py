# ============================================================
# src/core/portfolio.py — Gestión de posiciones, órdenes y PnL
# ------------------------------------------------------------
# - Soporta órdenes MARKET (buy/sell) con slippage y fees en bps
# - Mantiene posiciones por símbolo (qty, avg_price)
# - Calcula PnL realizado y no realizado (mark_to_market)
# - Registro sencillo de trades/fills para reporting posterior
# - No permite cortos por simplicidad (qty >= 0)
# - Integrado con logger central (loguru)
#
# Nota (mypy/ruff):
# - snapshot() devuelve un dict heterogéneo -> se tipa como Dict[str, Any]
# - Se evitan E501 partiendo logs largos.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.logger_config import init_logger


# -----------------------------
# Tipos básicos y estructuras
# -----------------------------
class Side(Enum):
    BUY = auto()
    SELL = auto()


@dataclass
class Order:
    symbol: str
    side: Side
    qty: float  # cantidad base (p.ej., BTC)
    price_ref: float  # precio de referencia (mid/último) para simular exec
    ts_ms: Optional[int] = None  # marca de tiempo opcional
    id: int = 0  # asignado por Portfolio


@dataclass
class Fill:
    order_id: int
    symbol: str
    side: Side
    qty: float
    price: float  # precio ejecutado (con slippage)
    fee: float  # comisiones pagadas en "cash" (p.ej., USDT)
    notional: float  # qty * price
    ts_ms: Optional[int] = None


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0  # precio medio ponderado de la posición abierta
    realized_pnl: float = 0.0  # PnL realizado acumulado (en cash)
    fees_paid: float = 0.0  # fees acumuladas (en cash)

    def __repr__(self) -> str:
        return (
            f"Position(symbol={self.symbol}, qty={self.qty:.8f}, "
            f"avg_price={self.avg_price:.2f}, realized_pnl={self.realized_pnl:.2f}, "
            f"fees_paid={self.fees_paid:.2f})"
        )


# -----------------------------
# Clase principal Portfolio
# -----------------------------
class Portfolio:
    """
    Cartera con cash (moneda de liquidación, p.ej., USDT) y posiciones por símbolo.
    Reglas:
      - Solo órdenes MARKET simuladas con precio de referencia + slippage.
      - Fees en bps sobre notional.
      - No se permiten cortos (qty neta nunca < 0).
    """

    _order_seq: int

    def __init__(
        self,
        starting_cash: float,
        fees_bps: float = 2.5,  # 2.5 bps = 0.025%
        slip_bps: float = 1.0,  # 1 bps = 0.01%
        allow_short: bool = False,  # por defecto, no cortos
    ) -> None:
        self.cash: float = float(starting_cash)
        self.fees_bps: float = float(fees_bps)
        self.slip_bps: float = float(slip_bps)
        self.allow_short: bool = bool(allow_short)

        self.positions: Dict[str, Position] = {}
        self.fills: List[Fill] = []
        self._order_seq = 0

        logger.debug(
            "Portfolio creado: cash=%.2f, fees_bps=%.2f, slip_bps=%.2f, allow_short=%s",
            self.cash,
            self.fees_bps,
            self.slip_bps,
            self.allow_short,
        )

    # -------- Utilidades internas --------
    def _next_order_id(self) -> int:
        self._order_seq += 1
        return self._order_seq

    def _exec_price(self, side: Side, price_ref: float) -> float:
        """Aplica slippage en bps al precio de referencia."""
        bps = self.slip_bps / 10_000.0
        if side is Side.BUY:
            return price_ref * (1.0 + bps)
        return price_ref * (1.0 - bps)

    def _fee_cash(self, notional: float) -> float:
        """Calcula fees en la divisa de liquidación (cash)."""
        return abs(notional) * (self.fees_bps / 10_000.0)

    def _get_pos(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    # -------- API pública --------
    def submit_market_order(
        self, symbol: str, side: Side, qty: float, price_ref: float, ts_ms: Optional[int] = None
    ) -> Fill:
        """
        Ejecuta una orden MARKET contra price_ref aplicando slippage/fees.
        - No permite que la posición neta quede negativa si allow_short=False.
        """
        assert qty > 0.0, "qty debe ser > 0"
        assert price_ref > 0.0, "price_ref debe ser > 0"

        pos = self._get_pos(symbol)
        order_id = self._next_order_id()

        # Si no permitimos cortos, chequear que SELL no excede qty disponible
        if not self.allow_short and side is Side.SELL and qty > pos.qty + 1e-12:
            raise ValueError(
                f"No se permiten cortos: intentar vender {qty} {symbol} con posición {pos.qty}"
            )

        px = self._exec_price(side, price_ref)
        notional = qty * px
        fee = self._fee_cash(notional)

        # Efectos en cash y posición
        if side is Side.BUY:
            # cash disminuye por notional + fee
            self.cash -= notional + fee
            # nuevo avg price ponderado
            new_qty = pos.qty + qty
            if new_qty <= 1e-12:
                # borde numérico
                pos.avg_price = 0.0
            else:
                pos.avg_price = (pos.avg_price * pos.qty + px * qty) / new_qty
            pos.qty = new_qty

        else:  # SELL
            # reducir posición y sumar cash por venta menos fee
            self.cash += notional - fee
            realized = (px - pos.avg_price) * qty
            pos.realized_pnl += realized
            pos.qty -= qty
            if pos.qty <= 1e-12:
                # cerramos posición → reset avg_price
                pos.qty = 0.0
                pos.avg_price = 0.0

        pos.fees_paid += fee

        fill = Fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=px,
            fee=fee,
            notional=notional,
            ts_ms=ts_ms,
        )
        self.fills.append(fill)

        logger.debug(
            "FILL o#%d %s %.8f %s @ %.2f (notional=%.2f, fee=%.6f) | cash=%.2f | %s",
            order_id,
            side.name,
            qty,
            symbol,
            px,
            notional,
            fee,
            self.cash,
            self._get_pos(symbol),
        )
        return fill

    def position_unrealized(self, symbol: str, mark_price: float) -> float:
        """
        PnL no realizado de la posición abierta usando mark_price.
        """
        pos = self._get_pos(symbol)
        if pos.qty <= 0.0:
            return 0.0
        return (mark_price - pos.avg_price) * pos.qty

    def equity(self, marks: Optional[Dict[str, float]] = None) -> float:
        """
        Devuelve equity total = cash + sum(posición_mark_to_market).
        Si marks es None, solo devuelve cash (útil para pruebas).
        """
        eq = self.cash
        if marks:
            for sym, mp in marks.items():
                pos = self._get_pos(sym)
                if pos.qty > 0.0:
                    eq += (mp - pos.avg_price) * pos.qty
        return eq

    def snapshot(self, marks: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """
        Devuelve un resumen serializable del estado: cash, por símbolo (qty, avg, realized, fees) y equity.
        Estructura:
          {
            "cash": float,
            "positions": {symbol: {"qty": float, "avg_price": float, "realized_pnl": float, "fees_paid": float}},
            "equity": float
          }
        """
        positions_dict: Dict[str, Dict[str, float]] = {}
        for sym, pos in self.positions.items():
            positions_dict[sym] = {
                "qty": round(pos.qty, 8),
                "avg_price": round(pos.avg_price, 8),
                "realized_pnl": round(pos.realized_pnl, 8),
                "fees_paid": round(pos.fees_paid, 8),
            }

        data: Dict[str, Any] = {
            "cash": round(self.cash, 8),
            "positions": positions_dict,
            "equity": round(self.equity(marks), 8) if marks else round(self.cash, 8),
        }
        return data


# -----------------------------
# Self-test rápido (ejecución)
# -----------------------------
def _self_test() -> None:
    """
    Prueba mínima:
      1) Cash 10_000 USDT, fees=2.5 bps, slip=1 bps
      2) BUY 0.01 BTC @ 60_000 ref → ejecuta con slippage
      3) Mark @ 61_000 → PnL no realizado > 0
      4) SELL 0.01 @ 61_000 → PnL realizado > 0 y equity ≈ inicial + beneficio - fees
    """
    init_logger()
    logger.info("Iniciando self-test de Portfolio...")

    pf = Portfolio(starting_cash=10_000.0, fees_bps=2.5, slip_bps=1.0, allow_short=False)

    # 1) BUY
    pf.submit_market_order(symbol="BTCUSDT", side=Side.BUY, qty=0.01, price_ref=60_000.0)
    snap1 = pf.snapshot(marks={"BTCUSDT": 60_000.0})
    logger.info(f"Después del BUY: {snap1}")

    # 2) mark a 61k
    u_pnl = pf.position_unrealized("BTCUSDT", mark_price=61_000.0)
    logger.info(f"Unrealized PnL a 61,000: {u_pnl:.4f}")
    assert u_pnl > 0, "El PnL no realizado debería ser positivo"

    # 3) SELL todo
    pos_qty = pf.positions["BTCUSDT"].qty
    pf.submit_market_order(symbol="BTCUSDT", side=Side.SELL, qty=pos_qty, price_ref=61_000.0)
    snap2 = pf.snapshot(marks={"BTCUSDT": 61_000.0})
    logger.info(f"Después del SELL: {snap2}")

    # Equity final > equity inicial (10_000) menos fees totales
    assert pf.cash > 10_000.0 - 1.0, "La cash final debería estar cerca de inicial + beneficio neto"
    logger.info("Self-test OK ✅")


if __name__ == "__main__":
    _self_test()

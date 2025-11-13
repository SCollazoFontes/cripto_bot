#!/usr/bin/env python3
# tools/run_live_binance.py
"""
Trading en vivo con datos reales de Binance (testnet o mainnet).
Conecta al WebSocket de Binance, procesa trades en tiempo real,
construye micro-velas y ejecuta la estrategia.

Uso:
    source activate.sh
    python -m tools.run_live_binance \
        --run-dir runs/$(date -u +%Y%m%dT%H%M%SZ)_live \
        --symbol BTCUSDT \
        --testnet \
        --duration 600 \
        --cash 10000 \
        --fees-bps 2.5 \
        --slip-bps 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import UTC, datetime
import json
import pathlib
import time

from bars.base import Trade
from bars.volume_qty import VolumeQtyBarBuilder
from brokers.base import OrderRequest
from brokers.binance_paper import BinancePaperBroker
from core.metrics import calculate_all_metrics
from core.spread_tracker import SpreadTracker
from data.feeds.binance_trades import iter_trades
from strategies.base import get_strategy_class


# Executor simple para ejecutar órdenes
class SimpleExecutor:
    """Executor mínimo que envuelve al broker para órdenes de mercado."""

    def __init__(self, broker: BinancePaperBroker):
        self.broker = broker
        self.orders_executed: list[dict] = []

    def market_buy(self, symbol: str, qty: float) -> None:
        """Ejecuta una orden de compra de mercado."""
        try:
            req = OrderRequest(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                quantity=qty,
            )
            order = self.broker.submit_order(req)
            if order.status == "FILLED":
                self.orders_executed.append(
                    {
                        "symbol": symbol,
                        "side": "BUY",
                        "qty": order.filled_qty,
                        "price": (
                            order.fills[0].price if (order.fills and len(order.fills) > 0) else 0.0
                        ),
                        "fee": sum(f.commission for f in order.fills),
                    }
                )
        except Exception as e:
            print(f"❌ Error en market_buy: {e}")
            import traceback

            traceback.print_exc()

    def market_sell(self, symbol: str, qty: float) -> None:
        """Ejecuta una orden de venta de mercado."""
        try:
            req = OrderRequest(
                symbol=symbol,
                side="SELL",
                order_type="MARKET",
                quantity=qty,
            )
            order = self.broker.submit_order(req)
            if order.status == "FILLED":
                self.orders_executed.append(
                    {
                        "symbol": symbol,
                        "side": "SELL",
                        "qty": order.filled_qty,
                        "price": (
                            order.fills[0].price if (order.fills and len(order.fills) > 0) else 0.0
                        ),
                        "fee": sum(f.commission for f in order.fills),
                    }
                )
        except Exception as e:
            print(f"❌ Error en market_sell: {e}")
            import traceback

            traceback.print_exc()


def save_manifest(run_dir: pathlib.Path, args: argparse.Namespace, started_ts: float) -> None:
    """Guarda metadata de la ejecución."""
    manifest = {
        "started_at": datetime.fromtimestamp(started_ts, tz=UTC).isoformat(),
        "symbol": args.symbol,
        "testnet": args.testnet,
        "duration": args.duration,
        "cash": args.cash,
        "fees_bps": args.fees_bps,
        "slip_bps": args.slip_bps,
        "strategy": args.strategy,
        "strategy_params": args.params,
    }
    with (run_dir / "manifest.json").open("w") as f:
        json.dump(manifest, f, indent=2)


def write_equity_csv(run_dir: pathlib.Path, rows: list[tuple]) -> None:
    """Guarda equity.csv con formato: ts, symbol, close, pos_qty, cash_usdt, equity_usdt."""
    with (run_dir / "equity.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "symbol", "close", "pos_qty", "cash_usdt", "equity_usdt"])
        writer.writerows(rows)


def write_trades_csv(run_dir: pathlib.Path, trades: list[dict]) -> None:
    """Guarda trades ejecutados con estructura: timestamp, side, price, qty, cash, equity, reason."""
    with (run_dir / "trades.csv").open("w", newline="") as f:
        fieldnames = ["timestamp", "side", "price", "qty", "cash", "equity", "reason"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if trades:
            writer.writerows(trades)


def write_data_csv(run_dir: pathlib.Path, bars: list[dict]) -> None:
    """Guarda datos de barras con estructura OHLCV: timestamp, open, high, low, close, volume."""
    with (run_dir / "data.csv").open("w", newline="") as f:
        fieldnames = ["timestamp", "open", "high", "low", "close", "volume"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if bars:
            writer.writerows(bars)


def write_decisions_csv(run_dir: pathlib.Path, decisions: list[dict]) -> None:
    """Guarda decisiones de estrategia."""
    if not decisions:
        return
    with (run_dir / "decisions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=decisions[0].keys())
        writer.writeheader()
        writer.writerows(decisions)


def write_summary(run_dir: pathlib.Path, stats: dict) -> None:
    """Guarda resumen de ejecución."""
    with (run_dir / "summary.json").open("w") as f:
        json.dump(stats, f, indent=2)


def write_returns_csv(run_dir: pathlib.Path, equity_rows: list[tuple]) -> None:
    """Escribe returns.csv con retornos por barra.

    equity_rows formato: (timestamp, symbol, price, qty, cash, equity)
    """
    if not equity_rows:
        return

    with (run_dir / "returns.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "equity", "return_pct", "cumulative_return_pct"])

        cumulative_return = 0.0
        for i, row in enumerate(equity_rows):
            equity = row[5]  # equity está en índice 5
            if i == 0:
                return_pct = 0.0
            else:
                prev_equity = equity_rows[i - 1][5]
                return_pct = (
                    ((equity - prev_equity) / prev_equity * 100) if prev_equity > 0 else 0.0
                )
                cumulative_return += return_pct

            writer.writerow(
                [
                    row[0],  # timestamp
                    f"{equity:.2f}",
                    f"{return_pct:.4f}",
                    f"{cumulative_return:.4f}",
                ]
            )


async def run_live_trading(
    symbol: str,
    run_dir: pathlib.Path,
    duration: int,
    cash: float,
    fees_bps: float,
    slip_bps: float | None,
    testnet: bool,
    strategy_name: str | None,
    strategy_params: str | None,
) -> None:
    """
    Ejecuta trading en vivo durante `duration` segundos.
    """
    # Inicializar SpreadTracker si slip_bps es None
    spread_tracker: SpreadTracker | None = None
    if slip_bps is None:
        spread_tracker = SpreadTracker(symbol=symbol, window_size=100, testnet=testnet)
        # Iniciar en background
        loop = asyncio.get_event_loop()
        spread_tracker.start_background(loop)
        print("[run_live] 📊 Slippage dinámico activado (basado en spread)", flush=True)
        # Esperar un momento para recolectar datos iniciales
        await asyncio.sleep(2)

    # Configuración de ejecución con fees y slippage
    from brokers.binance_paper import _ExecCfg

    # Si slip_bps es None, calcular dinámicamente desde el spread
    if slip_bps is None and spread_tracker:
        # Usar spread promedio inicial como fallback
        await asyncio.sleep(1)  # dar tiempo a recolectar samples
        initial_spread = spread_tracker.get_spread()
        effective_slip_pct = (initial_spread * 0.5) / 10000.0 if initial_spread > 0 else 0.0005
    else:
        effective_slip_pct = slip_bps / 10000.0 if slip_bps is not None else 0.0005

    exec_cfg = _ExecCfg(
        fee_pct=fees_bps / 10000.0,
        slip_pct=effective_slip_pct,
    )

    # Inicializar broker paper
    broker = BinancePaperBroker(exec_cfg=exec_cfg)
    # Configurar cash inicial manualmente
    broker._usdt = cash

    # Función helper para actualizar slippage dinámicamente
    def get_dynamic_slip_pct() -> float:
        if spread_tracker:
            spread_bps = spread_tracker.get_spread()
            # Si el spread es 0 o muy pequeño, usar valor conservador de 5 bps
            if spread_bps < 0.5:
                return 0.0005  # 5 bps default
            return (spread_bps * 0.5) / 10000.0
        return exec_cfg.slip_pct

    # Patchear el broker para usar slippage dinámico
    if spread_tracker:

        def dynamic_slippage(price: float, side: str) -> float:
            slip_pct = get_dynamic_slip_pct()
            if side.upper() == "BUY":
                return price * (1.0 + slip_pct)
            else:
                return price * (1.0 - slip_pct)

        broker._apply_slippage = dynamic_slippage  # type: ignore[assignment]

    # Crear executor
    executor = SimpleExecutor(broker)

    # Cargar estrategia
    strategy = None
    if strategy_name:
        try:
            cls = get_strategy_class(strategy_name)
            params = json.loads(strategy_params) if strategy_params else {}
            strategy = cls(**params)
            print(f"✅ Estrategia cargada: {strategy_name}")
        except Exception as e:
            print(f"⚠️  No se pudo cargar estrategia '{strategy_name}': {e}")
            import traceback

            traceback.print_exc()

    # Builder de micro-velas (volumen más pequeño para más barras)
    bar_builder = VolumeQtyBarBuilder(qty_limit=0.05)  # 0.05 BTC por barra (~$4.5k a $90k/BTC)

    # Contadores y almacenamiento
    equity_rows: list[tuple] = []
    bar_rows: list[dict] = []  # Para data.csv con OHLCV
    trade_rows: list[dict] = []
    decisions_rows: list[dict] = []

    trades_seen = 0
    bars_emitted = 0
    start_time = time.time()
    last_price = 0.0

    print("🚀 Iniciando trading en vivo...")
    print(f"   Símbolo: {symbol}")
    print(f"   Testnet: {testnet}")
    print(f"   Duración: {duration}s ({duration/60:.1f} min)")
    print(f"   Capital: ${cash:,.2f}")
    slip_display = "dinámico (spread)" if slip_bps is None else f"{slip_bps} bps"
    print(f"   Fees: {fees_bps} bps | Slippage: {slip_display}")
    print(f"   Directorio: {run_dir}")
    print("-" * 60)

    # Variables para logging de spread
    last_spread_log = time.time()
    spread_log_interval = 60  # loggear cada 60 segundos

    try:
        async for trade_data in iter_trades(symbol, testnet=testnet):
            # Verificar timeout
            elapsed = time.time() - start_time
            if elapsed >= duration:
                print(f"\n⏱️  Tiempo completado: {elapsed:.1f}s")
                break

            # Log periódico de spread dinámico
            if spread_tracker and bars_emitted > 0:
                now = time.time()
                if now - last_spread_log >= spread_log_interval:
                    stats = spread_tracker.get_stats()
                    current_spread_bps = stats.current_spread_bps
                    # El slippage efectivo es 50% del spread
                    effective_slip_bps = current_spread_bps * 0.5
                    print(
                        f"📊 Spread: {current_spread_bps:.2f} bps "
                        f"(avg: {stats.avg_spread_bps:.2f}, min: {stats.min_spread_bps:.2f}, "
                        f"max: {stats.max_spread_bps:.2f}) → slip efectivo: {effective_slip_bps:.2f} bps",
                        flush=True,
                    )
                    last_spread_log = now

            # Procesar trade
            trades_seen += 1
            t = trade_data["t"] / 1000.0  # ms a segundos
            price = float(trade_data["price"])
            qty = float(trade_data["qty"])
            is_buyer_maker = trade_data["is_buyer_maker"]
            last_price = price

            # Actualizar broker con precio de mercado
            broker.on_tick(symbol=symbol, mid=price, ts=t)

            # Construir micro-vela
            trade_obj = Trade(
                price=price,
                qty=qty,
                timestamp=datetime.fromtimestamp(t, tz=UTC),
                is_buyer_maker=is_buyer_maker,
            )
            bar = bar_builder.update(trade_obj)

            # Si se completó una barra, tomar decisión
            if bar:
                bars_emitted += 1
                bar_price = bar.close
                bar_ts = bar.end_time.timestamp()

                # Mark-to-market
                pos_qty = broker.get_position(symbol)
                cash_now = broker._usdt
                equity_now = cash_now + (pos_qty * bar_price)

                # Guardar equity
                equity_rows.append((bar_ts, symbol, bar_price, pos_qty, cash_now, equity_now))

                # Guardar datos de barra (OHLCV)
                bar_rows.append(
                    {
                        "timestamp": bar_ts,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                )

                # Ejecutar estrategia
                if strategy:
                    # Crear diccionario de barra para la estrategia
                    bar_dict = {
                        "ts": bar_ts,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }

                    # CRÍTICO: Añadir propiedades que la estrategia espera en el broker
                    # como atributos directos (no solo asignación)
                    type(broker).cash = property(lambda self: self._usdt)
                    type(broker).position_qty = property(lambda self: self.get_position(symbol))

                    # Llamar a la estrategia
                    try:
                        strategy.on_bar_live(broker, executor, symbol, bar_dict)

                        # Registrar trades del executor
                        for trade_info in executor.orders_executed:
                            # Recalcular equity después del trade
                            pos_after = broker.get_position(symbol)
                            cash_after = broker._usdt
                            equity_after = cash_after + (pos_after * bar_price)

                            trade_rows.append(
                                {
                                    "timestamp": bar_ts,
                                    "side": trade_info["side"],
                                    "price": trade_info["price"],
                                    "qty": trade_info["qty"],
                                    "cash": cash_after,
                                    "equity": equity_after,
                                    "reason": "strategy",
                                }
                            )
                        executor.orders_executed.clear()  # Limpiar para próxima barra

                    except Exception as e:
                        print(f"⚠️  Error en estrategia: {e}")
                        if bars_emitted <= 5:  # Solo mostrar traceback en las primeras barras
                            import traceback

                            traceback.print_exc()

                # Log de progreso
                if bars_emitted % 10 == 0:
                    print(
                        f"Bars: {bars_emitted:4d} | Trades: {trades_seen:6d} | "
                        f"Price: ${bar_price:,.2f} | Equity: ${equity_now:,.2f} | "
                        f"Elapsed: {elapsed:.0f}s"
                    )

    except KeyboardInterrupt:
        print("\n⚠️  Interrumpido por usuario")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Cerrar posición final si existe
        final_pos = broker.get_position(symbol)
        if abs(final_pos) > 0.01:
            print(f"\n🔄 Cerrando posición final: {final_pos:.4f} @ ${last_price:.2f}")
            try:
                side = "SELL" if final_pos > 0 else "BUY"
                req = OrderRequest(
                    symbol=symbol,
                    side=side,
                    order_type="MARKET",
                    quantity=abs(final_pos),
                )
                order = broker.submit_order(req)

                # Registrar el cierre de posición en trade_rows
                if order.status == "FILLED":
                    final_cash = broker._usdt
                    final_pos_qty = broker.get_position(symbol)
                    final_equity = final_cash + (final_pos_qty * last_price)
                    trade_rows.append(
                        {
                            "timestamp": time.time(),
                            "side": side,
                            "price": order.fills[0].price if order.fills else last_price,
                            "qty": order.filled_qty,
                            "cash": final_cash,
                            "equity": final_equity,
                            "reason": "close_position_end",
                        }
                    )

                print("✅ Posición cerrada exitosamente")
            except Exception as e:
                print(f"⚠️  Error cerrando posición: {e}")

        # Detener SpreadTracker si está activo
        if spread_tracker:
            spread_tracker.stop()
            stats = spread_tracker.get_stats()
            print(
                f"\n📊 Estadísticas de spread: "
                f"avg={stats.avg_spread_bps:.2f} bps, "
                f"min={stats.min_spread_bps:.2f}, "
                f"max={stats.max_spread_bps:.2f}, "
                f"samples={stats.samples}",
                flush=True,
            )

        # Guardar resultados
        final_cash = broker._usdt
        final_pos_qty = broker.get_position(symbol)
        final_equity = final_cash + (final_pos_qty * last_price)
        final_pnl = final_equity - cash
        final_ret = (final_pnl / cash) * 100 if cash > 0 else 0

        print("\n" + "=" * 60)
        print("📊 RESUMEN DE EJECUCIÓN")
        print("=" * 60)
        print(f"Trades procesados:  {trades_seen:,}")
        print(f"Barras emitidas:    {bars_emitted:,}")
        print(f"Órdenes ejecutadas: {len(trade_rows)}")
        print(f"Capital inicial:    ${cash:,.2f}")
        print(f"Equity final:       ${final_equity:,.2f}")
        print(f"PnL:                ${final_pnl:+,.2f} ({final_ret:+.2f}%)")
        print(f"Tiempo total:       {time.time() - start_time:.1f}s")
        print("=" * 60)

        # Guardar equity.csv
        write_equity_csv(run_dir, equity_rows)
        # Guardar trades.csv (siempre se crea, incluso si está vacío)
        write_trades_csv(run_dir, trade_rows)
        # Guardar decisions.csv
        write_decisions_csv(run_dir, decisions_rows)
        # Guardar data.csv con estructura OHLCV
        write_data_csv(run_dir, bar_rows)
        # Guardar quality.json
        duration_sec = time.time() - start_time
        bars_per_sec = bars_emitted / duration_sec if duration_sec > 0 else 0.0
        quality = {
            "bars_processed": bars_emitted,
            "duration_sec": duration_sec,
            "bars_per_sec": bars_per_sec,
        }
        with (run_dir / "quality.json").open("w") as f:
            json.dump(quality, f, indent=2)

        # Calcular métricas avanzadas
        equity_values = [row[5] for row in equity_rows]  # equity en índice 5
        trades_pnl = []

        # Calcular PnL por trade (asumiendo pares compra-venta)
        if len(trade_rows) >= 2:
            # Agrupar trades en pares compra-venta
            i = 0
            while i < len(trade_rows) - 1:
                if trade_rows[i].get("side") == "BUY" and trade_rows[i + 1].get("side") == "SELL":
                    buy_equity = trade_rows[i].get("equity", 0)
                    sell_equity = trade_rows[i + 1].get("equity", 0)
                    pnl = sell_equity - buy_equity
                    trades_pnl.append(pnl)
                    i += 2
                else:
                    i += 1

        metrics = calculate_all_metrics(equity_values, trades_pnl) if equity_values else {}

        # Contar órdenes por tipo
        n_buy = sum(1 for t in trade_rows if t.get("side") == "BUY")
        n_sell = sum(1 for t in trade_rows if t.get("side") == "SELL")

        # Guardar summary.json con métricas avanzadas
        write_summary(
            run_dir,
            {
                "trades_processed": trades_seen,
                "bars_emitted": bars_emitted,
                "orders_executed": n_buy + n_sell,
                "orders_buy": n_buy,
                "orders_sell": n_sell,
                "starting_cash": cash,
                "final_equity": final_equity,
                "pnl": final_pnl,
                "return_pct": final_ret,
                "duration_s": duration_sec,
                # Métricas avanzadas
                "sharpe_ratio": round(metrics.get("sharpe_ratio", 0.0), 4),
                "sortino_ratio": round(metrics.get("sortino_ratio", 0.0), 4),
                "max_drawdown_pct": round(metrics.get("max_drawdown", 0.0) * 100, 2),
                "profit_factor": round(metrics.get("profit_factor", 0.0), 2),
                "win_rate_pct": round(metrics.get("win_rate", 0.0) * 100, 2),
                "num_winning_trades": metrics.get("num_winning_trades", 0),
                "num_losing_trades": metrics.get("num_losing_trades", 0),
                "avg_win": round(metrics.get("avg_win", 0.0), 2),
                "avg_loss": round(metrics.get("avg_loss", 0.0), 2),
                "avg_trade": round(metrics.get("avg_trade", 0.0), 2),
            },
        )

        # Guardar returns.csv
        write_returns_csv(run_dir, equity_rows)

        print(f"\n✅ Resultados guardados en: {run_dir}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Trading en vivo con Binance WebSocket")
    p.add_argument("--run-dir", required=True, help="Directorio para guardar resultados")
    p.add_argument("--symbol", default="BTCUSDT", help="Símbolo a tradear")
    p.add_argument("--testnet", action="store_true", help="Usar Binance Testnet")
    p.add_argument("--duration", type=int, default=600, help="Duración en segundos")
    p.add_argument("--cash", type=float, default=10000.0, help="Capital inicial (USDT)")
    p.add_argument(
        "--fees-bps", type=float, default=10.0, help="Comisiones en bps (Binance: 10 bps = 0.1%)"
    )
    p.add_argument(
        "--slip-bps",
        type=float,
        default=None,
        help="Slippage en bps (None = dinámico basado en spread)",
    )
    p.add_argument("--strategy", default=None, help="Nombre de estrategia (ej: momentum)")
    p.add_argument("--params", default=None, help="Parámetros de estrategia en JSON")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = pathlib.Path(args.run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    started_ts = time.time()
    save_manifest(run_dir, args, started_ts)

    asyncio.run(
        run_live_trading(
            symbol=args.symbol,
            run_dir=run_dir,
            duration=args.duration,
            cash=args.cash,
            fees_bps=args.fees_bps,
            slip_bps=args.slip_bps,
            testnet=args.testnet,
            strategy_name=args.strategy,
            strategy_params=args.params,
        )
    )


if __name__ == "__main__":
    main()

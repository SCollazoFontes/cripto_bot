"""Core live trading loop with Binance WebSocket feed."""

from __future__ import annotations

import asyncio
import csv
from datetime import UTC, datetime
import json
import pathlib
import time

from bars.base import Trade
from bars.volume_qty import VolumeQtyBarBuilder
from brokers.base import OrderRequest
from brokers.binance_paper import BinancePaperBroker, _ExecCfg
from core.metrics import calculate_all_metrics
from core.spread_tracker import SpreadTracker
from data.feeds.binance_trades import iter_trades
from strategies.base import get_strategy_class
from tools.live.executor import SimpleExecutor
from tools.live.output_writers import (
    write_decisions_csv,
    write_equity_csv,
    write_returns_csv,
    write_summary,
    write_trades_csv,
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
    bar_rows: list[dict] = []  # Para data.csv con OHLCV (también se escribe incrementalmente)
    trade_rows: list[dict] = []
    decisions_rows: list[dict] = []

    trades_seen = 0
    bars_emitted = 0
    start_time = time.time()
    last_price = 0.0

    print("🚀 Iniciando trading en vivo...")

    # Preparar archivo de velas incremental
    data_csv_path = run_dir / "data.csv"
    try:
        if not data_csv_path.exists():
            with data_csv_path.open("w", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"]
                )
                writer.writeheader()
    except Exception as e:
        print(f"⚠️  No se pudo inicializar data.csv: {e}")
    print(f"   Símbolo: {symbol}")
    print(f"   Testnet: {testnet}")
    print(f"   Duración: {duration}s ({duration/60:.1f} min)")
    print(f"   Capital: ${cash:,.2f}")
    slip_display = "dinámico (spread)" if slip_bps is None else f"{slip_bps} bps"
    print(f"   Fees: {fees_bps} bps | Slippage: {slip_display}")
    print(f"   Directorio: {run_dir}")
    print("-" * 60)

    try:
        trade_gen = iter_trades(symbol, testnet=testnet)
        try:
            async for trade_data in trade_gen:
                # Verificar timeout
                elapsed = time.time() - start_time
                if elapsed >= duration:
                    print(f"\n⏱️  Tiempo completado: {elapsed:.1f}s")
                    break

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

                    # Guardar datos de barra (OHLCV) en memoria y en disco (incremental)
                    bar_row = {
                        "timestamp": bar_ts,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                    bar_rows.append(bar_row)
                    try:
                        with data_csv_path.open("a", newline="") as f:
                            writer = csv.DictWriter(
                                f,
                                fieldnames=[
                                    "timestamp",
                                    "open",
                                    "high",
                                    "low",
                                    "close",
                                    "volume",
                                ],
                            )
                            writer.writerow(bar_row)
                    except Exception as e:
                        # No detener ejecución por problema de escritura de archivo
                        if bars_emitted <= 3:
                            print(f"⚠️  No se pudo escribir en data.csv: {e}")

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
                                # Mensaje mínimo de decisión de trade en terminal
                                side_txt = "COMPRA" if trade_info["side"] == "BUY" else "VENTA"
                                try:
                                    q = float(trade_info["qty"])
                                except Exception:
                                    q = 0.0
                                try:
                                    px = float(trade_info["price"])
                                except Exception:
                                    px = bar_price
                                print(f"{side_txt} {q:.6f} {symbol} @ ${px:.2f}")
                            executor.orders_executed.clear()  # Limpiar para próxima barra

                        except Exception as e:
                            print(f"⚠️  Error en estrategia: {e}")
                            if bars_emitted <= 5:  # Solo mostrar traceback en las primeras barras
                                import traceback

                                traceback.print_exc()
        finally:
            try:
                await trade_gen.aclose()  # asegurar cierre del generador async
            except Exception:
                pass

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
            # Registrar como una decisión de trade final (venta/compra)
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
                    # Mensaje de decisión final
                    side_txt = "COMPRA" if side == "BUY" else "VENTA"
                    px = order.fills[0].price if getattr(order, "fills", None) else last_price
                    print(f"{side_txt} {abs(final_pos):.6f} {symbol} @ ${px:.2f}")
            except Exception as e:
                print(f"⚠️  Error cerrando posición: {e}")

        # Detener SpreadTracker si está activo
        if spread_tracker:
            spread_tracker.stop()

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
        # data.csv ya se fue escribiendo incrementalmente
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

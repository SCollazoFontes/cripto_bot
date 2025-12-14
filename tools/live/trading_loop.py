"""Core live trading loop with Binance WebSocket feed."""

from __future__ import annotations

import asyncio
import csv
from datetime import UTC, datetime
import json
import pathlib
import time

from bars.base import Trade
from bars.builders import CompositeBarBuilder, TimeBarBuilder
from brokers.base import OrderRequest
from brokers.binance_paper import BinancePaperBroker, _ExecCfg
from core.metrics import calculate_all_metrics
from core.monitoring import SpreadTracker
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
    bar_tick_limit: int | None = None,
    bar_qty_limit: float | None = None,
    bar_value_limit: float | None = None,
    bar_imbal_limit: float | None = None,
    bar_imbal_mode: str = "qty",
    bar_policy: str = "any",
    bar_flush_interval: int = 20,
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

    # Exponer propiedades que las estrategias esperan (evitar patch inside loop)
    type(broker).cash = property(lambda self: self._usdt)  # type: ignore
    type(broker).position_qty = property(lambda self: self.get_position(symbol))  # type: ignore

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

    # Registrar callback de fills para instrumentación de costes
    def _on_fill(details: dict) -> None:
        spread_bps = 0.0
        if spread_tracker:
            try:
                spread_bps = float(spread_tracker.get_spread())
            except Exception:
                spread_bps = 0.0
        mid = float(details.get("mid_price", 0.0))
        eff = float(details.get("effective_price", 0.0))
        side = str(details.get("side", "buy"))
        sign = 1.0 if side.lower() == "buy" else -1.0
        exec_dev_bps = 0.0
        if mid > 0 and eff > 0:
            exec_dev_bps = ((eff - mid) / mid) * 10000.0 * sign
        row = {
            "timestamp": int(details.get("timestamp", time.time())),
            "symbol": details.get("symbol", symbol),
            "side": side,
            "role": details.get("role", "taker"),
            "mid_price": mid,
            "effective_price": eff,
            "qty": float(details.get("qty", 0.0)),
            "fee": float(details.get("fee", 0.0)),
            "spread_bps": spread_bps,
            "exec_dev_bps": exec_dev_bps,
        }
        cost_rows.append(row)

    try:
        broker.on_fill = _on_fill  # type: ignore[attr-defined]
    except Exception:
        pass

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

    # Configurar Bar Builders
    # Si no se especifica ningún umbral, usar defaults conservadores
    if all(x is None for x in [bar_tick_limit, bar_qty_limit, bar_value_limit, bar_imbal_limit]):
        # Defaults: 100 trades O $50k negociados
        bar_tick_limit = 100
        bar_value_limit = 50000.0
        print("⚙️  Usando configuración de barras por defecto: tick_limit=100, value_limit=$50k")

    # Builder para ESTRATEGIA (micro-velas)
    bar_builder = CompositeBarBuilder(
        tick_limit=bar_tick_limit,
        qty_limit=bar_qty_limit,
        value_limit=bar_value_limit,
        imbal_limit=bar_imbal_limit,
        imbal_mode="tick" if str(bar_imbal_mode).lower() == "tick" else "qty",
        policy=bar_policy,
    )

    # Builder para GRÁFICO (barras de tiempo fijas desde trades brutos)
    chart_builder = TimeBarBuilder(period_ms=1000)  # 1s fijo

    # El dashboard reagrupa data.csv a timeframes fijos; no necesitamos builder extra aquí.

    # Mostrar configuración del builder
    active_rules = []
    if bar_tick_limit:
        active_rules.append(f"tick={bar_tick_limit}")
    if bar_qty_limit:
        active_rules.append(f"qty={bar_qty_limit:.2f} BTC")
    if bar_value_limit:
        active_rules.append(f"value=${bar_value_limit:,.0f}")
    if bar_imbal_limit:
        active_rules.append(f"imbal={bar_imbal_limit:.2f}")
    print(f"📊 Bar Builder: CompositeBarBuilder({', '.join(active_rules)}, policy='{bar_policy}')")

    # Contadores y almacenamiento
    equity_rows: list[tuple] = []
    # Buffers de escritura
    bar_rows: list[dict] = []  # Micro-velas (estrategia) → data.csv
    chart_rows: list[dict] = []  # Barras de tiempo (gráfico) → chart.csv
    _last_flushed: int = 0
    _chart_last_flushed: int = 0
    _FLUSH_INTERVAL = int(bar_flush_interval)  # número de barras entre flush a disco
    trade_rows: list[dict] = []
    cost_rows: list[dict] = []
    decisions_rows: list[dict] = []

    trades_seen = 0
    bars_emitted = 0
    start_time = time.time()
    last_price = 0.0

    print("🚀 Iniciando trading en vivo...")

    # Preparar archivo de velas incremental
    data_csv_path = run_dir / "data.csv"
    chart_csv_path = run_dir / "chart.csv"  # Barras de tiempo para gráfico
    equity_csv_path = run_dir / "equity.csv"
    trades_csv_path = run_dir / "trades.csv"
    costs_csv_path = run_dir / "costs.csv"
    try:
        if not data_csv_path.exists():
            with data_csv_path.open("w", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "trade_count",
                        "dollar_value",
                        "start_time",
                        "end_time",
                        "duration_ms",
                    ],
                )
                writer.writeheader()
        # Inicializar chart.csv (barras de tiempo fijas para gráfico)
        if not chart_csv_path.exists():
            with chart_csv_path.open("w", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "trade_count",
                        "dollar_value",
                        "start_time",
                        "end_time",
                        "duration_ms",
                    ],
                )
                writer.writeheader()
        # Inicializar equity.csv si no existe (para escritura incremental)
        if not equity_csv_path.exists():
            with equity_csv_path.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "symbol", "price", "qty", "cash", "equity"])
        # Inicializar trades.csv si no existe (para escritura incremental)
        if not trades_csv_path.exists():
            with trades_csv_path.open("w", newline="") as f:
                w = csv.DictWriter(
                    f, fieldnames=["timestamp", "side", "price", "qty", "cash", "equity", "reason"]
                )
                w.writeheader()
        # Inicializar costs.csv si no existe (para escritura incremental)
        if not costs_csv_path.exists():
            with costs_csv_path.open("w", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp",
                        "symbol",
                        "side",
                        "role",
                        "mid_price",
                        "effective_price",
                        "qty",
                        "fee",
                        "spread_bps",
                        "exec_dev_bps",
                    ],
                )
                w.writeheader()
    except Exception as e:
        print(f"⚠️  No se pudo inicializar data.csv: {e}")
    print(f"   Símbolo: {symbol}")
    net_display = "Mainnet (Paper Live)" if not testnet else "Testnet"
    print(f"   Red: {net_display}")
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
                # Actualizar builder de estrategia
                bar = bar_builder.update(trade_obj)

                # Actualizar builder de gráfico (tiempo fijo)
                chart_bar = chart_builder.update(trade_obj)

                # Si se cerró una barra de tiempo, escribirla a chart.csv
                if chart_bar:
                    chart_start_ts = chart_bar.start_time.timestamp()
                    chart_end_ts = chart_bar.end_time.timestamp()
                    chart_duration_ms = int((chart_end_ts - chart_start_ts) * 1000)
                    chart_row = {
                        "timestamp": chart_end_ts,
                        "open": chart_bar.open,
                        "high": chart_bar.high,
                        "low": chart_bar.low,
                        "close": chart_bar.close,
                        "volume": chart_bar.volume,
                        "trade_count": chart_bar.trade_count,
                        "dollar_value": chart_bar.dollar_value if chart_bar.dollar_value else 0.0,
                        "start_time": chart_start_ts,
                        "end_time": chart_end_ts,
                        "duration_ms": chart_duration_ms,
                    }
                    chart_rows.append(chart_row)
                    # Flush inmediato para el dashboard
                    pending_chart = len(chart_rows) - _chart_last_flushed
                    if pending_chart >= 1:  # Escribir cada barra inmediatamente
                        try:
                            with chart_csv_path.open("a", newline="") as f:
                                writer = csv.DictWriter(
                                    f,
                                    fieldnames=[
                                        "timestamp",
                                        "open",
                                        "high",
                                        "low",
                                        "close",
                                        "volume",
                                        "trade_count",
                                        "dollar_value",
                                        "start_time",
                                        "end_time",
                                        "duration_ms",
                                    ],
                                )
                                for row in chart_rows[_chart_last_flushed:]:
                                    writer.writerow(row)
                            _chart_last_flushed = len(chart_rows)
                        except Exception as e:
                            if len(chart_rows) <= 5:
                                print(f"⚠️  Error escritura chart.csv: {e}")

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
                    # Escribir equity incrementalmente para el dashboard
                    try:
                        with equity_csv_path.open("a", newline="") as f:
                            w = csv.writer(f)
                            w.writerow((bar_ts, symbol, bar_price, pos_qty, cash_now, equity_now))
                    except Exception:
                        pass

                    # Guardar datos de barra (OHLCV) en memoria y en disco (incremental)
                    bar_start_ts = bar.start_time.timestamp()
                    bar_end_ts = bar.end_time.timestamp()
                    bar_duration_ms = int((bar_end_ts - bar_start_ts) * 1000)

                    bar_row = {
                        "timestamp": bar_ts,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "trade_count": bar.trade_count,
                        "dollar_value": bar.dollar_value if bar.dollar_value else 0.0,
                        "start_time": bar_start_ts,
                        "end_time": bar_end_ts,
                        "duration_ms": bar_duration_ms,
                    }
                    bar_rows.append(bar_row)
                    # Flush por lotes para reducir I/O
                    pending = len(bar_rows) - _last_flushed
                    if pending >= _FLUSH_INTERVAL:
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
                                        "trade_count",
                                        "dollar_value",
                                        "start_time",
                                        "end_time",
                                        "duration_ms",
                                    ],
                                )
                                for row in bar_rows[_last_flushed:]:
                                    writer.writerow(row)
                            _last_flushed = len(bar_rows)
                        except Exception as e:
                            if bars_emitted <= _FLUSH_INTERVAL:
                                print(f"⚠️  Error escritura batch data.csv: {e}")

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

                        # Propiedades ya expuestas fuera del loop

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
                                # Escribir trade incrementalmente para el dashboard
                                try:
                                    with trades_csv_path.open("a", newline="") as f:
                                        w = csv.DictWriter(
                                            f,
                                            fieldnames=[
                                                "timestamp",
                                                "side",
                                                "price",
                                                "qty",
                                                "cash",
                                                "equity",
                                                "reason",
                                            ],
                                        )
                                        w.writerow(
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
                                except Exception:
                                    pass
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
                    last_ts = time.time()
                    trade_record = {
                        "timestamp": last_ts,
                        "side": side,
                        "price": order.fills[0].price if order.fills else last_price,
                        "qty": order.filled_qty,
                        "cash": final_cash,
                        "equity": final_equity,
                        "reason": "close_position_end",
                    }
                    trade_rows.append(trade_record)
                    # Escribir también incrementalmente el cierre
                    try:
                        with trades_csv_path.open("a", newline="") as f:
                            w = csv.DictWriter(
                                f,
                                fieldnames=[
                                    "timestamp",
                                    "side",
                                    "price",
                                    "qty",
                                    "cash",
                                    "equity",
                                    "reason",
                                ],
                            )
                            w.writerow(trade_record)
                    except Exception:
                        pass
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
        # Flush final de barras pendientes (micro y chart)
        remaining = len(bar_rows) - _last_flushed
        if remaining > 0:
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
                            "trade_count",
                            "dollar_value",
                            "start_time",
                            "end_time",
                            "duration_ms",
                        ],
                    )
                    for row in bar_rows[_last_flushed:]:
                        writer.writerow(row)
            except Exception as e:
                print(f"⚠️  Error flush final data.csv: {e}")

        # Forzar cierre de la última barra de tiempo (chart) si hay buffer
        try:
            final_chart_bar = chart_builder.flush_partial()
        except Exception:
            final_chart_bar = None

        if final_chart_bar:
            chart_start_ts = final_chart_bar.start_time.timestamp()
            chart_end_ts = final_chart_bar.end_time.timestamp()
            chart_duration_ms = int((chart_end_ts - chart_start_ts) * 1000)
            final_chart_row = {
                "timestamp": chart_end_ts,
                "open": final_chart_bar.open,
                "high": final_chart_bar.high,
                "low": final_chart_bar.low,
                "close": final_chart_bar.close,
                "volume": final_chart_bar.volume,
                "trade_count": final_chart_bar.trade_count,
                "dollar_value": (
                    final_chart_bar.dollar_value if final_chart_bar.dollar_value else 0.0
                ),
                "start_time": chart_start_ts,
                "end_time": chart_end_ts,
                "duration_ms": chart_duration_ms,
            }
            chart_rows.append(final_chart_row)

        remaining_chart = len(chart_rows) - _chart_last_flushed
        if remaining_chart > 0:
            try:
                with chart_csv_path.open("a", newline="") as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=[
                            "timestamp",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                            "trade_count",
                            "dollar_value",
                            "start_time",
                            "end_time",
                            "duration_ms",
                        ],
                    )
                    for row in chart_rows[_chart_last_flushed:]:
                        writer.writerow(row)
            except Exception as e:
                print(f"⚠️  Error flush final chart.csv: {e}")
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
        # Guardar costs.csv
        try:
            if cost_rows:
                with costs_csv_path.open("a", newline="") as f:
                    w = csv.DictWriter(
                        f,
                        fieldnames=[
                            "timestamp",
                            "symbol",
                            "side",
                            "role",
                            "mid_price",
                            "effective_price",
                            "qty",
                            "fee",
                            "spread_bps",
                            "exec_dev_bps",
                        ],
                    )
                    for r in cost_rows:
                        w.writerow(r)
        except Exception as e:
            print(f"⚠️  Error guardando costs.csv: {e}")
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

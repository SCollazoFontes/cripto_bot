"""Layout principal del dashboard de trading en vivo."""

from pathlib import Path
import time

import streamlit as st

try:
    from tools.visual.chart_ohlc import (
        render_ohlc_volume,
    )
    from tools.visual.kill_switch import handle_kill_switch
except ModuleNotFoundError:
    from chart_ohlc import render_ohlc_volume
    from kill_switch import handle_kill_switch


def render_dashboard(run_dir: str):
    """Renderiza el dashboard completo con estructura modular tipo Binance."""
    st.set_page_config(
        page_title="Trading Live",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Estilos globales
    st.markdown(
        """
        <style>
        /* Fondo oscuro tipo Binance */
        .stApp {
            background-color: #0b0e11;
        }

        /* Contenedores de métricas */
        .metric-box {
            background-color: #1e2329;
            border: 1px solid #2b3139;
            border-radius: 4px;
            padding: 12px;
            margin-bottom: 8px;
        }

        .metric-label {
            color: #848e9c;
            font-size: 12px;
            font-weight: 500;
            margin-bottom: 4px;
        }

        .metric-value {
            color: #eaecef;
            font-size: 16px;
            font-weight: 600;
        }

        .metric-value-large {
            color: #eaecef;
            font-size: 24px;
            font-weight: 700;
        }

        /* Botones de timeframe más compactos y tipo Binance */
        .stButton > button {
            background-color: transparent !important;
            border: none !important;
            color: #848e9c !important;
            padding: 4px 10px !important;
            font-size: 12px !important;
            font-weight: 500 !important;
            min-width: 38px !important;
            height: 28px !important;
            margin: 0 2px !important;
            border-radius: 2px !important;
            transition: all 0.2s !important;
            white-space: nowrap !important;
        }

        .stButton > button:hover {
            background-color: #2b3139 !important;
            color: #eaecef !important;
        }

        /* Botón seleccionado (primary) */
        .stButton > button[kind="primary"],
        .stButton > button[data-baseweb="button"][kind="primary"] {
            background-color: #2b3139 !important;
            color: #f0b90b !important;
        }

        /* Botón no seleccionado (secondary) */
        .stButton > button[kind="secondary"],
        .stButton > button[data-baseweb="button"][kind="secondary"] {
            background-color: transparent !important;
            color: #848e9c !important;
        }

        /* Ocultar elementos de Streamlit */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}

        /* Texto general */
        .stMarkdown {
            color: #eaecef;
        }

        /* Título del par */
        .pair-title {
            color: #eaecef;
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 8px;
        }

        /* Estado de estrategia */
        .status-running {
            color: #0ecb81;
            font-weight: 600;
        }

        .status-label {
            color: #848e9c;
            font-size: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Kill switch en sidebar
    handle_kill_switch(run_dir)

    # Selector de timeframe fijo en la parte superior (horizontal)
    tf_options = ["1s", "5s", "10s", "30s", "1m", "5m", "1h"]
    cols = st.columns([0.6] + [0.5] * len(tf_options) + [8])

    # Inicializar timeframe en session_state
    if "selected_tf" not in st.session_state:
        st.session_state.selected_tf = "1s"

    # Inicializar contador de última fila procesada para evitar parpadeos
    if "last_row_count" not in st.session_state:
        st.session_state.last_row_count = 0

    with cols[0]:
        st.markdown(
            (
                '<div style="color: #848e9c; font-size: 13px; '
                "font-weight: 500; padding-top: 4px; "
                'line-height: 32px;">Tiempo</div>'
            ),
            unsafe_allow_html=True,
        )

    for idx, tf in enumerate(tf_options):
        with cols[idx + 1]:
            # Determinar si este botón está seleccionado
            is_selected = st.session_state.selected_tf == tf
            button_type = "primary" if is_selected else "secondary"

            if st.button(tf, key=f"tf_{tf}", type=button_type):
                st.session_state.selected_tf = tf
                st.rerun()

    # Layout principal: 2 columnas (gráfico ancho + panel info)
    col_main, col_info = st.columns([6.5, 2])

    # Nota: el bloque completo de "POSICIÓN" se renderiza más abajo como un único contenedor

    with col_main:
        # Calcular métricas en tiempo real desde data.csv
        data_file = Path(run_dir) / "data.csv"

        # Variable para controlar si hay datos nuevos
        has_new_data = False

        if data_file.exists():
            import pandas as pd

            df = pd.read_csv(data_file)

            # Verificar si hay nuevas filas desde la última actualización
            current_row_count = len(df)
            if current_row_count > st.session_state.last_row_count:
                has_new_data = True
                st.session_state.last_row_count = current_row_count

            if not df.empty and len(df) >= 2:
                # Precio actual (último close)
                current_price = df["close"].iloc[-1]

                # Cambio % desde el inicio de la sesión
                session_start_price = df["close"].iloc[0]
                price_change_pct = (
                    (current_price - session_start_price) / session_start_price
                ) * 100

                # Máximo y mínimo de la sesión
                session_high = df["high"].max()
                session_low = df["low"].min()

                # Volumen del último segundo completo en USDT
                last_timestamp_second = int(df["timestamp"].iloc[-1])
                last_second_data = df[
                    df["timestamp"].apply(lambda x: int(x) == last_timestamp_second)
                ]
                last_volume_btc = last_second_data["volume"].sum()
                last_avg_price = last_second_data["close"].mean()
                last_volume = last_volume_btc * last_avg_price

                # Tiempo de ejecución
                start_time = df["timestamp"].iloc[0]
                current_time = df["timestamp"].iloc[-1]
                elapsed_seconds = int(current_time - start_time)
                elapsed_minutes = elapsed_seconds // 60
                elapsed_secs = elapsed_seconds % 60

                # Color para el cambio
                change_color = "#0ecb81" if price_change_pct >= 0 else "#f6465d"
            else:
                current_price = 0.0
                price_change_pct = 0.0
                session_high = 0.0
                session_low = 0.0
                last_volume = 0.0
                elapsed_minutes = 0
                elapsed_secs = 0
                change_color = "#848e9c"
        else:
            current_price = 0.0
            price_change_pct = 0.0
            session_high = 0.0
            session_low = 0.0
            last_volume = 0.0
            elapsed_minutes = 0
            elapsed_secs = 0
            change_color = "#848e9c"

        # Fila superior con métricas en tiempo real
        header_cols = st.columns(6)
        with header_cols[0]:
            st.markdown(
                (
                    '<div style="color: #f0b90b; font-size: 24px; '
                    'font-weight: 700; line-height: 1.0; text-align: center;">'
                    f"{current_price:,.2f}</div>"
                ),
                unsafe_allow_html=True,
            )
        with header_cols[1]:
            st.markdown(
                (
                    f'<div style="color: {change_color}; font-size: 24px; '
                    'font-weight: 700; line-height: 1.0; text-align: center;">'
                    f"{price_change_pct:+.2f}%</div>"
                ),
                unsafe_allow_html=True,
            )
        with header_cols[2]:
            st.markdown(
                (
                    '<div style="line-height: 1.2; text-align: center;">'
                    '<span style="color: #848e9c; font-size: 16px;">Máx: </span>'
                    '<span style="color: #ffffff; font-size: 20px; font-weight: 600;">'
                    f"{session_high:,.2f}</span></div>"
                ),
                unsafe_allow_html=True,
            )
        with header_cols[3]:
            st.markdown(
                (
                    '<div style="line-height: 1.2; text-align: center;">'
                    '<span style="color: #848e9c; font-size: 16px;">Mín: </span>'
                    '<span style="color: #ffffff; font-size: 20px; font-weight: 600;">'
                    f"{session_low:,.2f}</span></div>"
                ),
                unsafe_allow_html=True,
            )
        with header_cols[4]:
            if last_volume >= 1_000_000:
                vol_display = f"{last_volume / 1_000_000:.2f}M"
            elif last_volume >= 1_000:
                vol_display = f"{last_volume / 1_000:.2f}K"
            else:
                vol_display = f"{last_volume:,.2f}"
            st.markdown(
                (
                    '<div style="line-height: 1.2; text-align: center;">'
                    '<span style="color: #848e9c; font-size: 16px;">Vol: </span>'
                    '<span style="color: #ffffff; font-size: 20px; font-weight: 600;">'
                    f"{vol_display}</span></div>"
                ),
                unsafe_allow_html=True,
            )
        with header_cols[5]:
            st.markdown(
                (
                    '<div style="line-height: 1.2; text-align: center;">'
                    '<span style="color: #848e9c; font-size: 16px;">T: </span>'
                    '<span style="color: #ffffff; font-size: 20px; font-weight: 600;">'
                    f"{elapsed_minutes:02d}:{elapsed_secs:02d}</span></div>"
                ),
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
        # Gráfico combinado
        render_ohlc_volume(run_dir, st.session_state.selected_tf, width=900, height=600)

    with col_info:
        # Leer datos de equity
        equity_file = Path(run_dir) / "equity.csv"
        trades_file = Path(run_dir) / "trades.csv"

        current_qty = 0.0
        current_equity = 10000.0
        position_value = 0.0
        avg_entry_price = 0.0
        pnl_usdt = 0.0
        pnl_pct = 0.0
        exposure_pct = 0.0
        initial_cash = 10000.0

        if equity_file.exists():
            import pandas as pd

            eq_df = pd.read_csv(equity_file)
            if not eq_df.empty:
                last_eq = eq_df.iloc[-1]
                current_qty = last_eq.get("qty", 0.0)
                current_equity = last_eq.get("equity", 10000.0)
                initial_cash = eq_df.iloc[0].get("equity", 10000.0)
                pnl_usdt = current_equity - initial_cash
                pnl_pct = (pnl_usdt / initial_cash) * 100 if initial_cash > 0 else 0.0
                position_value = current_qty * current_price if current_qty > 0 else 0.0
                exposure_pct = (
                    (position_value / current_equity) * 100 if current_equity > 0 else 0.0
                )

        if trades_file.exists() and current_qty > 0:
            import pandas as pd

            tr_df = pd.read_csv(trades_file)
            if not tr_df.empty:
                buys = tr_df[tr_df["side"] == "buy"]
                if not buys.empty:
                    total_qty = buys["qty"].sum()
                    if total_qty > 0:
                        avg_entry_price = (buys["price"] * buys["qty"]).sum() / total_qty

        pnl_color = "#0ecb81" if pnl_usdt >= 0 else "#f6465d"

        # POSICIÓN: título + contenido en un único bloque
        pos_block_html = (
            '<div style="padding: 0px 8px; margin-top: -48px; margin-bottom: 0px;">'
            '  <div style="display: flex; align-items: center; justify-content: center; '
            'height: 22px;">'
            '    <span style="color: #f0b90b; font-size: 20px; font-weight: 700; '
            'letter-spacing: 0.5px;">POSICIÓN</span>'
            "  </div>"
            '  <div style="text-align: center; margin-top: 10px; margin-bottom: 6px;">'
            f'    <div style="color: {pnl_color}; font-size: 28px; font-weight: 700; '
            f'line-height: 1.2;">{pnl_usdt:+.2f} USDT</div>'
            f'    <div style="color: {pnl_color}; font-size: 18px; font-weight: 600;">'
            f"({pnl_pct:+.2f}%)</div>"
            "  </div>"
            '  <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">'
            '    <span style="color: #848e9c; font-size: 14px;">Tamaño:</span>'
            f'    <span style="color: #ffffff; font-size: 14px; font-weight: 600;">'
            f"{current_qty:.4f} BTC</span>"
            "  </div>"
            '  <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">'
            '    <span style="color: #848e9c; font-size: 14px;">Precio medio:</span>'
            f'    <span style="color: #ffffff; font-size: 14px; font-weight: 600;">'
            f"{avg_entry_price:,.2f} USDT</span>"
            "  </div>"
            '  <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">'
            '    <span style="color: #848e9c; font-size: 14px;">Valor posición:</span>'
            f'    <span style="color: #ffffff; font-size: 14px; font-weight: 600;">'
            f"{position_value:,.2f} USDT</span>"
            "  </div>"
            '  <div style="display: flex; justify-content: space-between;">'
            '    <span style="color: #848e9c; font-size: 14px;">Exposición:</span>'
            f'    <span style="color: #ffffff; font-size: 14px; font-weight: 600;">'
            f"{exposure_pct:.1f}%</span>"
            "  </div>"
            "</div>"
        )

        st.markdown(pos_block_html, unsafe_allow_html=True)

        # Señal estrategia - Calcular usando lógica de momentum_v2
        manifest_file = Path(run_dir) / "manifest.json"
        signal_value = 0.0
        long_threshold = 0.0015
        short_threshold = -0.0015
        lookback_ticks = 20

        if manifest_file.exists():
            import json

            with open(manifest_file) as f:
                manifest = json.load(f)
                params = manifest.get("params", {})
                long_threshold = params.get("entry_threshold", 0.0015)
                exit_threshold = params.get("exit_threshold", 0.001)
                short_threshold = -exit_threshold
                lookback_ticks = params.get("lookback_ticks", 20)

        # Calcular señal usando lógica de momentum_v2
        if data_file.exists() and not df.empty and len(df) >= lookback_ticks:
            import pandas as pd

            # Obtener últimos precios para calcular momentum
            recent_prices = df["close"].tail(lookback_ticks)
            mean_price = recent_prices.mean()

            if mean_price > 0:
                # Momentum = (precio_actual - media) / media
                momentum = (current_price - mean_price) / mean_price

                # Normalizar a rango [-1, +1] usando los umbrales como referencia
                # Si momentum >= long_threshold → señal positiva fuerte
                # Si momentum <= short_threshold → señal negativa fuerte
                # Entre umbrales → zona neutral

                if momentum >= long_threshold:
                    # Escalar desde long_threshold hasta un máximo razonable (ej: 3x threshold)
                    max_signal = long_threshold * 3
                    signal_value = min(1.0, momentum / max_signal)
                elif momentum <= short_threshold:
                    # Escalar desde short_threshold hasta un mínimo razonable
                    min_signal = short_threshold * 3
                    signal_value = max(-1.0, momentum / min_signal)
                else:
                    # Zona neutral: escalar linealmente entre umbrales
                    neutral_range = long_threshold - short_threshold
                    if neutral_range > 0:
                        signal_value = (momentum - short_threshold) / neutral_range * 2 - 1
                    else:
                        signal_value = 0.0

                # Clip final por seguridad
                signal_value = max(-1.0, min(1.0, signal_value))

        # Calcular posición del marcador en la barra (0-100%)
        marker_position = ((signal_value + 1) / 2) * 100

        # Calcular posiciones de los umbrales en la barra
        long_threshold_pos = ((long_threshold + 1) / 2) * 100
        short_threshold_pos = ((short_threshold + 1) / 2) * 100

        # Determinar color del valor según la zona
        if signal_value >= long_threshold:
            signal_color = "#0ecb81"  # Verde
            zone_text = "LONG"
        elif signal_value <= short_threshold:
            signal_color = "#f6465d"  # Rojo
            zone_text = "SHORT"
        else:
            signal_color = "#848e9c"  # Gris
            zone_text = "NEUTRAL"

        signal_block_html = (
            '<div style="padding: 0px 8px; margin-top: 12px; margin-bottom: 0px;">'
            '  <div style="display: flex; align-items: center; justify-content: center; '
            'height: 20px;">'
            '    <span style="color: #f0b90b; font-size: 16px; font-weight: 700; '
            'letter-spacing: 0.5px;">SEÑAL ESTRATEGIA</span>'
            "  </div>"
            '  <div style="text-align: center; margin-top: 8px; margin-bottom: 8px;">'
            f'    <div style="color: {signal_color}; font-size: 24px; font-weight: 700; '
            f'line-height: 1.2;">{signal_value:+.4f}</div>'
            f'    <div style="color: {signal_color}; font-size: 12px; font-weight: 600; '
            f'margin-top: 2px;">{zone_text}</div>'
            "  </div>"
            '  <div style="position: relative; height: 20px; background: '
            "linear-gradient(to right, "
            f"    #f6465d 0%, #f6465d {short_threshold_pos}%, "
            f"    #2b3139 {short_threshold_pos}%, #2b3139 {long_threshold_pos}%, "
            f"    #0ecb81 {long_threshold_pos}%, #0ecb81 100%); "
            '    border-radius: 4px; margin: 8px 0px;">'
            f'    <div style="position: absolute; left: {marker_position}%; top: 50%; '
            "transform: translate(-50%, -50%); "
            "      width: 8px; height: 8px; background-color: #ffffff; border: 2px solid #0b0e11; "
            '      border-radius: 50%; box-shadow: 0 0 4px rgba(255,255,255,0.6);"></div>'
            "  </div>"
            '  <div style="display: flex; justify-content: space-between; margin-top: 4px;">'
            f'    <span style="color: #848e9c; font-size: 14px;">Short: {short_threshold:+.4f}</span>'
            f'    <span style="color: #848e9c; font-size: 14px;">Long: {long_threshold:+.4f}</span>'
            "  </div>"
            "</div>"
        )

        st.markdown(signal_block_html, unsafe_allow_html=True)

        # KPIs internos
        # TODO: Obtener valores reales de la estrategia cuando estén disponibles
        kpi_momentum = 3.21
        kpi_momentum_thr = 2.5
        kpi_vol_rel = 1.8
        kpi_vol_rel_thr = 1.2
        kpi_imbalance = 24.0
        kpi_imbalance_thr = 15.0
        kpi_volatility = 0.42
        kpi_volatility_max = 1.2

        kpis_block_html = (
            '<div style="padding: 0px 8px; margin-top: 12px; margin-bottom: 0px;">'
            '  <div style="display: flex; align-items: center; justify-content: center; '
            'height: 20px;">'
            '    <span style="color: #f0b90b; font-size: 16px; font-weight: 700; '
            'letter-spacing: 0.5px;">KPIs INTERNOS</span>'
            "  </div>"
            '  <div style="margin-top: 12px;">'
            '    <div style="display: flex; justify-content: space-between; align-items: '
            'baseline; margin-bottom: 8px;">'
            '      <span style="color: #848e9c; font-size: 13px;">Momentum:</span>'
            f'      <span style="color: #ffffff; font-size: 13px; font-weight: 600;">'
            f"{kpi_momentum:.2f} "
            '      <span style="color: #848e9c; font-size: 12px;">'
            f"(thr {kpi_momentum_thr:.1f})</span></span>"
            "    </div>"
            '    <div style="display: flex; justify-content: space-between; align-items: '
            'baseline; margin-bottom: 8px;">'
            '      <span style="color: #848e9c; font-size: 13px;">Vol relativo:</span>'
            f'      <span style="color: #ffffff; font-size: 13px; font-weight: 600;">'
            f"{kpi_vol_rel:.1f}× "
            '      <span style="color: #848e9c; font-size: 12px;">'
            f"(thr {kpi_vol_rel_thr:.1f}×)</span></span>"
            "    </div>"
            '    <div style="display: flex; justify-content: space-between; align-items: '
            'baseline; margin-bottom: 8px;">'
            '      <span style="color: #848e9c; font-size: 13px;">Imbalance:</span>'
            f'      <span style="color: #ffffff; font-size: 13px; font-weight: 600;">'
            f"{kpi_imbalance:.0f}% "
            '      <span style="color: #848e9c; font-size: 12px;">'
            f"(thr {kpi_imbalance_thr:.0f}%)</span></span>"
            "    </div>"
            '    <div style="display: flex; justify-content: space-between; align-items: '
            'baseline;">'
            '      <span style="color: #848e9c; font-size: 13px;">Volatilidad:</span>'
            f'      <span style="color: #ffffff; font-size: 13px; font-weight: 600;">'
            f"{kpi_volatility:.2f}% "
            '      <span style="color: #848e9c; font-size: 12px;">'
            f"(max {kpi_volatility_max:.1f}%)</span></span>"
            "    </div>"
            "  </div>"
            "</div>"
        )

        st.markdown(kpis_block_html, unsafe_allow_html=True)

        # Decisión del bot - Leer último trade
        last_action = "—"
        last_action_time = "—"
        last_reason = "—"
        last_pnl = "—"
        last_position = "—"

        if trades_file.exists():
            from datetime import datetime

            import pandas as pd

            tr_df = pd.read_csv(trades_file)
            if not tr_df.empty:
                last_trade = tr_df.iloc[-1]

                # Acción y precio
                side = last_trade.get("side", "").upper()
                trade_price = last_trade.get("price", 0.0)
                trade_qty = last_trade.get("qty", 0.0)

                # Hora del trade
                trade_ts = last_trade.get("timestamp", 0.0)
                if trade_ts > 0:
                    trade_time = datetime.fromtimestamp(trade_ts).strftime("%H:%M:%S")
                    last_action_time = trade_time

                last_action = f"{side} @ {trade_price:,.2f}"

                # Motivo
                reason = last_trade.get("reason", "—")
                if reason and reason != "—":
                    # Simplificar motivo para display compacto
                    if "entry" in reason.lower() or "signal" in reason.lower():
                        last_reason = "señal > thr long"
                    elif "exit" in reason.lower():
                        last_reason = "señal < thr exit"
                    elif "stop" in reason.lower():
                        last_reason = "stop loss"
                    elif "profit" in reason.lower():
                        last_reason = "take profit"
                    else:
                        last_reason = reason[:30]  # Truncar si es muy largo
                else:
                    last_reason = "—"

                # PnL de la acción (diferencia de equity antes/después si hay más de 1 trade)
                if len(tr_df) >= 2:
                    prev_equity = tr_df.iloc[-2].get("equity", 10000.0)
                    curr_equity = last_trade.get("equity", 10000.0)
                    action_pnl = curr_equity - prev_equity
                    pnl_sign = "+" if action_pnl >= 0 else ""
                    last_pnl = f"{pnl_sign}{action_pnl:.2f} USDT"
                else:
                    last_pnl = "0.00 USDT"

                # Nueva posición resultante
                last_position = f"{trade_qty:.4f} BTC" if side == "BUY" else "0.0000 BTC"

        decision_block_html = (
            '<div style="padding: 0px 8px; margin-top: 16px; margin-bottom: 0px;">'
            '  <div style="display: flex; align-items: center; justify-content: center; '
            'height: 20px;">'
            '    <span style="color: #f0b90b; font-size: 14px; font-weight: 700; '
            'letter-spacing: 0.5px;">DECISIÓN DEL BOT</span>'
            "  </div>"
            '  <div style="border-bottom: 1px solid #2b3139; margin: 6px 0px;"></div>'
            '  <div style="margin-top: 8px;">'
            # Acción
            '    <div style="margin-bottom: 6px;">'
            '      <span style="color: #848e9c; font-size: 12px;">Acción: </span>'
            f'      <span style="color: #ffffff; font-size: 12px; font-weight: 600;">{last_action}</span>'
            f'      <span style="color: #848e9c; font-size: 11px;"> ({last_action_time})</span>'
            "    </div>"
            # Motivo
            '    <div style="margin-bottom: 6px;">'
            '      <span style="color: #848e9c; font-size: 12px;">Motivo: </span>'
            f'      <span style="color: #ffffff; font-size: 12px;">{last_reason}</span>'
            "    </div>"
            # PnL acción
            '    <div style="margin-bottom: 6px;">'
            '      <span style="color: #848e9c; font-size: 12px;">PNL acción: </span>'
            f'      <span style="color: #ffffff; font-size: 12px; font-weight: 600;">{last_pnl}</span>'
            "    </div>"
            # Nueva posición
            "    <div>"
            '      <span style="color: #848e9c; font-size: 12px;">Nueva posición: </span>'
            f'      <span style="color: #ffffff; font-size: 12px; font-weight: 600;">{last_position}</span>'
            "    </div>"
            "  </div>"
            "</div>"
        )

        st.markdown(decision_block_html, unsafe_allow_html=True)

    # Auto-refresh
    if has_new_data:
        time.sleep(1.0)
    else:
        time.sleep(0.3)

    st.rerun()

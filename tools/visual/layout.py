"""Layout principal del dashboard de trading en vivo."""

from pathlib import Path
import time

import pandas as pd
import streamlit as st

try:
    from tools.visual.chart_ohlc import render_ohlc_volume
    from tools.visual.components.decision_panel import render_decision_panel
    from tools.visual.components.metrics_header import render_header
    from tools.visual.components.signal_panel import render_signal_panel
    from tools.visual.components.timeframe import render_timeframe_selector
    from tools.visual.kill_switch import handle_kill_switch
except ModuleNotFoundError:
    from chart_ohlc import render_ohlc_volume
    from components.decision_panel import render_decision_panel
    from components.metrics_header import render_header
    from components.signal_panel import render_signal_panel
    from components.timeframe import render_timeframe_selector
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

    # Selector de timeframe y estado
    tf_options = ["1s", "5s", "10s", "30s", "1m", "5m", "1h"]
    render_timeframe_selector(tf_options)

    data_file = Path(run_dir) / "data.csv"
    chart_file = Path(run_dir) / "chart.csv"

    def _safe_read(path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        try:
            return pd.read_csv(path)
        except Exception:
            return None

    df_micro = _safe_read(data_file)
    df_chart = _safe_read(chart_file)

    # Variable para controlar si hay datos nuevos
    has_new_data = False

    # Inicializar contador de última fila procesada para evitar parpadeos
    if "last_row_count" not in st.session_state:
        st.session_state.last_row_count = 0

    source_df = df_chart if df_chart is not None else df_micro
    if source_df is not None:
        current_row_count = len(source_df)
        if current_row_count > st.session_state.last_row_count:
            has_new_data = True
            st.session_state.last_row_count = current_row_count

    # Layout principal: 2 columnas (gráfico ancho + panel info)
    col_main, col_info = st.columns([6.5, 2])

    # Nota: el bloque completo de "POSICIÓN" se renderiza más abajo como un único contenedor

    with col_main:
        metrics_df = df_chart if df_chart is not None else df_micro

        if metrics_df is not None and not metrics_df.empty and len(metrics_df) >= 2:
            current_price = metrics_df["close"].iloc[-1]

            session_start_price = metrics_df["close"].iloc[0]
            price_change_pct = ((current_price - session_start_price) / session_start_price) * 100

            session_high = metrics_df["high"].max()
            session_low = metrics_df["low"].min()

            last_timestamp_second = int(metrics_df["timestamp"].iloc[-1])
            last_second_data = metrics_df[
                metrics_df["timestamp"].apply(lambda x: int(x) == last_timestamp_second)
            ]
            last_volume_btc = last_second_data["volume"].sum()
            last_avg_price = last_second_data["close"].mean()
            last_volume = last_volume_btc * last_avg_price

            start_time = metrics_df["timestamp"].iloc[0]
            current_time = metrics_df["timestamp"].iloc[-1]
            elapsed_seconds = int(current_time - start_time)
            elapsed_minutes = elapsed_seconds // 60
            elapsed_secs = elapsed_seconds % 60

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

        # Fila superior con métricas en tiempo real
        metrics = {
            "current_price": current_price,
            "price_change_pct": price_change_pct,
            "session_high": session_high,
            "session_low": session_low,
            "last_volume": last_volume,
            "elapsed_minutes": elapsed_minutes,
            "elapsed_secs": elapsed_secs,
            "change_color": change_color,
        }
        render_header(metrics)

        st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
        # Gráfico combinado
        render_ohlc_volume(run_dir, st.session_state.selected_tf, width=900, height=600)

    with col_info:
        # Leer datos de equity
        equity_file = Path(run_dir) / "equity.csv"
        trades_file = Path(run_dir) / "trades.csv"
        eq_df: pd.DataFrame | None = None
        tr_df: pd.DataFrame | None = None

        current_qty = 0.0
        current_equity = 10000.0
        position_value = 0.0
        avg_entry_price = 0.0
        pnl_usdt = 0.0
        pnl_pct = 0.0
        exposure_pct = 0.0
        initial_cash = 10000.0

        if equity_file.exists():
            try:
                eq_df = pd.read_csv(equity_file)
            except Exception:
                eq_df = None
            if eq_df is not None and not eq_df.empty:
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
            try:
                tr_df = pd.read_csv(trades_file)
            except Exception:
                tr_df = None
            if tr_df is not None and not tr_df.empty:
                tr_df["side_norm"] = tr_df["side"].astype(str).str.upper()
                buys = tr_df[tr_df["side_norm"] == "BUY"]
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

        # Señal y barra visuales
        render_signal_panel(run_dir, df_micro)

        # KPIs internos
        # TODO: Obtener valores reales de la estrategia cuando estén disponibles
        render_decision_panel(run_dir, df_micro, tr_df)

    # Auto-refresh
    if has_new_data:
        time.sleep(1.0)
    else:
        time.sleep(0.3)

    st.rerun()

from pathlib import Path
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def _resample_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Reagrupa micro-velas en timeframe específico (1m, 5m, 15m, etc)."""
    if df.empty or "timestamp" not in df.columns:
        return df

    # Asegurar datetime index
    df_copy = df.copy()
    df_copy["dt"] = pd.to_datetime(df_copy["timestamp"], unit="s")
    df_copy = df_copy.set_index("dt")

    # Mapeo de timeframe a pandas resample rule
    tf_map = {
        "1s": "1s",
        "5s": "5s",
        "10s": "10s",
        "30s": "30s",
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
    }

    rule = tf_map.get(timeframe, "1min")

    # Reagrupar OHLCV
    resampled = (
        df_copy.resample(rule)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )

    # Restaurar timestamp como columna
    resampled["timestamp"] = resampled.index.astype(np.int64) // 10**9
    resampled = resampled.reset_index(drop=True)

    return resampled


def _get_available_timeframes(duration_sec: float) -> list[str]:
    """Determina qué timeframes tienen sentido según duración de datos."""
    # Heurística: mostrar TF si habrá al menos 10 velas
    all_tfs = {
        "1s": 1,
        "5s": 5,
        "10s": 10,
        "30s": 30,
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
    }

    available = []
    for tf, sec_per_bar in all_tfs.items():
        if duration_sec / sec_per_bar >= 10:
            available.append(tf)

    return available if available else ["1s"]  # fallback


def render_ohlc(run_dir: str):
    data_file = Path(run_dir) / "data.csv"
    refresh_sec = 1
    kill_file = Path(run_dir) / "KILL"

    # Menú horizontal fijo estilo Binance (fuera del loop) con botones muy compactos
    st.markdown(
        """
        <style>
        .stButton > button {
            padding: 2px 6px !important;
            font-size: 10px !important;
            font-weight: 500 !important;
            min-width: 28px !important;
            height: 24px !important;
            margin: 0 2px !important;
        }
        </style>
    """,
        unsafe_allow_html=True,
    )

    # Selector de timeframe fijo (estilo Binance) - más compacto
    tf_options = ["1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "4h"]
    cols = st.columns(len(tf_options))

    # Inicializar timeframe en session_state
    if "selected_tf" not in st.session_state:
        st.session_state.selected_tf = "1s"

    for idx, tf in enumerate(tf_options):
        with cols[idx]:
            if st.button(tf, key=f"tf_{tf}"):
                st.session_state.selected_tf = tf
                try:
                    st.rerun()
                except Exception:
                    try:
                        st.experimental_rerun()
                    except Exception:
                        pass

    selected_tf = st.session_state.selected_tf

    # Estado para controlar re-render sólo cuando cambian datos o timeframe
    if "last_plot_ts" not in st.session_state:
        st.session_state.last_plot_ts = None
    if "last_plot_tf" not in st.session_state:
        st.session_state.last_plot_tf = selected_tf

    # Placeholder único para el gráfico (evita columnas múltiples)
    chart_placeholder = st.empty()

    # Loop de actualización
    iteration = 0
    while True:
        iteration += 1
        if kill_file.exists():
            chart_placeholder.warning(
                "Ejecución detenida por el usuario. Puedes cerrar la ventana."
            )
            break
        if not data_file.exists():
            chart_placeholder.info("Esperando datos...")
            time.sleep(refresh_sec)
            continue
        df = pd.read_csv(data_file)
        if len(df) == 0:
            chart_placeholder.info("Cargando velas...")
            time.sleep(refresh_sec)
            continue

        # Reagrupar según timeframe seleccionado
        if selected_tf != "1s":
            df_plot = _resample_bars(df, selected_tf)
        else:
            df_plot = df

        # Limitar a últimas 60 velas para mejor visualización
        df_plot = df_plot.tail(60).copy()  # .copy() evita SettingWithCopyWarning

        # Evitar re-render si no hay cambios (misma última vela y mismo TF)
        try:
            last_ts_val = float(df_plot.iloc[-1]["timestamp"])
        except Exception:
            last_ts_val = None
        if (
            last_ts_val is not None
            and st.session_state.last_plot_ts == last_ts_val
            and st.session_state.last_plot_tf == selected_tf
        ):
            time.sleep(refresh_sec)
            continue

        # Convertir eje X según timeframe: categoría para 1s, fecha para otros
        if selected_tf == "1s":
            x_vals = [str(i) for i in range(len(df_plot))]
        else:
            if "timestamp" in df_plot.columns:
                try:
                    df_plot["dt"] = pd.to_datetime(df_plot["timestamp"], unit="s")
                    x_vals = df_plot["dt"]
                except Exception:
                    x_vals = df_plot["timestamp"]
            else:
                x_vals = df_plot.index
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=x_vals,
                    open=df_plot["open"],
                    high=df_plot["high"],
                    low=df_plot["low"],
                    close=df_plot["close"],
                    increasing_line_color="#0ecb81",  # Binance green
                    decreasing_line_color="#f6465d",  # Binance red
                    increasing_fillcolor="#0ecb81",
                    decreasing_fillcolor="#f6465d",
                )
            ]
        )

        # Overlay de trades siempre visible (si existe trades.csv)
        trades_file = Path(run_dir) / "trades.csv"
        if trades_file.exists():
            try:
                tdf = pd.read_csv(trades_file)
                if (
                    not tdf.empty
                    and "timestamp" in tdf.columns
                    and "side" in tdf.columns
                    and "price" in tdf.columns
                ):
                    tdf["dt"] = pd.to_datetime(tdf["timestamp"], unit="s", errors="coerce")
                    buys = (
                        tdf[tdf["side"] == "BUY"].dropna(subset=["dt"])
                        if not tdf.empty
                        else pd.DataFrame()
                    )
                    sells = (
                        tdf[tdf["side"] == "SELL"].dropna(subset=["dt"])
                        if not tdf.empty
                        else pd.DataFrame()
                    )
                    if selected_tf == "1s":
                        # Mapear cada trade al índice de vela más reciente <= ts
                        arr = df_plot["timestamp"].to_numpy()
                        if not buys.empty:
                            idxs = np.clip(
                                np.searchsorted(arr, buys["timestamp"].to_numpy(), side="right")
                                - 1,
                                0,
                                len(arr) - 1,
                            )
                            fig.add_trace(
                                go.Scatter(
                                    x=[str(int(i)) for i in idxs],
                                    y=buys["price"],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-up",
                                        color="#0ecb81",
                                        size=10,
                                        line=dict(width=1.5, color="#0b0e11"),
                                    ),
                                    name="COMPRA",
                                    showlegend=True,
                                )
                            )
                        if not sells.empty:
                            idxs = np.clip(
                                np.searchsorted(arr, sells["timestamp"].to_numpy(), side="right")
                                - 1,
                                0,
                                len(arr) - 1,
                            )
                            fig.add_trace(
                                go.Scatter(
                                    x=[str(int(i)) for i in idxs],
                                    y=sells["price"],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-down",
                                        color="#f6465d",
                                        size=10,
                                        line=dict(width=1.5, color="#0b0e11"),
                                    ),
                                    name="VENTA",
                                    showlegend=True,
                                )
                            )
                    else:
                        if not buys.empty:
                            fig.add_trace(
                                go.Scatter(
                                    x=buys["dt"],
                                    y=buys["price"],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-up",
                                        color="#0ecb81",
                                        size=10,
                                        line=dict(width=1.5, color="#0b0e11"),
                                    ),
                                    name="COMPRA",
                                    showlegend=True,
                                )
                            )
                        if not sells.empty:
                            fig.add_trace(
                                go.Scatter(
                                    x=sells["dt"],
                                    y=sells["price"],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-down",
                                        color="#f6465d",
                                        size=10,
                                        line=dict(width=1.5, color="#0b0e11"),
                                    ),
                                    name="VENTA",
                                    showlegend=True,
                                )
                            )
            except Exception:
                pass
        # Configuración de eje temporal estilo Binance
        # - Eje de fecha siempre ('date') para formateo inteligente
        # - Pocas marcas (nticks)
        # - tickformatstops para formato dinámico según escala
        dtick_map = {
            "1s": None,
            "5s": None,
            "10s": None,
            "30s": None,
            "1m": "T1",
            "5m": "T5",
            "15m": "T15",
            "30m": "T30",
            "1h": "H1",
            "4h": "H4",
        }

        tickformatstops = [
            dict(dtickrange=[None, 60_000], value="%H:%M:%S"),  # < 1 min
            dict(dtickrange=[60_000, 3_600_000], value="%H:%M"),  # 1m–1h
            dict(dtickrange=[3_600_000, 86_400_000], value="%d %b %H:%M"),  # 1h–1d
            dict(dtickrange=[86_400_000, None], value="%d %b"),  # > 1d
        ]

        fig.update_layout(
            xaxis_rangeslider_visible=False,
            height=600,
            template="plotly_dark",
            paper_bgcolor="#0b0e11",
            plot_bgcolor="#0b0e11",
            font=dict(color="#848e9c", size=12),
            uirevision="chart",  # mantiene estado/estilo entre actualizaciones y evita parpadeos
            xaxis=dict(
                type=("category" if selected_tf == "1s" else "date"),
                showgrid=True,
                gridcolor="#2b3139",
                gridwidth=0.5,
                tickmode="auto",
                nticks=6,
                dtick=dtick_map.get(selected_tf),
                tickformatstops=tickformatstops,
                tickfont=dict(size=11),
                ticks="outside",
                ticklen=4,
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor="#2b3139",
                gridwidth=0.5,
            ),
            margin=dict(l=10, r=10, t=30, b=10),
            # Ancho de barras fijo para mejor visualización (separación)
            bargap=0.15,
        )

        # Renderizar gráfico único que se actualiza en el mismo lugar
        chart_placeholder.plotly_chart(fig, width="stretch")

        # Actualizar memoria para evitar re-renders innecesarios
        st.session_state.last_plot_ts = last_ts_val
        st.session_state.last_plot_tf = selected_tf

        time.sleep(refresh_sec)

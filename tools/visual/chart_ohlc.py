"""Componente del gráfico OHLC con dimensiones fijas."""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st


def _resample_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Reagrupa micro-velas en timeframe específico."""
    if df.empty or "timestamp" not in df.columns:
        return df

    df_copy = df.copy()

    # Para timeframe de 1s, agrupar por segundo completo (redondear timestamp)
    if timeframe == "1s":
        # Redondear timestamp al segundo más cercano (floor)
        df_copy["ts_second"] = df_copy["timestamp"].apply(lambda x: int(x))

        # Agrupar por segundo y agregar OHLCV + dollar_value
        agg_dict = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        if "dollar_value" in df_copy.columns:
            agg_dict["dollar_value"] = "sum"

        grouped = df_copy.groupby("ts_second").agg(agg_dict).reset_index()

        # Renombrar columna de vuelta a timestamp
        grouped = grouped.rename(columns={"ts_second": "timestamp"})

        # Usar dollar_value si existe, sino calcular (fallback)
        if "dollar_value" in grouped.columns:
            grouped["volume_usdt"] = grouped["dollar_value"]
        else:
            grouped["volume_usdt"] = grouped["volume"] * grouped["close"]

        return grouped

    # Para otros timeframes, usar resample normal
    df_copy["dt"] = pd.to_datetime(df_copy["timestamp"], unit="s")
    df_copy = df_copy.set_index("dt")

    tf_map = {
        "5s": "5s",
        "10s": "10s",
        "30s": "30s",
        "1m": "1min",
        "5m": "5min",
        "1h": "1h",
    }

    rule = tf_map.get(timeframe, "5s")

    # Usar dollar_value si existe, sino calcular volumen USDT
    agg_dict = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }

    if "dollar_value" in df_copy.columns:
        agg_dict["dollar_value"] = "sum"
    else:
        df_copy["volume_usdt"] = df_copy["volume"] * df_copy["close"]
        agg_dict["volume_usdt"] = "sum"

    resampled = df_copy.resample(rule).agg(agg_dict).dropna()

    # Asignar volume_usdt desde dollar_value si existe
    if "dollar_value" in resampled.columns:
        resampled["volume_usdt"] = resampled["dollar_value"]

    resampled["timestamp"] = resampled.index.astype(np.int64) // 10**9
    resampled = resampled.reset_index(drop=True)

    return resampled


def render_chart_ohlc(run_dir: str, timeframe: str, width: int = 900, height: int = 600):
    """Renderiza gráfico OHLC con dimensiones fijas."""
    # Usar chart.csv (barras de tiempo) en lugar de data.csv (micro-velas)
    data_file = Path(run_dir) / "chart.csv"

    # Placeholder mientras carga
    placeholder = st.empty()

    if not data_file.exists():
        placeholder.markdown(
            (
                '<div style="color: #848e9c; text-align: center; '
                'padding-top: 280px;">⏳ Esperando datos...</div>'
            ),
            unsafe_allow_html=True,
        )
        return

    try:
        df = pd.read_csv(data_file)
        if df.empty or len(df) < 2:
            placeholder.markdown(
                (
                    '<div style="color: #848e9c; text-align: center; '
                    'padding-top: 280px;">📊 Procesando primeras velas...</div>'
                ),
                unsafe_allow_html=True,
            )
            return

        # Resamplear según timeframe
        df_resampled = _resample_bars(df, timeframe)

        if df_resampled.empty:
            placeholder.markdown(
                (
                    '<div style="color: #848e9c; text-align: center; '
                    'padding-top: 280px;">📊 Insuficientes datos para este timeframe</div>'
                ),
                unsafe_allow_html=True,
            )
            return

        # Limitar ESTRICTAMENTE a últimas 100 barras
        df_resampled = df_resampled.tail(100).reset_index(drop=True)

        # Crear etiquetas de tiempo para el eje X
        df_resampled["dt"] = pd.to_datetime(df_resampled["timestamp"], unit="s")
        # Convertir a horario de España (CET/CEST - UTC+1/UTC+2)
        df_resampled["dt_spain"] = (
            df_resampled["dt"].dt.tz_localize("UTC").dt.tz_convert("Europe/Madrid")
        )
        df_resampled["time_label"] = df_resampled["dt_spain"].dt.strftime("%H:%M:%S")
        df_resampled["datetime_label"] = df_resampled["dt_spain"].dt.strftime("%Y-%m-%d %H:%M:%S")

        # Para timeframes de segundos, mostrar etiquetas cada 30 segundos
        if timeframe in ["1s", "5s", "10s"]:
            # Mostrar solo etiquetas cuando los segundos son múltiplo de 30
            df_resampled["show_label"] = df_resampled["dt_spain"].dt.second % 30 == 0
            df_resampled["x_label"] = df_resampled.apply(
                lambda row: row["time_label"] if row["show_label"] else "", axis=1
            )
            # Crear lista de índices donde mostrar gridlines (cada 30 segundos)
            gridline_indices = df_resampled[df_resampled["show_label"]].index.tolist()
        else:
            # Para otros timeframes, usar las etiquetas normales
            df_resampled["x_label"] = df_resampled["time_label"]
            gridline_indices = []

        # Leer trades para marcar entradas/salidas
        trades_file = Path(run_dir) / "trades.csv"
        buy_times = []
        sell_times = []
        buy_prices = []
        sell_prices = []

        if trades_file.exists():
            trades = pd.read_csv(trades_file)
            if not trades.empty and "timestamp" in trades.columns:
                trades["side_norm"] = trades["side"].astype(str).str.upper()
                buys = trades[trades["side_norm"] == "BUY"]
                sells = trades[trades["side_norm"] == "SELL"]
                buy_times = buys["timestamp"].tolist()
                sell_times = sells["timestamp"].tolist()
                buy_prices = buys["price"].tolist()
                sell_prices = sells["price"].tolist()

        # Usar container para reducir parpadeo
        with placeholder.container():
            # Crear gráfico con tema Binance
            fig = go.Figure()

            # Velas OHLC
            fig.add_trace(
                go.Candlestick(
                    x=df_resampled.index,
                    open=df_resampled["open"],
                    high=df_resampled["high"],
                    low=df_resampled["low"],
                    close=df_resampled["close"],
                    increasing_line_color="#0ecb81",
                    decreasing_line_color="#f6465d",
                    name="OHLC",
                    customdata=df_resampled["datetime_label"],
                    hovertext=df_resampled["datetime_label"],
                )
            )

        # Marcadores de compra
        if buy_times:
            buy_indices = []
            for ts in buy_times:
                idx = (df_resampled["timestamp"] - ts).abs().idxmin()
                buy_indices.append(idx)

            fig.add_trace(
                go.Scatter(
                    x=buy_indices,
                    y=buy_prices,
                    mode="markers",
                    marker=dict(symbol="triangle-up", size=12, color="#0ecb81"),
                    name="Compra",
                    showlegend=False,
                )
            )

        # Marcadores de venta
        if sell_times:
            sell_indices = []
            for ts in sell_times:
                idx = (df_resampled["timestamp"] - ts).abs().idxmin()
                sell_indices.append(idx)

            fig.add_trace(
                go.Scatter(
                    x=sell_indices,
                    y=sell_prices,
                    mode="markers",
                    marker=dict(symbol="triangle-down", size=12, color="#f6465d"),
                    name="Venta",
                    showlegend=False,
                )
            )

        # Calcular rango del eje X para mostrar exactamente las últimas 100 barras
        x_max = len(df_resampled) - 1
        x_min = max(0, x_max - 99)

        # Filtrar etiquetas solo para las barras visibles
        _ = df_resampled.iloc[x_min : x_max + 1].copy()

        # Layout con dimensiones fijas
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#1e2329",
            plot_bgcolor="#1e2329",
            font=dict(color="#eaecef", size=10),
            xaxis_rangeslider_visible=False,  # Ocultar mini-gráfico (rangeslider)
            xaxis=dict(
                showgrid=False,  # Desactivar gridlines automáticas
                showline=True,  # Mostrar línea del eje X
                linecolor="#2b3139",
                showticklabels=False,  # Ocultar etiquetas (las mostrará el gráfico de volumen)
                tickangle=0,
                fixedrange=True,
                range=[x_min - 0.5, x_max + 0.5],  # Padding para que las velas no se corten
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor="#2b3139",
                showline=True,
                linecolor="#2b3139",
                side="left",  # Eje a la izquierda
                fixedrange=True,
                nticks=5,  # Solo 5 líneas horizontales
            ),
            margin=dict(l=60, r=0, t=10, b=0),  # Sin margen inferior
            hovermode="x unified",
            hoverlabel=dict(
                bgcolor="#1e2329",
                font_size=11,
                font_color="#eaecef",
                namelength=-1,  # Mostrar nombre completo
            ),
            width=width,
            height=height,
            dragmode=False,
            uirevision=timeframe,  # Cambiar al cambiar timeframe, mantener dentro del mismo
        )

        # Agregar líneas verticales manualmente cada 30 segundos (solo para las visibles)
        if gridline_indices:
            visible_gridlines = [idx for idx in gridline_indices if x_min <= idx <= x_max]
            for idx in visible_gridlines:
                fig.add_shape(
                    type="line",
                    x0=idx,
                    x1=idx,
                    y0=0,
                    y1=1,
                    yref="paper",
                    line=dict(color="#2b3139", width=1),
                    layer="below",
                )

        # Deshabilitar todas las herramientas de interacción
        config = {
            "displayModeBar": False,
            "staticPlot": False,
            "scrollZoom": False,
            "doubleClick": False,
        }

        placeholder.plotly_chart(fig, config=config, key=f"ohlc_{timeframe}")

    except Exception as e:
        placeholder.markdown(
            f'<div style="color: #f6465d; text-align: center; padding-top: 280px;">❌ Error: {e}</div>',
            unsafe_allow_html=True,
        )


def render_volume_chart(run_dir: str, timeframe: str, width: int = 900, height: int = 150):
    """Renderiza gráfico de volumen con dimensiones fijas."""
    # Usar chart.csv (barras de tiempo) en lugar de data.csv (micro-velas)
    data_file = Path(run_dir) / "chart.csv"

    # Placeholder mientras carga
    placeholder = st.empty()

    if not data_file.exists():
        return

    try:
        df = pd.read_csv(data_file)
        if df.empty or len(df) < 2:
            return

        df_resampled = _resample_bars(df, timeframe)

        if df_resampled.empty:
            return

        # Limitar ESTRICTAMENTE a últimas 100 barras
        df_resampled = df_resampled.tail(100).reset_index(drop=True)

        # Crear etiquetas de tiempo
        df_resampled["dt"] = pd.to_datetime(df_resampled["timestamp"], unit="s")
        # Convertir a horario de España (CET/CEST - UTC+1/UTC+2)
        df_resampled["dt_spain"] = (
            df_resampled["dt"].dt.tz_localize("UTC").dt.tz_convert("Europe/Madrid")
        )
        df_resampled["time_label"] = df_resampled["dt_spain"].dt.strftime("%H:%M:%S")

        # Para timeframes de segundos, mostrar etiquetas cada 30 segundos
        if timeframe in ["1s", "5s", "10s"]:
            df_resampled["show_label"] = df_resampled["dt_spain"].dt.second % 30 == 0
            df_resampled["x_label"] = df_resampled.apply(
                lambda row: row["time_label"] if row["show_label"] else "", axis=1
            )
        else:
            df_resampled["x_label"] = df_resampled["time_label"]

        # Colores para barras de volumen (verde si close > open)
        colors = [
            "#0ecb81" if row["close"] >= row["open"] else "#f6465d"
            for _, row in df_resampled.iterrows()
        ]

        # Usar container para reducir parpadeo
        with placeholder.container():
            fig = go.Figure()

            fig.add_trace(
                go.Bar(
                    x=df_resampled.index,
                    y=df_resampled["volume_usdt"],
                    marker_color=colors,
                    name="Volumen (USDT)",
                    showlegend=False,
                )
            )

        # Calcular rango del eje X para mostrar exactamente las últimas 100 barras
        x_max = len(df_resampled) - 1
        x_min = max(0, x_max - 99)

        # Filtrar etiquetas solo para las barras visibles
        df_visible = df_resampled.iloc[x_min : x_max + 1].copy()

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#1e2329",
            plot_bgcolor="#1e2329",
            font=dict(color="#eaecef", size=10),
            xaxis=dict(
                showgrid=False,  # Sin gridlines verticales
                showline=True,  # Mostrar línea del eje X
                linecolor="#2b3139",
                showticklabels=True,  # Mostrar etiquetas de tiempo
                ticktext=df_visible["x_label"].tolist(),
                tickvals=df_visible.index.tolist(),
                fixedrange=True,
                range=[x_min - 0.5, x_max + 0.5],  # Padding para que las barras no se corten
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor="#2b3139",
                showline=True,
                linecolor="#2b3139",
                side="left",  # Eje a la izquierda
                fixedrange=True,
                nticks=4,  # Solo 4 líneas horizontales
            ),
            margin=dict(l=60, r=0, t=0, b=30),
            hovermode="x unified",
            width=width,
            height=height,
            dragmode=False,
            uirevision=timeframe,
        )

        config = {
            "displayModeBar": False,
            "staticPlot": False,
            "scrollZoom": False,
            "doubleClick": False,
        }

        placeholder.plotly_chart(fig, config=config, key=f"volume_{timeframe}")

    except Exception:
        pass


def render_ohlc_volume(run_dir: str, timeframe: str, width: int = 900, height: int = 600):
    """Renderiza OHLC + Volumen como un solo gráfico (subplots) sin separación."""
    data_file = Path(run_dir) / "chart.csv"
    trades_file = Path(run_dir) / "trades.csv"

    placeholder = st.empty()

    if not data_file.exists():
        return

    try:
        df = pd.read_csv(data_file)
        if df.empty or len(df) < 2:
            return

        df_resampled = _resample_bars(df, timeframe)
        if df_resampled.empty:
            return

        # Limitar a últimas 100 barras
        df_resampled = df_resampled.tail(100).reset_index(drop=True)

        # Tiempos y etiquetas (España)
        df_resampled["dt"] = pd.to_datetime(df_resampled["timestamp"], unit="s")
        df_resampled["dt_spain"] = (
            df_resampled["dt"].dt.tz_localize("UTC").dt.tz_convert("Europe/Madrid")
        )
        df_resampled["time_label"] = df_resampled["dt_spain"].dt.strftime("%H:%M:%S")
        df_resampled["datetime_label"] = df_resampled["dt_spain"].dt.strftime("%Y-%m-%d %H:%M:%S")

        if timeframe in ["1s", "5s", "10s"]:
            df_resampled["show_label"] = df_resampled["dt_spain"].dt.second % 30 == 0
            df_resampled["x_label"] = df_resampled.apply(
                lambda row: row["time_label"] if row["show_label"] else "", axis=1
            )
            gridline_indices = df_resampled[df_resampled["show_label"]].index.tolist()
        else:
            df_resampled["x_label"] = df_resampled["time_label"]
            gridline_indices = []

        # Colores de volumen
        colors = [
            "#0ecb81" if row["close"] >= row["open"] else "#f6465d"
            for _, row in df_resampled.iterrows()
        ]
        # Map timeframe to approximate second span for marker matching
        tf_seconds = {
            "1s": 1,
            "5s": 5,
            "10s": 10,
            "30s": 30,
            "1m": 60,
            "5m": 300,
            "1h": 3600,
        }.get(timeframe, 60)

        buy_markers: dict[str, list] = {"x": [], "y": [], "text": []}
        sell_markers: dict[str, list] = {"x": [], "y": [], "text": []}
        bar_ts = df_resampled["timestamp"].to_numpy(dtype=float)
        if trades_file.exists() and len(bar_ts) > 0:
            try:
                trades_df = pd.read_csv(trades_file)
            except Exception:
                trades_df = None
            if trades_df is not None and not trades_df.empty:
                recent_trades = trades_df.tail(500)
                for _, trade in recent_trades.iterrows():
                    try:
                        trade_ts = float(trade.get("timestamp", 0.0))
                    except Exception:
                        continue
                    if trade_ts <= 0:
                        continue
                    idx = int(np.abs(bar_ts - trade_ts).argmin())
                    if idx < 0 or idx >= len(df_resampled):
                        continue
                    # asegurar que el trade cae dentro del rango visible
                    if abs(bar_ts[idx] - trade_ts) > max(1.0, tf_seconds):
                        continue
                    bar_row = df_resampled.iloc[idx]
                    side = str(trade.get("side", "")).upper()
                    price = float(trade.get("price", bar_row["close"]))
                    qty = float(trade.get("qty", 0.0))
                    reason = str(trade.get("reason", ""))
                    hover = (
                        f"{side} {qty:.4f} @ {price:,.2f}<br>"
                        f"{datetime.fromtimestamp(trade_ts).strftime('%H:%M:%S')}<br>{reason}"
                    )
                    target = buy_markers if side == "BUY" else sell_markers
                    y_anchor = bar_row["high"] if side == "BUY" else bar_row["low"]
                    target["x"].append(idx)
                    target["y"].append(y_anchor)
                    target["text"].append(hover)

        # Rango de X
        x_max = len(df_resampled) - 1
        x_min = max(0, x_max - 99)
        df_visible = df_resampled.iloc[x_min : x_max + 1].copy()

        with placeholder.container():
            fig = make_subplots(
                rows=2,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,  # pequeño espacio entre gráficos
                row_heights=[0.7, 0.3],
            )

            # OHLC
            fig.add_trace(
                go.Candlestick(
                    x=df_resampled.index,
                    open=df_resampled["open"],
                    high=df_resampled["high"],
                    low=df_resampled["low"],
                    close=df_resampled["close"],
                    increasing_line_color="#0ecb81",
                    decreasing_line_color="#f6465d",
                    name="OHLC",
                    customdata=df_resampled["datetime_label"],
                    hovertext=df_resampled["datetime_label"],
                ),
                row=1,
                col=1,
            )

            # Volumen
            fig.add_trace(
                go.Bar(
                    x=df_resampled.index,
                    y=df_resampled["volume_usdt"],
                    marker_color=colors,
                    name="Volumen (USDT)",
                    showlegend=False,
                ),
                row=2,
                col=1,
            )

            if buy_markers["x"]:
                fig.add_trace(
                    go.Scatter(
                        x=buy_markers["x"],
                        y=buy_markers["y"],
                        mode="markers",
                        marker=dict(
                            symbol="triangle-up",
                            size=14,
                            color="#f0b90b",
                            line=dict(color="#0b0e11", width=2),
                            opacity=0.95,
                        ),
                        name="Buy",
                        hovertext=buy_markers["text"],
                        hoverinfo="text",
                        showlegend=False,
                    ),
                    row=1,
                    col=1,
                )
            if sell_markers["x"]:
                fig.add_trace(
                    go.Scatter(
                        x=sell_markers["x"],
                        y=sell_markers["y"],
                        mode="markers",
                        marker=dict(
                            symbol="triangle-down",
                            size=14,
                            color="#ff6767",
                            line=dict(color="#0b0e11", width=2),
                            opacity=0.95,
                        ),
                        name="Sell",
                        hovertext=sell_markers["text"],
                        hoverinfo="text",
                        showlegend=False,
                    ),
                    row=1,
                    col=1,
                )

            # Layout global
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#1e2329",
                plot_bgcolor="#1e2329",
                font=dict(color="#eaecef", size=10),
                width=None,
                height=height,
                margin=dict(l=60, r=0, t=10, b=30),
                hovermode="x unified",
                xaxis_rangeslider_visible=False,
                dragmode=False,
                uirevision=timeframe,
                showlegend=False,
                bargap=0.1,  # reducir espacio entre barras para hacerlas más anchas
            )

            # Eje X inferior (visible)
            fig.update_xaxes(
                showgrid=False,
                showline=True,
                linecolor="#2b3139",
                tickmode="array",
                ticktext=df_visible["x_label"].tolist(),
                tickvals=df_visible.index.tolist(),
                range=[x_min - 0.5, x_max + 0.5],
                row=2,
                col=1,
            )
            # Eje X superior (oculto, sin línea para evitar banda)
            fig.update_xaxes(showticklabels=False, showline=False, row=1, col=1)

            # Eje Y precio
            fig.update_yaxes(
                tickformat=",.0f",  # sin sufijos k/M
                showgrid=True,
                gridcolor="#2b3139",
                showline=True,
                linecolor="#2b3139",
                side="left",
                fixedrange=True,
                nticks=5,
                row=1,
                col=1,
            )
            # Eje Y volumen
            fig.update_yaxes(
                tickformat=",.0f",
                showgrid=True,
                gridcolor="#2b3139",
                showline=True,
                linecolor="#2b3139",
                side="left",
                fixedrange=True,
                nticks=4,
                row=2,
                col=1,
            )

            # Gridlines verticales manuales (alineados al eje X inferior)
            if gridline_indices:
                visible_grid = [idx for idx in gridline_indices if x_min <= idx <= x_max]
                for idx in visible_grid:
                    fig.add_shape(
                        type="line",
                        x0=idx,
                        x1=idx,
                        y0=0,
                        y1=1,
                        xref="x2",
                        yref="paper",
                        line=dict(color="#2b3139", width=1),
                        layer="below",
                    )

            # Línea divisoria entre OHLC y Volumen (ligeramente más gruesa que gridlines)
            fig.add_shape(
                type="line",
                x0=0,
                x1=1,
                y0=0.3,
                y1=0.3,
                xref="paper",
                yref="paper",
                line=dict(color="#2b3139", width=2),
                layer="above",
            )

            config = {
                "displayModeBar": False,
                "staticPlot": False,
                "scrollZoom": False,
                "doubleClick": False,
            }

            placeholder.plotly_chart(
                fig, config=config, key=f"combined_{timeframe}", width="stretch"
            )
    except Exception as e:
        placeholder.markdown(
            f'<div style="color: #f6465d; text-align: center; padding-top: 280px;">❌ Error: {e}</div>',
            unsafe_allow_html=True,
        )

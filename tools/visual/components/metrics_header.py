from __future__ import annotations

from typing import Any

import streamlit as st


def compute_realtime_metrics(df) -> dict[str, Any]:
    if df is None or df.empty or len(df) < 2:
        return {
            "current_price": 0.0,
            "price_change_pct": 0.0,
            "session_high": 0.0,
            "session_low": 0.0,
            "last_volume": 0.0,
            "elapsed_minutes": 0,
            "elapsed_secs": 0,
            "change_color": "#848e9c",
        }

    current_price = df["close"].iloc[-1]
    session_start_price = df["close"].iloc[0]
    price_change_pct = ((current_price - session_start_price) / session_start_price) * 100
    session_high = df["high"].max()
    session_low = df["low"].min()

    last_timestamp_second = int(df["timestamp"].iloc[-1])
    last_second_data = df[df["timestamp"].apply(lambda x: int(x) == last_timestamp_second)]
    last_volume_btc = last_second_data["volume"].sum()
    last_avg_price = last_second_data["close"].mean()
    last_volume = float(last_volume_btc) * float(last_avg_price)

    start_time = df["timestamp"].iloc[0]
    current_time = df["timestamp"].iloc[-1]
    elapsed_seconds = int(current_time - start_time)
    elapsed_minutes = elapsed_seconds // 60
    elapsed_secs = elapsed_seconds % 60

    change_color = "#0ecb81" if price_change_pct >= 0 else "#f6465d"

    return {
        "current_price": float(current_price),
        "price_change_pct": float(price_change_pct),
        "session_high": float(session_high),
        "session_low": float(session_low),
        "last_volume": float(last_volume),
        "elapsed_minutes": int(elapsed_minutes),
        "elapsed_secs": int(elapsed_secs),
        "change_color": change_color,
    }


def render_header(metrics: dict[str, Any]) -> None:
    header_cols = st.columns(6)
    with header_cols[0]:
        st.markdown(
            (
                '<div style="color: #f0b90b; font-size: 24px; '
                'font-weight: 700; line-height: 1.0; text-align: center;">'
                f"{metrics['current_price']:,.2f}</div>"
            ),
            unsafe_allow_html=True,
        )
    with header_cols[1]:
        st.markdown(
            (
                f'<div style="color: {metrics["change_color"]}; font-size: 24px; '
                'font-weight: 700; line-height: 1.0; text-align: center;">'
                f"{metrics['price_change_pct']:+.2f}%</div>"
            ),
            unsafe_allow_html=True,
        )
    with header_cols[2]:
        st.markdown(
            (
                '<div style="line-height: 1.2; text-align: center;">'
                '<span style="color: #848e9c; font-size: 16px;">Máx: </span>'
                '<span style="color: #ffffff; font-size: 20px; font-weight: 600;">'
                f"{metrics['session_high']:,.2f}</span></div>"
            ),
            unsafe_allow_html=True,
        )
    with header_cols[3]:
        st.markdown(
            (
                '<div style="line-height: 1.2; text-align: center;">'
                '<span style="color: #848e9c; font-size: 16px;">Mín: </span>'
                '<span style="color: #ffffff; font-size: 20px; font-weight: 600;">'
                f"{metrics['session_low']:,.2f}</span></div>"
            ),
            unsafe_allow_html=True,
        )
    with header_cols[4]:
        last_volume = metrics["last_volume"]
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
                f"{metrics['elapsed_minutes']:02d}:{metrics['elapsed_secs']:02d}</span></div>"
            ),
            unsafe_allow_html=True,
        )

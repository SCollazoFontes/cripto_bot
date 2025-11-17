from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DEFAULT_STRATEGY = "momentum"
DEFAULT_ENTRY = 0.0002
DEFAULT_EXIT = 0.00015


def _load_manifest(run_dir: str) -> tuple[str, dict[str, Any]]:
    strategy_name = DEFAULT_STRATEGY
    params: dict[str, Any] = {}
    manifest_file = Path(run_dir) / "manifest.json"
    if manifest_file.exists():
        with open(manifest_file) as f:
            manifest = json.load(f)
            candidate = manifest.get("strategy")
            if isinstance(candidate, str) and candidate:
                strategy_name = candidate
            params = manifest.get("params", {})
    return strategy_name, params


def _resolve_thresholds(strategy_name: str, params: dict[str, Any]) -> tuple[float, float]:
    """Return (entry_threshold, exit_threshold) for the active strategy."""
    entry = float(params.get("entry_threshold", DEFAULT_ENTRY))
    exit_thr = float(params.get("exit_threshold", DEFAULT_EXIT))

    if strategy_name == "vwap_reversion":
        entry = float(params.get("z_entry", entry))
        exit_thr = float(params.get("z_exit", exit_thr))

    entry = abs(entry) if entry else DEFAULT_ENTRY
    exit_thr = abs(exit_thr) if exit_thr else DEFAULT_EXIT
    return entry, exit_thr


def _build_signal_history(
    df: pd.DataFrame,
    strategy_name: str,
    params: dict[str, Any],
    calc_fn: Callable[..., tuple[float, str, dict[str, Any]]],
    max_points: int = 40,
) -> pd.DataFrame | None:
    """Compute rolling history of the signal for the last `max_points` bars."""
    if df is None or df.empty or "timestamp" not in df.columns:
        return None

    history: list[tuple[float, float]] = []
    start_idx = max(0, len(df) - max_points)
    for idx in range(start_idx, len(df)):
        window = df.iloc[: idx + 1]
        try:
            sig_value, _, _ = calc_fn(
                strategy_name=strategy_name,
                df=window,
                params=params or None,
            )
            history.append((float(window["timestamp"].iloc[-1]), float(sig_value)))
        except Exception:
            continue

    if not history:
        return None

    hist_df = pd.DataFrame(history, columns=["timestamp", "signal"])
    hist_df["dt"] = pd.to_datetime(hist_df["timestamp"], unit="s")
    return hist_df


def _render_history_chart(hist_df: pd.DataFrame, color: str) -> None:
    """Render a compact sparkline for the signal history."""
    if hist_df is None or hist_df.empty:
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=hist_df["dt"],
            y=hist_df["signal"],
            mode="lines",
            line=dict(color=color, width=2),
            hovertemplate="%{x|%H:%M:%S}<br>Signal=%{y:+.4f}<extra></extra>",
            name="Signal",
        )
    )
    fig.add_hline(y=0.0, line=dict(color="#555", width=1, dash="dot"))
    fig.update_layout(
        height=120,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            showgrid=False,
            showticklabels=False,
            zeroline=False,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="#2b3139",
            zeroline=False,
            range=[-1.05, 1.05],
            tickfont=dict(color="#848e9c", size=10),
        ),
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def render_signal_panel(run_dir: str, df) -> None:
    # Default values
    signal_value = 0.0
    zone_text = "NEUTRAL"
    signal_history: pd.DataFrame | None = None

    # Load manifest
    strategy_name, params = _load_manifest(run_dir)

    if df is None or df.empty:
        st.markdown(
            '<div style="color: #848e9c; font-size: 12px; padding: 8px; text-align: center;">'
            "Sin barras para calcular la señal todavía.</div>",
            unsafe_allow_html=True,
        )
        return

    from pathlib import Path as P
    import sys

    src_path = P(__file__).parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    try:
        from strategies.signals import calculate_signal

        signal_value, zone_text, _ = calculate_signal(
            strategy_name=strategy_name, df=df, params=params or None
        )
        signal_history = _build_signal_history(df, strategy_name, params, calculate_signal)
    except Exception:
        signal_value = 0.0
        zone_text = "ERROR"
        signal_history = None

    # Marker position (0-100)
    marker_position = ((signal_value + 1) / 2) * 100

    # Color
    if signal_value >= 0.5:
        signal_color = "#0ecb81"
    elif signal_value <= -0.5:
        signal_color = "#f6465d"
    else:
        signal_color = "#848e9c"

    # Thresholds visualization based on strategy params
    entry_threshold, exit_threshold = _resolve_thresholds(strategy_name, params or {})
    threshold_scale = max(abs(signal_value), entry_threshold, exit_threshold, 0.0005) * 3
    if threshold_scale <= 0:
        threshold_scale = 0.001

    # Normalized thresholds are available if we later add tick marks on the bar.
    # For now, we only display numeric thresholds below the bar.

    signal_block_html = f"""
<div style="padding: 0px 8px; margin-top: 12px; margin-bottom: 0px;">
    <div style="display: flex; align-items: center; justify-content: center; height: 20px;">
        <span style="color: #f0b90b; font-size: 16px; font-weight: 700; letter-spacing: 0.5px;">SEÑAL ESTRATEGIA</span>
    </div>
    <div style="text-align: center; margin-top: 8px; margin-bottom: 8px;">
        <div style="color: {signal_color}; font-size: 24px; font-weight: 700;
            line-height: 1.2;">{signal_value:+.4f}</div>
        <div style="color: {signal_color}; font-size: 12px; font-weight: 600; margin-top: 2px;">{zone_text}</div>
    </div>
    <div style="position: relative; height: 20px; background: linear-gradient(90deg,
        #f6465d 0%, #f1824e 25%, #f0b90b 50%, #59d98e 75%, #0ecb81 100%);
        border-radius: 4px; margin: 8px 0px;">
        <div style="position: absolute; left: {marker_position}%; top: 50%; transform: translate(-50%, -50%);
            width: 8px; height: 8px; background-color: #ffffff; border: 2px solid #0b0e11; border-radius: 50%;
            box-shadow: 0 0 4px rgba(255,255,255,0.6);"></div>
    </div>
    <div style="display: flex; justify-content: space-between; margin-top: 4px;">
        <span style="color: #848e9c; font-size: 14px;">Short: {-entry_threshold:+.5f}</span>
        <span style="color: #848e9c; font-size: 14px;">Long: {entry_threshold:+.5f}</span>
    </div>
    <div style="display: flex; justify-content: space-between; margin-top: 2px;">
        <span style="color: #848e9c; font-size: 12px;">Exit: {exit_threshold:+.5f}</span>
        <span style="color: #848e9c; font-size: 12px;">Scale≈±{threshold_scale:.4f}</span>
    </div>
</div>
"""

    st.markdown(signal_block_html, unsafe_allow_html=True)
    if signal_history is not None:
        _render_history_chart(signal_history, signal_color)

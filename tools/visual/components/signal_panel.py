from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st


def _load_manifest(run_dir: str) -> tuple[str, dict[str, Any]]:
    strategy_name = "momentum"
    params: dict[str, Any] = {}
    manifest_file = Path(run_dir) / "manifest.json"
    if manifest_file.exists():
        with open(manifest_file) as f:
            manifest = json.load(f)
            strategy_name = manifest.get("strategy", strategy_name)
            params = manifest.get("params", {})
    return strategy_name, params


def render_signal_panel(run_dir: str, df) -> None:
    # Default values
    signal_value = 0.0
    zone_text = "NEUTRAL"

    # Load manifest
    strategy_name, params = _load_manifest(run_dir)

    if df is not None and not df.empty:
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
        except Exception:
            signal_value = 0.0
            zone_text = "ERROR"

    # Marker position (0-100)
    marker_position = ((signal_value + 1) / 2) * 100

    # Color
    if signal_value >= 0.5:
        signal_color = "#0ecb81"
    elif signal_value <= -0.5:
        signal_color = "#f6465d"
    else:
        signal_color = "#848e9c"

    # Thresholds visualization (heuristic)
    manifest_file = Path(run_dir) / "manifest.json"
    if manifest_file.exists():
        manifest_params = json.loads(manifest_file.read_text()).get("params", {})
        entry_threshold = manifest_params.get("entry_threshold", 0.001)
    else:
        entry_threshold = 0.001

    max_signal = entry_threshold * 3
    long_threshold_norm = min(1.0, entry_threshold / max_signal) if max_signal > 0 else 0.0
    short_threshold_norm = -long_threshold_norm

    long_threshold_pos = ((long_threshold_norm + 1) / 2) * 100
    short_threshold_pos = ((short_threshold_norm + 1) / 2) * 100

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
    <div style="position: relative; height: 20px; background: linear-gradient(to right,
        #f6465d 0%, #f6465d {short_threshold_pos}%,
        #2b3139 {short_threshold_pos}%, #2b3139 {long_threshold_pos}%,
        #0ecb81 {long_threshold_pos}%, #0ecb81 100%);
        border-radius: 4px; margin: 8px 0px;">
        <div style="position: absolute; left: {marker_position}%; top: 50%; transform: translate(-50%, -50%);
            width: 8px; height: 8px; background-color: #ffffff; border: 2px solid #0b0e11; border-radius: 50%;
            box-shadow: 0 0 4px rgba(255,255,255,0.6);"></div>
    </div>
    <div style="display: flex; justify-content: space-between; margin-top: 4px;">
        <span style="color: #848e9c; font-size: 14px;">Short: {short_threshold_norm:+.4f}</span>
        <span style="color: #848e9c; font-size: 14px;">Long: {long_threshold_norm:+.4f}</span>
    </div>
</div>
"""

    st.markdown(signal_block_html, unsafe_allow_html=True)

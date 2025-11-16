from __future__ import annotations

import streamlit as st


def render_kpis_panel() -> None:
    # Placeholder KPIs; replace with real metrics when available
    kpi_momentum = 3.21
    kpi_momentum_thr = 2.5
    kpi_vol_rel = 1.8
    kpi_vol_rel_thr = 1.2
    kpi_imbalance = 24.0
    kpi_imbalance_thr = 15.0
    kpi_volatility = 0.42
    kpi_volatility_max = 1.2

    kpis_block_html = f"""
<div style="padding: 0px 8px; margin-top: 12px; margin-bottom: 0px;">
    <div style="display: flex; align-items: center; justify-content: center; height: 20px;">
        <span style="color: #f0b90b; font-size: 16px; font-weight: 700; letter-spacing: 0.5px;">KPIs INTERNOS</span>
    </div>
    <div style="margin-top: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
            <span style="color: #848e9c; font-size: 13px;">Momentum:</span>
            <span style="color: #ffffff; font-size: 13px; font-weight: 600;">
                {kpi_momentum:.2f}
                <span style="color: #848e9c; font-size: 12px;">(thr {kpi_momentum_thr:.1f})</span>
            </span>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
            <span style="color: #848e9c; font-size: 13px;">Vol relativo:</span>
            <span style="color: #ffffff; font-size: 13px; font-weight: 600;">
                {kpi_vol_rel:.1f}×
                <span style="color: #848e9c; font-size: 12px;">(thr {kpi_vol_rel_thr:.1f}×)</span>
            </span>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
            <span style="color: #848e9c; font-size: 13px;">Imbalance:</span>
            <span style="color: #ffffff; font-size: 13px; font-weight: 600;">
                {kpi_imbalance:.0f}%
                <span style="color: #848e9c; font-size: 12px;">(thr {kpi_imbalance_thr:.0f}%)</span>
            </span>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: baseline;">
            <span style="color: #848e9c; font-size: 13px;">Volatilidad:</span>
            <span style="color: #ffffff; font-size: 13px; font-weight: 600;">
                {kpi_volatility:.2f}%
                <span style="color: #848e9c; font-size: 12px;">(max {kpi_volatility_max:.1f}%)</span>
            </span>
        </div>
    </div>
</div>
"""

    st.markdown(kpis_block_html, unsafe_allow_html=True)

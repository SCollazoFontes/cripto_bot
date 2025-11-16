from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st


def render_position_panel(run_dir: str, current_price: float) -> None:
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
        eq_df = pd.read_csv(equity_file)
        if not eq_df.empty:
            last_eq = eq_df.iloc[-1]
            current_qty = last_eq.get("qty", 0.0)
            current_equity = last_eq.get("equity", 10000.0)
            initial_cash = eq_df.iloc[0].get("equity", 10000.0)
            pnl_usdt = current_equity - initial_cash
            pnl_pct = (pnl_usdt / initial_cash) * 100 if initial_cash > 0 else 0.0
            position_value = current_qty * current_price if current_qty > 0 else 0.0
            exposure_pct = (position_value / current_equity) * 100 if current_equity > 0 else 0.0

    if trades_file.exists() and current_qty > 0:
        tr_df = pd.read_csv(trades_file)
        if not tr_df.empty:
            tr_df["side_norm"] = tr_df["side"].astype(str).str.upper()
            buys = tr_df[tr_df["side_norm"] == "BUY"]
            if not buys.empty:
                total_qty = buys["qty"].sum()
                if total_qty > 0:
                    avg_entry_price = (buys["price"] * buys["qty"]).sum() / total_qty

    pnl_color = "#0ecb81" if pnl_usdt >= 0 else "#f6465d"

    pos_block_html = f"""
<div style="padding: 0px 8px; margin-top: -48px; margin-bottom: 0px;">
    <div style="display: flex; align-items: center; justify-content: center; height: 22px;">
        <span style="color: #f0b90b; font-size: 20px; font-weight: 700; letter-spacing: 0.5px;">POSICIÓN</span>
    </div>
    <div style="text-align: center; margin-top: 10px; margin-bottom: 6px;">
        <div style="color: {pnl_color}; font-size: 28px; font-weight: 700; line-height: 1.2;">{pnl_usdt:+.2f} USDT</div>
        <div style="color: {pnl_color}; font-size: 18px; font-weight: 600; margin-top: 2px;">({pnl_pct:+.2f}%)</div>
    </div>
    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
        <span style="color: #848e9c; font-size: 14px;">Tamaño:</span>
        <span style="color: #ffffff; font-size: 14px; font-weight: 600;">{current_qty:.4f} BTC</span>
    </div>
    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
        <span style="color: #848e9c; font-size: 14px;">Precio medio:</span>
        <span style="color: #ffffff; font-size: 14px; font-weight: 600;">{avg_entry_price:,.2f} USDT</span>
    </div>
    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
        <span style="color: #848e9c; font-size: 14px;">Valor posición:</span>
        <span style="color: #ffffff; font-size: 14px; font-weight: 600;">{position_value:,.2f} USDT</span>
    </div>
    <div style="display: flex; justify-content: space-between;">
        <span style="color: #848e9c; font-size: 14px;">Exposición:</span>
        <span style="color: #ffffff; font-size: 14px; font-weight: 600;">{exposure_pct:.1f}%</span>
    </div>
</div>
"""

    st.markdown(pos_block_html, unsafe_allow_html=True)

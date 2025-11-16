from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


def render_decision_panel(run_dir: str) -> None:
    trades_file = Path(run_dir) / "trades.csv"

    last_action = "—"
    last_action_time = "—"
    last_reason = "—"
    last_pnl = "—"
    last_position = "—"

    if trades_file.exists():
        tr_df = pd.read_csv(trades_file)
        if not tr_df.empty:
            last_trade = tr_df.iloc[-1]

            side = str(last_trade.get("side", "")).upper()
            trade_price = float(last_trade.get("price", 0.0))
            trade_qty = float(last_trade.get("qty", 0.0))

            trade_ts = float(last_trade.get("timestamp", 0.0))
            if trade_ts > 0:
                trade_time = datetime.fromtimestamp(trade_ts).strftime("%H:%M:%S")
                last_action_time = trade_time

            last_action = f"{side} @ {trade_price:,.2f}"

            reason = last_trade.get("reason", "—")
            if reason and reason != "—":
                rl = str(reason).lower()
                if "entry" in rl or "signal" in rl:
                    last_reason = "señal > thr long"
                elif "exit" in rl:
                    last_reason = "señal < thr exit"
                elif "stop" in rl:
                    last_reason = "stop loss"
                elif "profit" in rl:
                    last_reason = "take profit"
                else:
                    last_reason = str(reason)[:30]
            else:
                last_reason = "—"

            if len(tr_df) >= 2:
                prev_equity = float(tr_df.iloc[-2].get("equity", 10000.0))
                curr_equity = float(last_trade.get("equity", 10000.0))
                action_pnl = curr_equity - prev_equity
                pnl_sign = "+" if action_pnl >= 0 else ""
                last_pnl = f"{pnl_sign}{action_pnl:.2f} USDT"
            else:
                last_pnl = "0.00 USDT"

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
        '    <div style="margin-bottom: 6px;">'
        '      <span style="color: #848e9c; font-size: 12px;">Acción: </span>'
        f'      <span style="color: #ffffff; font-size: 12px; font-weight: 600;">{last_action}</span>'
        f'      <span style="color: #848e9c; font-size: 11px;"> ({last_action_time})</span>'
        "    </div>"
        '    <div style="margin-bottom: 6px;">'
        '      <span style="color: #848e9c; font-size: 12px;">Motivo: </span>'
        f'      <span style="color: #ffffff; font-size: 12px;">{last_reason}</span>'
        "    </div>"
        '    <div style="margin-bottom: 6px;">'
        '      <span style="color: #848e9c; font-size: 12px;">PNL acción: </span>'
        f'      <span style="color: #ffffff; font-size: 12px; font-weight: 600;">{last_pnl}</span>'
        "    </div>"
        "    <div>"
        '      <span style="color: #848e9c; font-size: 12px;">Nueva posición: </span>'
        f'      <span style="color: #ffffff; font-size: 12px; font-weight: 600;">{last_position}</span>'
        "    </div>"
        "  </div>"
        "</div>"
    )

    st.markdown(decision_block_html, unsafe_allow_html=True)

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


def _format_reason(reason: str) -> str:
    rl = reason.lower()
    if "entry" in rl or "signal" in rl:
        return "Entrada señal"
    if "exit" in rl:
        return "Salida señal"
    if "stop" in rl:
        return "Stop loss"
    if "profit" in rl:
        return "Take profit"
    return reason[:40]


def render_decision_panel(
    run_dir: str,
    df_bars: pd.DataFrame | None,
    trades_df: pd.DataFrame | None,
) -> None:
    trades_file = Path(run_dir) / "trades.csv"
    if trades_df is None:
        try:
            trades_df = pd.read_csv(trades_file)
        except Exception:
            trades_df = None

    st.markdown(
        '<div style="padding: 0px 8px; margin-top: 16px;">'
        '  <div style="display: flex; align-items: center; justify-content: center; height: 20px;">'
        '    <span style="color: #f0b90b; font-size: 14px; font-weight: 700; letter-spacing: 0.5px;">'
        "    DECISIONES DEL BOT</span>"
        "  </div>"
        '  <div style="border-bottom: 1px solid #2b3139; margin: 6px 0px;"></div>'
        "</div>",
        unsafe_allow_html=True,
    )

    if trades_df is None or trades_df.empty:
        st.markdown(
            '<div style="color: #848e9c; font-size: 12px; text-align: center;">'
            "Sin operaciones registradas en esta sesión.</div>",
            unsafe_allow_html=True,
        )
        return

    last_trades = trades_df.tail(3).iloc[::-1].copy()

    rows = []
    for _, trade in last_trades.iterrows():
        side = str(trade.get("side", "")).upper()
        price = float(trade.get("price", 0.0))
        qty = float(trade.get("qty", 0.0))
        ts = float(trade.get("timestamp", 0.0))
        time_label = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts > 0 else "—"

        reason = str(trade.get("reason", "—"))
        reason_fmt = _format_reason(reason) if reason and reason != "—" else "—"

        pnl = 0.0
        pnl_display = "0.00 USDT"
        idx = trade.name
        if idx > 0:
            prev = trades_df.iloc[idx - 1]
            prev_equity = float(prev.get("equity", 0.0))
            curr_equity = float(trade.get("equity", prev_equity))
            pnl = curr_equity - prev_equity
            pnl_display = f"{'+' if pnl >= 0 else ''}{pnl:.2f} USDT"

        rows.append(
            {
                "time": time_label,
                "side": side,
                "price": f"{price:,.2f}",
                "qty": f"{qty:.4f}",
                "pnl": pnl_display,
                "reason": reason_fmt,
            }
        )

    table_html = ["<div style='padding: 0 8px;'>"]
    for row in rows:
        color = "#0ecb81" if row["side"] == "BUY" else "#f6465d"
        table_html.append(
            "<div style='border: 1px solid #2b3139; border-radius: 4px; padding: 8px; margin-bottom: 6px;'>"
            f"<div style='display:flex; justify-content:space-between; font-size:12px; color:#848e9c;'>"
            f"<span>{row['time']}</span><span>{row['reason']}</span></div>"
            f"<div style='display:flex; justify-content:space-between; align-items:center; margin-top:4px;'>"
            f"<span style='color:{color}; font-size:14px; font-weight:600;'>{row['side']} {row['qty']} BTC</span>"
            f"<span style='color:#ffffff; font-size:14px;'>@ {row['price']}</span>"
            f"<span style='color:#ffffff; font-size:13px;'>{row['pnl']}</span>"
            "</div></div>"
        )
    table_html.append("</div>")
    st.markdown("\n".join(table_html), unsafe_allow_html=True)

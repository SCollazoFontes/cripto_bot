from __future__ import annotations

import streamlit as st


def render_timeframe_selector(options: list[str]) -> str:
    """Render a compact timeframe selector and return selected option.
    Uses st.session_state['selected_tf'] to store state.
    """
    if "selected_tf" not in st.session_state:
        st.session_state.selected_tf = options[0] if options else "1s"

    cols = st.columns([0.6] + [0.5] * len(options) + [8])
    with cols[0]:
        st.markdown(
            (
                '<div style="color: #848e9c; font-size: 13px; '
                "font-weight: 500; padding-top: 4px; "
                'line-height: 32px;">Tiempo</div>'
            ),
            unsafe_allow_html=True,
        )

    for idx, tf in enumerate(options):
        with cols[idx + 1]:
            is_selected = st.session_state.selected_tf == tf
            button_type = "primary" if is_selected else "secondary"
            if st.button(tf, key=f"tf_{tf}", type=button_type):
                st.session_state.selected_tf = tf
                st.rerun()

    return st.session_state.selected_tf

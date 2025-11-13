import streamlit as st


def render_layout(run_dir: str):
    st.set_page_config(page_title="CriptoBot - Live Dashboard", layout="wide")
    # Título muy compacto
    st.markdown(
        "<h5 style='margin:0;padding:0;color:#848e9c;'>📊 BTCUSDT - Live</h5>",
        unsafe_allow_html=True,
    )
    st.sidebar.header("Control de ejecución")
    st.sidebar.text_input("Directorio de ejecución", value=run_dir, key="run_dir_input")
    st.sidebar.divider()
    st.sidebar.write("Elementos disponibles:")
    st.sidebar.write("- Velas OHLC")
    st.sidebar.write("- Equity y PnL (próximamente)")
    st.sidebar.write("- Decisiones de la estrategia (próximamente)")
    st.sidebar.write("- Estadísticas avanzadas (próximamente)")
    st.sidebar.write("- Logs y alertas (próximamente)")
    st.sidebar.write("- ...")
    st.divider()

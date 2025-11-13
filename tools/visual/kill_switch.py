from pathlib import Path

import streamlit as st


def handle_kill_switch(run_dir: str):
    kill_file = Path(run_dir) / "KILL"
    if st.sidebar.button("❌ Cerrar y terminar ejecución", key="kill_button"):
        kill_file.touch()
        st.warning("Ejecución detenida por el usuario. Puedes cerrar la ventana.")
        st.stop()

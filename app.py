"""AgroLens — punto de entrada.

Ejecutar con:  streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:  # permite `import agrolens` sin instalar el paquete
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from agrolens.config import APP_NAME, APP_TAGLINE  # noqa: E402
from agrolens.ui.auth import gate  # noqa: E402
from agrolens.ui.components import CSS, init_state  # noqa: E402

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"about": f"**{APP_NAME}** — {APP_TAGLINE}"},
)
st.markdown(CSS, unsafe_allow_html=True)
gate()  # login con Google, clave compartida o sesión local, según lo configurado
init_state()

PAGES = [
    st.Page("views/0_panel.py", title="Panel", icon=":material/dashboard:", default=True),
    st.Page("views/1_lotes.py", title="Lotes", icon=":material/map:"),
    st.Page("views/2_vegetacion.py", title="Vegetación", icon=":material/eco:"),
    st.Page("views/3_clima.py", title="Clima y agua", icon=":material/water_drop:"),
    st.Page("views/8_tormentas.py", title="Tormentas", icon=":material/thunderstorm:"),
    st.Page("views/4_ambientes.py", title="Ambientes", icon=":material/grid_view:"),
    st.Page("views/5_historia.py", title="Historia", icon=":material/history:"),
    st.Page("views/6_informe.py", title="Informe", icon=":material/description:"),
    st.Page("views/7_ajustes.py", title="Ajustes", icon=":material/settings:"),
]

st.navigation(
    {
        "Resumen": PAGES[:2],
        "Análisis": PAGES[2:7],
        "Salidas": PAGES[7:],
    }
).run()

"""
app.py — Interfaz para preparar los datos de entrada del cuadrante anual.

Punto de entrada: selector de escenario en la barra lateral y el menú de páginas.
  Inicio      — estado del escenario e importar varios ficheros a la vez
  Parámetros  — config.toml como formulario
  Datos       — una página por CSV (app_pages/), en orden de dependencia
  Resultado   — generar el cuadrante: ejecuta el pipeline con barra de progreso

Uso:  interfaz/lanzar.sh      (o: streamlit run interfaz/app.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ficheros as fx  # noqa: E402
import tablas  # noqa: E402
import ui  # noqa: E402

st.set_page_config(page_title="Cuadrante anual", page_icon=":material/calendar_month:",
                   layout="wide")
ui.iniciar_estado()
ss = st.session_state

# --------------------------------------------------------------------------- #
#  Escenario activo (global a todas las páginas)
# --------------------------------------------------------------------------- #
escenarios = fx.listar_escenarios()
if "_escenario_nuevo" in ss:                       # recién creado en el diálogo
    ss["escenario"] = ss.pop("_escenario_nuevo")
if ss.get("escenario") not in escenarios:
    ss["escenario"] = escenarios[0] if escenarios else None

with st.sidebar:
    st.selectbox("Escenario", escenarios, key="escenario", placeholder="Ninguno todavía",
                 help="Cada escenario es un juego completo de datos de entrada, guardado en "
                      "escenarios/<nombre>/.")
    if st.button("Nuevo escenario", icon=":material/add:", width="stretch"):
        ui.dialogo_nuevo_escenario()

ui.mostrar_avisos()

# --------------------------------------------------------------------------- #
#  Páginas
# --------------------------------------------------------------------------- #
inicio = st.Page("app_pages/inicio.py", title="Inicio", icon=":material/home:", default=True)
if ss["escenario"]:
    paginas = {
        "Escenario": [inicio],
        "Configuración": [st.Page("app_pages/parametros.py", title="Parámetros",
                                  icon=":material/tune:")],
        "Datos": [st.Page(f"app_pages/{Path(nombre).stem}.py", title=titulo, icon=icono)
                  for nombre, (titulo, icono) in tablas.PAGINAS.items()],
        "Resultado": [st.Page("app_pages/generar.py", title="Generar cuadrante",
                              icon=":material/play_circle:")],
    }
else:
    paginas = [inicio]                  # sin escenario no hay nada que editar
st.navigation(paginas).run()

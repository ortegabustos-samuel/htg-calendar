"""
ui.py — Piezas de interfaz compartidas por todas las páginas: avisos, escenario activo y el
diálogo para crear uno nuevo.
"""
from __future__ import annotations

import streamlit as st

import ficheros as fx

ss = st.session_state


def iniciar_estado() -> None:
    """Todo el estado de sesión nace aquí, una vez."""
    ss.setdefault("avisos_flash", [])
    ss.setdefault("nonce", 0)       # cambia la clave de subidores y editores para rehacerlos al importar


def flash(tipo: str, texto: str) -> None:
    """Aviso que sobrevive al st.rerun() que sigue a guardar un fichero. Los de éxito salen como
    aviso flotante que se va solo; los de error o advertencia se quedan en la página."""
    ss.avisos_flash.append((tipo, texto))


def mostrar_avisos() -> None:
    iconos = {"success": ":material/check_circle:", "warning": ":material/warning:",
              "error": ":material/error:"}
    for tipo, texto in ss.avisos_flash:
        if tipo == "success":
            st.toast(texto, icon=iconos[tipo])
        else:
            getattr(st, tipo)(texto, icon=iconos[tipo])
    ss.avisos_flash = []


def recargar() -> None:
    """Tras importar un fichero: vacía los subidores y relee los editores del disco."""
    ss.nonce += 1
    st.rerun()


def guardado(nombre: str, cambios: list[str]) -> None:
    flash("success", f"`{nombre}` guardado." + (f" Además: {'; '.join(cambios)}." if cambios else ""))


def escenario() -> str | None:
    return ss.get("escenario")


@st.dialog("Nuevo escenario", icon=":material/add_circle:")
def dialogo_nuevo_escenario() -> None:
    escenarios = fx.listar_escenarios()
    nombre = st.text_input("Nombre", placeholder="p.ej. 2027",
                           help="Solo letras, números, guiones o guion bajo, sin espacios.")
    origenes = {"datos": "Datos actuales (data/input)", "copia": "Copia de otro escenario",
                "vacio": "Vacío"}
    origen = st.radio("Empezar con", list(origenes), format_func=origenes.get)
    base = None
    if origen == "copia":
        base = st.selectbox("Escenario a copiar", escenarios, disabled=not escenarios)
    if st.button("Crear escenario", type="primary", icon=":material/check:", width="stretch"):
        if not fx.nombre_valido(nombre):
            st.error("Usa solo letras, números, guiones o guion bajo (sin espacios).",
                     icon=":material/error:")
            return
        if origen == "copia" and not base:
            st.error("No hay ningún escenario que copiar.", icon=":material/error:")
            return
        desde = {"datos": fx.DATA_ORIGINAL, "vacio": None}.get(
            origen, fx.carpeta_entrada(base) if base else None)
        try:
            fx.crear_escenario(nombre, desde)
        except FileExistsError as e:
            st.error(str(e), icon=":material/error:")
            return
        # El selector de escenario ya se ha dibujado en esta ejecución y su valor no se puede
        # cambiar ahora: se deja pendiente y lo aplica app.py al principio de la siguiente.
        ss["_escenario_nuevo"] = nombre
        flash("success", f"Escenario **{nombre}** creado.")
        recargar()

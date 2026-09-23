"""Página de parámetros: config.toml como formulario con guardado automático."""
import streamlit as st

import ficheros as fx
import ui

ss = st.session_state
escenario = ui.escenario()
entrada = fx.carpeta_entrada(escenario)
ruta_cfg = entrada / "config.toml"

st.title("Parámetros", icon=":material/tune:")
st.caption("Año, jornada y convenio con los que se genera el cuadrante · fichero `config.toml`")

cfg = fx.config_de_escenario(escenario)
etiquetas = {"anio": "Año", "horas_objetivo": "Horas objetivo al año",
             "descanso_minimo": "Descanso mínimo entre turnos (h)",
             "horas_max_semana": "Horas máximas por semana",
             "dias_max_semana": "Días máximos por semana"}
claves = {c: f"cfg_{c}_{escenario}_{ss.nonce}" for c in [*fx.PARAMETROS, "grupos_rigidos"]}


def guardar() -> None:
    # Guardado automático: cualquier cambio se escribe en el acto, así no hay un botón que olvidar.
    fx.escribir_config(escenario, {c: ss[k] for c, k in claves.items()})
    ui.flash("success", "Parámetros guardados en `config.toml`.")


def campo(clave: str) -> None:
    _, _, lo, hi, ayuda = fx.PARAMETROS[clave]
    # Un valor fuera de rango en un config.toml importado haría fallar el number_input.
    st.number_input(etiquetas[clave], lo, hi, min(max(int(cfg[clave]), lo), hi), help=ayuda,
                    key=claves[clave], on_change=guardar)


izq, der = st.columns([3, 2], gap="large")

with izq:
    if not ruta_cfg.is_file():
        st.warning("Este escenario aún no tiene `config.toml`: se creará al cambiar cualquier "
                   "campo, o impórtalo a la derecha.", icon=":material/warning:")
    with st.container(border=True):
        st.subheader("Horizonte y jornada", icon=":material/calendar_month:")
        with st.container(horizontal=True):
            campo("anio")
            campo("horas_objetivo")

        st.subheader("Convenio", icon=":material/gavel:")
        with st.container(horizontal=True):
            campo("descanso_minimo")
            campo("horas_max_semana")
            campo("dias_max_semana")

        st.subheader("Patrones rígidos", icon=":material/lock:")
        patrones = fx.patrones_declarados(escenario)
        actuales = [str(g) for g in cfg.get("grupos_rigidos", [])]
        huerfanos = [g for g in actuales if g not in patrones]
        st.multiselect(
            "Patrones cuyo descanso no se fracciona", patrones + huerfanos, actuales,
            help="Para estos patrones no se ceden días sueltos: se cede el ciclo entero (bloque de "
                 "trabajo + su descanso) y el cubridor asume los dos. Es el caso de noches y UVI.",
            key=claves["grupos_rigidos"], on_change=guardar)
        if not patrones:
            st.caption("Importa o rellena **Patrones** para elegirlos de una lista.")
        if huerfanos:
            st.warning(f"{', '.join(huerfanos)} no aparece(n) en `patrones.csv`.",
                       icon=":material/warning:")
        st.caption("Los cambios se guardan solos.")

with der:
    st.subheader("¿Ya tienes un `config.toml`?", icon=":material/upload_file:")
    subido = st.file_uploader("Impórtalo y rellenará el formulario", type=["toml"],
                              key=f"toml_{escenario}_{ss.nonce}")
    if subido is not None:
        try:
            fx.guardar_fichero(escenario, "config.toml", subido.getvalue())
        except ValueError as e:
            st.error(f"No se ha guardado: {e}", icon=":material/error:")
        else:
            ui.flash("success", "`config.toml` importado.")
            ui.recargar()
    if ruta_cfg.is_file():
        st.download_button("Descargar config.toml", ruta_cfg.read_bytes(), "config.toml",
                           icon=":material/download:", width="stretch")
        with st.expander("Ver el fichero que se usará", icon=":material/visibility:"):
            st.code(ruta_cfg.read_text(encoding="utf-8"), language="toml")

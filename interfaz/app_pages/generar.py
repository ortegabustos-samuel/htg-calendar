"""Página para generar el cuadrante: comprueba los datos, elige la carpeta de salida y ejecuta el
pipeline con barra de progreso."""
import os

import streamlit as st

import ejecucion as ej
import ficheros as fx
import tablas
import ui

ss = st.session_state
escenario = ui.escenario()

st.title("Generar cuadrante", icon=":material/play_circle:")
st.caption(f"Ejecuta el pipeline completo con los datos del escenario **{escenario}**.")

en_marcha = ej.actual(escenario)
corriendo = en_marcha is not None and not en_marcha.terminada

# --------------------------------------------------------------------------- #
#  1. Datos de entrada
# --------------------------------------------------------------------------- #
st.subheader("Datos de entrada", icon=":material/checklist:")
estado = fx.estado_datos(escenario)
titulos = {"config.toml": "Parámetros", **{n: t for n, (t, _) in tablas.PAGINAS.items()}}
faltan = {n: motivo for n, motivo in estado.items() if motivo}
with st.container(border=True, gap="small"):
    for nombre, motivo in estado.items():
        with st.container(horizontal=True, vertical_alignment="center"):
            if motivo:
                st.badge("Falta", icon=":material/close:", color="red")
            else:
                st.badge("Listo", icon=":material/check:", color="green")
            st.markdown(f"**{titulos[nombre]}** · `{nombre}`" + (f" · {motivo}" if motivo else ""))
if faltan:
    st.error("Faltan datos: complétalos en " + ", ".join(f"**{titulos[n]}**" for n in faltan) +
             " antes de generar.", icon=":material/error:")

# --------------------------------------------------------------------------- #
#  2. Carpeta de salida
# --------------------------------------------------------------------------- #
st.subheader("Carpeta de salida", icon=":material/folder_open:")
propia = fx.carpeta_salida(escenario)
opciones = {"escenario": "Carpeta del escenario", "otra": "Otra carpeta"}
modo = st.segmented_control("Dónde guardar el resultado", list(opciones),
                            format_func=opciones.get, default="escenario",
                            key=f"modo_salida_{escenario}", disabled=corriendo)
if modo == "otra":
    texto = st.text_input(
        "Ruta de la carpeta", key=f"ruta_salida_{escenario}", disabled=corriendo,
        placeholder=str(fx.RAIZ / "data" / "output"),
        help="Ruta absoluta, o relativa a la carpeta del proyecto. Si no existe se crea.")
    salida = ej.resolver_salida(texto) if texto.strip() else None
else:
    salida = propia

problema = None
if salida is None:
    problema = "Escribe la ruta de la carpeta."
elif motivo := ej.problema_salida(salida):
    problema = f"No se puede usar `{salida}`: {motivo}."
if problema:
    st.warning(problema, icon=":material/warning:")
else:
    st.caption(f"El resultado se guardará en `{salida}`" +
               (" (se creará)." if not salida.exists() else "."))
    if (salida / "calendario.xlsx").exists():
        st.caption(":orange[Ya hay un `calendario.xlsx` en esa carpeta: se sobrescribirá.]")

with st.expander("Opciones avanzadas", icon=":material/tune:"):
    segundos = st.number_input(
        "Tiempo máximo del solver por nivel (s)", 10, 3600, 300, step=10, disabled=corriendo,
        help="El solver resuelve tres niveles seguidos (cobertura, equidad, forma semanal), cada "
             "uno con este tope. Más tiempo puede dar una solución algo mejor.")
    hilos = st.number_input("Hilos del solver", 1, os.cpu_count() or 8,
                            min(8, os.cpu_count() or 8), disabled=corriendo,
                            help="Núcleos del procesador que usa el solver.")
    st.caption(f"Duración máxima aproximada: {3 * segundos // 60 + 1} min.")

# --------------------------------------------------------------------------- #
#  3. Ejecutar
# --------------------------------------------------------------------------- #
if st.button("Generar cuadrante", type="primary", icon=":material/play_arrow:",
             disabled=bool(faltan or problema or corriendo)):
    try:
        ej.lanzar(escenario, salida, int(segundos), int(hilos))
    except (RuntimeError, OSError) as e:
        st.error(f"No se ha podido arrancar: {e}", icon=":material/error:")
    else:
        st.rerun()


def seguimiento() -> None:
    """Se refresca sola cada segundo mientras hay una ejecución en marcha."""
    actual = ej.actual(escenario)
    if actual is None:
        return
    if not actual.terminada:
        st.progress(actual.progreso(),
                    text=f"Paso {actual.paso}/7 · {actual.texto_paso} · {actual.duracion()}")
        st.caption("Puedes cambiar de página: la ejecución sigue y vuelve aquí para ver cómo va.")
        if st.button("Cancelar", icon=":material/stop_circle:"):
            actual.cancelar()
            st.rerun()
    elif actual.cancelada:
        st.warning(f"Ejecución cancelada tras {actual.duracion()}.", icon=":material/stop_circle:")
    elif actual.ok:
        st.success(f"Cuadrante generado en {actual.duracion()}.", icon=":material/check_circle:")
        excel = actual.salida / "calendario.xlsx"
        if excel.is_file():
            st.download_button("Descargar calendario.xlsx", excel.read_bytes(), excel.name,
                               icon=":material/download:", type="primary")
            st.caption(f"Guardado en `{excel}`")
    else:
        st.error(f"El pipeline ha fallado (código {actual.proc.returncode}). Revisa el registro.",
                 icon=":material/error:")
    with st.expander("Registro de la ejecución", icon=":material/terminal:",
                     expanded=actual.terminada and not actual.ok and not actual.cancelada):
        st.code("\n".join(actual.lineas[-300:]) or "(sin salida todavía)", language=None)
    # Al terminar, una recarga completa de la página reactiva el botón y los controles.
    if actual.terminada and ss.get(f"vista_{escenario}") == "corriendo":
        ss[f"vista_{escenario}"] = "terminada"
        st.rerun()
    ss[f"vista_{escenario}"] = "terminada" if actual.terminada else "corriendo"


st.fragment(seguimiento, run_every=1 if corriendo else None)()

"""Página de inicio: estado del escenario e importación de varios ficheros a la vez."""
from pathlib import Path

import streamlit as st

import ficheros as fx
import tablas
import ui

ss = st.session_state
escenario = ui.escenario()

st.title("Cuadrante anual", icon=":material/calendar_month:")

if not escenario:
    st.markdown("Prepara aquí los datos de entrada del cuadrante: los parámetros del convenio y "
                "los seis ficheros de datos. Cada juego de datos es un **escenario**, así se puede "
                "preparar 2027 sin tocar 2026.")
    if st.button("Crear el primer escenario", type="primary", icon=":material/add_circle:"):
        ui.dialogo_nuevo_escenario()
    st.stop()

entrada = fx.carpeta_entrada(escenario)
st.caption(f"Escenario **{escenario}** · guardado en `escenarios/{escenario}/`")

st.subheader("Estado de los ficheros", icon=":material/checklist:")
st.caption("Rellénalos en este orden: cada uno se apoya en los anteriores.")
paginas = {"config.toml": ("Parámetros", ":material/tune:"), **tablas.PAGINAS}
with st.container(border=True, gap="small"):
    for nombre, (titulo, icono) in paginas.items():
        ruta = entrada / nombre
        with st.container(horizontal=True, vertical_alignment="center"):
            if ruta.is_file():
                filas = (sum(1 for _ in ruta.open(encoding="utf-8")) - 1
                         if nombre.endswith(".csv") else None)
                st.badge("Listo", icon=":material/check:", color="green")
                detalle = f"{filas} filas" if filas is not None else "configurado"
            else:
                st.badge("Falta", icon=":material/pending:", color="orange")
                detalle = "sin datos"
            st.markdown(f"{icono} **{titulo}** · `{nombre}` · {detalle}")

st.subheader("Importar varios ficheros a la vez", icon=":material/upload:")
st.caption("CSV o Excel (.xlsx) con la misma estructura que el CSV: fila 1 la cabecera, una fila "
           "por registro. Se reconocen por el nombre del fichero; un único Excel puede traerlos "
           "todos, con una hoja por fichero llamada `turnos`, `patrones`, `trabajadores`… "
           "Cada fichero importado sustituye al que hubiera.")
varios = st.file_uploader("Ficheros a importar", type=["csv", "xlsx", "toml"],
                          accept_multiple_files=True, key=f"varios_{escenario}_{ss.nonce}")
if varios:
    conocidos = {"config.toml", *fx.CSVS}
    for f in varios:
        es_excel = f.name.lower().endswith(".xlsx")
        destino = Path(f.name).stem + ".csv" if es_excel else f.name
        try:
            if destino in conocidos:
                ui.guardado(destino, fx.guardar_fichero(escenario, destino, f.getvalue()))
            elif es_excel:
                # Libro con varias hojas: cada una llamada como su fichero.
                hojas = fx.csvs_de_xlsx(f.getvalue())
                if not hojas:
                    ui.flash("warning", f"`{f.name}`: ninguna hoja se llama como un fichero "
                                        f"esperado ({', '.join(Path(n).stem for n in fx.CSVS)}); "
                                        "ignorado.")
                for nombre_csv, contenido in hojas.items():
                    ui.guardado(nombre_csv, fx.guardar_fichero(escenario, nombre_csv, contenido) +
                                [f"hoja '{Path(nombre_csv).stem}' de {f.name}"])
            else:
                ui.flash("warning", f"`{f.name}` no es ninguno de los ficheros esperados; ignorado.")
        except ValueError as e:
            ui.flash("error", f"`{f.name}` no se ha guardado: {e}")
    ui.recargar()

"""
tablas.py — La página de cada CSV: importar, descargar y editar a mano con guardado automático.

Las seis páginas de app_pages/ (turnos, patrones, …) son una llamada a `pagina(nombre)`: todo lo
que cambia de un fichero a otro está en `_columnas`, que dice cómo se edita cada columna.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import ficheros as fx
import ui

ss = st.session_state

# Título e icono de la página de cada CSV, en el orden del menú.
PAGINAS = {
    "turnos.csv": ("Turnos", ":material/schedule:"),
    "patrones.csv": ("Patrones", ":material/view_week:"),
    "trabajadores.csv": ("Trabajadores", ":material/badge:"),
    "capacidades.csv": ("Capacidades", ":material/fact_check:"),
    "festivos.csv": ("Festivos", ":material/event:"),
    "calendarios_municipio.csv": ("Calendarios por municipio", ":material/location_city:"),
}


def _ayuda(texto: str, columna: str) -> str:
    # La cabecera es legible; la ayuda da el nombre real, que es el que debe llevar un Excel importado.
    return f"{texto}\n\nColumna `{columna}` del CSV."


def _desplegable(etiqueta: str, columna: str, opciones: list[str], ayuda: str,
                 obligatorio: bool = True):
    # Sin opciones (aún no hay de dónde sacarlas) un SelectboxColumn vacío no dejaría escribir
    # nada, así que cae a texto libre hasta que las haya.
    if opciones:
        return st.column_config.SelectboxColumn(
            etiqueta, options=opciones, required=obligatorio, help=_ayuda(ayuda, columna))
    return st.column_config.TextColumn(
        etiqueta, help=_ayuda(f"{ayuda} Todavía sin opciones: ver el aviso de arriba.", columna))


def _casilla(etiqueta: str, columna: str, texto: str):
    return st.column_config.CheckboxColumn(etiqueta, default=False, help=_ayuda(texto, columna))


def _fecha(etiqueta: str, columna: str, texto: str, anio: int):
    # Como texto libre "13/052026" o un año equivocado no los pilla nada hasta el pipeline; con el
    # selector de calendario no se puede ni teclear.
    return st.column_config.DateColumn(
        etiqueta, format="DD/MM/YYYY", required=True,
        min_value=date(anio, 1, 1), max_value=date(anio, 12, 31),
        help=_ayuda(f"{texto} Solo fechas de {anio}, el año de Parámetros.", columna))


def _hora(etiqueta: str, columna: str, texto: str):
    # Texto y no TimeColumn: el selector de hora del navegador sale en AM/PM según el idioma del
    # sistema, y Streamlit no deja cambiarlo. El patrón solo admite 24 h: 7:00, 07:00, 21:30.
    return st.column_config.TextColumn(
        etiqueta, required=True, max_chars=5, validate=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$",
        help=_ayuda(f"{texto} Formato 24 h, p.ej. 7:00 o 21:30.", columna))


def _columnas(nombre: str, escenario: str, anio: int) -> tuple[dict, list[str]]:
    """Cómo se edita cada columna de `nombre`, y los avisos de ficheros de los que depende y aún
    no están (sin ellos, los desplegables caen a texto libre)."""
    avisos: list[str] = []
    turnos = fx.turnos_declarados(escenario)

    if nombre == "turnos.csv":
        config = {
            "id_turno": st.column_config.TextColumn(
                "Línea", required=True,
                help=_ayuda("Identificador de la línea. Es el que se usa en patrones, trabajadores "
                            "y capacidades.", "id_turno")),
            "municipio": st.column_config.TextColumn(
                "Municipio", required=True, help=_ayuda("Municipio donde opera.", "municipio")),
            "lv": _casilla("L-V", "lv", "Marcado si opera de lunes a viernes."),
            "sabado": _casilla("Sáb", "sabado", "Marcado si opera los sábados."),
            "domingo": _casilla("Dom", "domingo", "Marcado si opera los domingos."),
            "festivo": _casilla("Fest", "festivo",
                                "Marcado si opera los festivos (manda sobre el día de la semana)."),
            "hora_entrada": _hora("Entrada", "hora_entrada", "Hora de inicio del turno."),
            "hora_salida": _hora(
                "Salida", "hora_salida", "Hora de fin. Si es menor que la entrada, acaba al día "
                                         "siguiente; igual a la entrada = guardia de 24 h."),
            "horas_computadas": st.column_config.NumberColumn(
                "Horas computadas", min_value=0, max_value=24, step=0.25, format="%.2f",
                required=True, help=_ayuda("Horas que computan para la jornada (una guardia "
                                           "localizada de 24 h computa 8).", "horas_computadas")),
            "dem": st.column_config.NumberColumn(
                "Demanda", min_value=0, step=1, format="%d", default=1,
                help=_ayuda("Personas por día que opera. 0 = refuerzo de calendario (REF CAL), "
                            "sin demanda detrás.", "dem")),
        }
    elif nombre == "patrones.csv":
        if not turnos:
            avisos.append("Importa o rellena antes **Turnos**: hasta entonces los días solo "
                          "admiten DO o LIBRE.")
        config = {
            "patron": st.column_config.TextColumn(
                "Patrón", required=True,
                help=_ayuda("Id del patrón. Se repite igual en todas las filas de su ciclo.",
                            "patron")),
            "fila": st.column_config.NumberColumn(
                "Semana del ciclo", min_value=0, step=1, format="%d", required=True,
                help=_ayuda("0 es la primera semana del ciclo; avanza una por semana.", "fila")),
        }
        for dia in fx.DIAS:
            config[dia] = st.column_config.SelectboxColumn(
                dia, options=[*turnos, *fx.DESCANSOS], required=True,
                help=_ayuda("Una línea de Turnos, o DO (descanso obligatorio), o LIBRE.", dia))
    elif nombre == "trabajadores.csv":
        patrones = fx.patrones_declarados(escenario)
        if not patrones or not turnos:
            avisos.append("Importa o rellena antes **Turnos** y **Patrones**: `Patrón` y `Línea` "
                          "se eligen de ellos.")
        config = {
            "id_trab": st.column_config.TextColumn(
                "DNI/NIE", required=True, help=_ayuda("Identifica al trabajador.", "id_trab")),
            "nombre": st.column_config.TextColumn(
                "Nombre", help=_ayuda("Apellidos y nombre, solo para el Excel de salida.", "nombre")),
            "municipio": _desplegable("Municipio", "municipio", fx.municipios_validos(escenario),
                                      "Zona a la que pertenece: con quién se compara en equidad."),
            "tipo": st.column_config.SelectboxColumn(
                "Tipo", options=fx.TIPOS_TRABAJADOR, required=True,
                help=_ayuda("fijo: una línea L-V · patron: sigue una rotación · correturno: "
                            "pool rodante.", "tipo")),
            "patron": _desplegable("Patrón", "patron", patrones,
                                   "Solo si el tipo es patron.", obligatorio=False),
            "vac1_inicio": _fecha("Vacaciones 1 (inicio)", "vac1_inicio",
                                  "Primer día de la 1ª quincena de vacaciones.", anio),
            "vac2_inicio": _fecha("Vacaciones 2 (inicio)", "vac2_inicio",
                                  "Primer día de la 2ª quincena de vacaciones.", anio),
            "linea": _desplegable("Línea (fijos)", "linea", turnos,
                                  "Solo si el tipo es fijo: la línea que cubre de lunes a viernes.",
                                  obligatorio=False),
            "fila_inicial": st.column_config.NumberColumn(
                "Fila inicial", min_value=0, step=1, format="%d",
                help=_ayuda("Solo si el tipo es patron: fila del patrón que hace la primera semana "
                            "del año (continuidad con el año anterior).", "fila_inicial")),
            "factor_jornada": st.column_config.NumberColumn(
                "Jornada (%)", min_value=1, max_value=100, step=0.5, format="%.1f %%", default=100,
                help=_ayuda("Porcentaje de jornada del contrato: 100 = completa, 75 = reducción de "
                            "un cuarto. En el CSV se guarda como fracción (0.75).",
                            "factor_jornada")),
        }
    elif nombre == "capacidades.csv":
        trabajadores = fx.trabajadores_declarados(escenario)
        if not trabajadores or not turnos:
            avisos.append("Importa o rellena antes **Turnos** y **Trabajadores**: trabajador y "
                          "línea se eligen de ellos.")
        config = {
            "id_trab": _desplegable("Trabajador (DNI/NIE)", "id_trab", trabajadores,
                                    "Un trabajador de Trabajadores."),
            "id_turno": _desplegable("Línea", "id_turno", turnos, "Una línea de Turnos."),
            "lv": _casilla("L-V", "lv", "Marcado si puede hacer esta línea de lunes a viernes."),
            "sab": _casilla("Sáb", "sab", "Marcado si puede hacerla los sábados."),
            "dom": _casilla("Dom", "dom", "Marcado si puede hacerla los domingos."),
            "fest": _casilla("Fest", "fest", "Marcado si puede hacerla los festivos."),
            "v": st.column_config.NumberColumn(
                "Cubridor (v)", min_value=0, step=1, format="%d", default=0,
                help=_ayuda("0 = no es cubridor designado · 1 = cubridor principal · 2, 3… = "
                            "suplentes, por orden: solo entran si los anteriores no pueden.", "v")),
        }
    elif nombre == "festivos.csv":
        config = {
            "fecha": _fecha("Fecha", "fecha", "Día festivo.", anio),
            "ambito": st.column_config.TextColumn(
                "Ámbito", required=True,
                help=_ayuda("'Nacional' o el nombre de un calendario local (el mismo que luego se "
                            "elige en Calendarios por municipio).", "ambito")),
        }
    else:  # calendarios_municipio.csv
        municipios = fx.municipios_validos(escenario)
        ambitos = fx.ambitos_declarados(escenario)
        if not municipios:
            avisos.append("Importa o rellena antes **Turnos**: el municipio se elige de sus líneas.")
        if not ambitos:
            avisos.append("Añade antes algún festivo en **Festivos**: el calendario se elige de "
                          "sus ámbitos.")
        config = {
            "municipio": _desplegable("Municipio", "municipio", municipios,
                                      "Un municipio de Turnos."),
            "calendario_festivos": _desplegable("Calendario de festivos", "calendario_festivos",
                                                ambitos, "Uno de los ámbitos de Festivos."),
        }
    return config, avisos


def _tabla(nombre: str, escenario: str) -> pd.DataFrame:
    """El CSV como tabla del editor: cada columna con su tipo (fecha, número, casilla…)."""
    tabla = pd.DataFrame(fx.filas_editables(escenario, nombre), columns=fx.EDITABLES[nombre])
    for col in fx.FECHAS.get(nombre, []):
        tabla[col] = pd.to_datetime(tabla[col], format="%d/%m/%Y", errors="coerce").dt.date
    for col in [*fx.ENTEROS.get(nombre, []), *fx.DECIMALES.get(nombre, [])]:
        tabla[col] = pd.to_numeric(tabla[col], errors="coerce")
    for col in fx.CASILLAS.get(nombre, []):
        tabla[col] = tabla[col].astype(str).str.strip() == "1"
    if nombre == "turnos.csv":
        tabla["dem"] = tabla["dem"].fillna(1)               # dem vacía = 1, como en el cargador
    if nombre == "capacidades.csv":
        tabla["v"] = tabla["v"].fillna(0)
    if nombre == "trabajadores.csv":
        tabla["factor_jornada"] = tabla["factor_jornada"].map(fx.porcentaje_jornada)
    return tabla


def a_csv(nombre: str, tabla: pd.DataFrame) -> list[dict]:
    """Las filas del editor con cada valor escrito como lo espera el cargador (fechas DD/MM/AAAA,
    horas H:MM, enteros sin decimales, casillas 0/1, jornada como fracción)."""
    filas = tabla.to_dict("records")
    for fila in filas:
        for col in fx.FECHAS.get(nombre, []):
            # Las fechas leídas del CSV llegan como date, pero las que se editan o añaden en la
            # tabla vuelven del navegador como texto ISO ("2026-05-01"): se aceptan las dos.
            f = pd.to_datetime(fila.get(col), errors="coerce")
            fila[col] = f.strftime("%d/%m/%Y") if not pd.isna(f) else ""
        for col in fx.ENTEROS.get(nombre, []):
            v = fila.get(col)
            fila[col] = str(int(v)) if v is not None and not pd.isna(v) else ""
        for col in fx.DECIMALES.get(nombre, []):
            v = fila.get(col)
            fila[col] = str(float(v)) if v is not None and not pd.isna(v) else ""
        for col in fx.HORAS.get(nombre, []):
            # Sin cero delante (7:00, no 07:00), como ya viene en el CSV.
            t = str(fila.get(col) or "").strip()
            fila[col] = f"{int(t.split(':')[0])}:{t.split(':')[1]}" if ":" in t else t
        for col in fx.CASILLAS.get(nombre, []):
            fila[col] = "1" if fila.get(col) is True else "0"
        if nombre == "trabajadores.csv":
            fila["factor_jornada"] = fx.fraccion_jornada(fila["factor_jornada"])
    return filas


def _importar(nombre: str, escenario: str) -> None:
    with st.popover("Importar fichero", icon=":material/upload:"):
        f = st.file_uploader(
            "CSV o Excel (.xlsx)", type=["csv", "xlsx"], key=f"subir_{nombre}_{escenario}_{ss.nonce}",
            help="Misma estructura que el CSV: fila 1 la cabecera, una fila por registro. En un "
                 f"Excel se usa la hoja '{Path(nombre).stem}' o, si no la hay, la primera.")
        st.caption("Sustituye todo el contenido actual de la tabla.")
    if f is not None:
        try:
            ui.guardado(nombre, fx.guardar_fichero(escenario, nombre, f.getvalue()))
        except ValueError as e:
            ui.flash("error", f"`{f.name}` no se ha guardado: {e}")
        ui.recargar()


def pagina(nombre: str) -> None:
    escenario = ui.escenario()
    titulo, icono = PAGINAS[nombre]
    st.title(titulo, icon=icono)
    st.caption(f"{fx.CSVS[nombre]} · fichero `{nombre}`")

    # La descarga se añade a esta barra al final, cuando ya se ha guardado lo último editado.
    barra = st.container(horizontal=True)
    with barra:
        _importar(nombre, escenario)

    anio = int(fx.config_de_escenario(escenario)["anio"])
    config, avisos = _columnas(nombre, escenario, anio)
    for aviso in avisos:
        st.warning(aviso, icon=":material/warning:")

    # La tabla de partida se guarda en la sesión mientras el editor está en pantalla: el editor
    # recuerda los cambios como diferencias sobre ella, y si en cada ejecución se le pasara lo
    # recién guardado las filas añadidas se aplicarían otra vez encima y saldrían duplicadas.
    # Pero en cuanto el editor se monta de nuevo (primera vez, o al volver de otra página:
    # Streamlit borra el estado de un widget que deja de dibujarse, y con él las diferencias) la
    # partida se RELEE del disco. Si no, se mostraría la tabla de cuando se abrió la página por
    # primera vez y el guardado automático la escribiría encima de lo editado después.
    clave = f"editor_{nombre}_{escenario}_{ss.nonce}"
    if clave not in ss or f"base_{clave}" not in ss:
        base = _tabla(nombre, escenario)
        ss[f"base_{clave}"] = base
        ss[f"guardado_{clave}"] = fx.filas_limpias(nombre, a_csv(nombre, base))

    # Guardado automático: cada celda confirmada (Intro o salir de ella) vuelve a ejecutar la
    # página, y si el contenido ha cambiado respecto a lo último guardado se escribe en el acto.
    editado = st.data_editor(ss[f"base_{clave}"], num_rows="dynamic", hide_index=True,
                             column_config=config, key=clave)
    filas = fx.filas_limpias(nombre, a_csv(nombre, editado))
    # Segunda barrera: sin ediciones del usuario en el editor no se escribe nunca, aunque la
    # comparación diga otra cosa. Así ningún desajuste de estado puede pisar el fichero.
    cambios = ss.get(clave) or {}
    editado_por_usuario = any(cambios.get(k) for k in ("edited_rows", "added_rows", "deleted_rows"))
    if editado_por_usuario and filas != ss[f"guardado_{clave}"]:
        try:
            fx.guardar_filas(escenario, nombre, filas)
        except OSError as e:
            st.error(f"No se ha podido guardar `{nombre}`: {e}", icon=":material/error:")
        else:
            ss[f"guardado_{clave}"] = filas
            st.toast(f"`{nombre}` guardado.", icon=":material/check_circle:")
    st.caption(f"{len(filas)} filas · los cambios se guardan solos · una fila nueva se guarda en "
               "cuanto tiene rellena su primera columna.")

    ruta = fx.carpeta_entrada(escenario) / nombre
    barra.download_button("Descargar CSV", ruta.read_bytes() if ruta.is_file() else b"", nombre,
                          icon=":material/download:", disabled=not ruta.is_file(),
                          key=f"bajar_{nombre}")

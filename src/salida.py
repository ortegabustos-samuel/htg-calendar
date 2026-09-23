"""
salida.py — El cuadrante a XLSX: rejilla trabajador x dia, comprobante de cobertura y plan
funcional para el comité.

La hoja Cuadrante lleva el NOMBRE como única columna de identificación, justo después las cinco
métricas de la persona —sábados, domingos, festivos, días de vacaciones y horas— y a continuación el
calendario: una columna por día, con tres filas de cabecera (mes, fecha `dd/mm/aaaa` escrita en
vertical para que quepa en una sola celda, e inicial del día de la semana o `F` si es festivo).

Es una hoja VIVA: las cinco métricas y la tabla COBERTURA DÍA A DÍA de debajo son FÓRMULAS, no
números pegados. Si el planificador escribe una línea en una celda del calendario, se recalculan
solos: las métricas con los datos DE ESA LÍNEA (sus horas, y si ese día es festivo según el
calendario de su municipio) y la tabla de cobertura con su semáforo —verde cubierto exacto, amarillo
sobra gente, rojo falta— de esa línea ese día. Así una alteración manual posterior a la generación
del cuadrante se ve al momento.

Todo lo que un lector externo necesita localizar tiene RANGO CON NOMBRE: `Trabajadores` (la columna
de nombres), una métrica por nombre (`Sabados`, `Domingos`, `Festivos`, `Vacaciones`, `Horas`),
`Calendario` (la rejilla entera) y un rango por mes (`CalendarioEnero`...). Así nadie tiene que
buscar celdas por su texto.

La hoja Conversión Comité lleva el plan funcional (una fila por línea, con su demanda y su
"Asignados" en vivo) y una columna Leyenda Comité en blanco para que el comité la rellene, una
leyenda por turno.
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

from cargar_datos import DESCANSOS, DO, Datos, turno_de

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = Path(os.environ.get("HT_SALIDA") or RAIZ / "data" / "output")   # la interfaz la cambia por escenario

DIA_INI = ["L", "M", "X", "J", "V", "S", "D"]
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# Las columnas de la izquierda: el nombre y, pegadas a él, las cinco métricas de la persona. Cada
# una se publica luego como rango con nombre (sin tildes: son identificadores, no rótulos).
METRICAS = (("Sábados", "Sabados"), ("Domingos", "Domingos"), ("Festivos", "Festivos"),
            ("Vacaciones", "Vacaciones"), ("Horas", "Horas"))
COL_NOMBRE, COL_M0 = 1, 2
COL_D0 = COL_M0 + len(METRICAS)

# Tres filas de cabecera sobre el calendario: mes, fecha en vertical, inicial del día.
F_MES, F_FECHA, F_DIA = 3, 4, 5

# Colores del Excel. El de FESTIVO se aplica CELDA A CELDA, no a la columna entera: un festivo local
# solo tiñe a quien trabaja en un municipio que se acoge a ese calendario.
CAT_FILL = {"lv": "FFF2CC", "finde": "F8CBAD", "festivo": "FFC000"}
FILL_VAC_XL = "FFFF00"
# Semáforo de COBERTURA DÍA A DÍA, por formato condicional (no relleno fijo) para que siga vivo si
# el planificador retoca el cuadrante después de generarlo: verde asignados = demanda, amarillo
# asignados > demanda (sobra gente), rojo asignados < demanda (falta gente).
FILL_COBERTURA_OK, FILL_COBERTURA_EXCESO, FILL_COBERTURA_FALTA = "C6EFCE", "FFEB9C", "FFC7CE"

# Las tres clases de descanso que se rotulan en el cuadrante. Provisionales: el estilo está pensado
# para distinguirlas de un vistazo (y del color de categoría del día), no para ser definitivo.
#   DS — descanso semanal: hasta 2 días de descanso por semana ISO.
#   DF — descanso festivo: uno por cada festivo trabajado, en el primer día libre posterior.
#   DO — descanso obligatorio: el que da un turno de 24 h. NO se calcula aquí, viene en el plan
#        desde el patrón y se hereda con el bloque; aquí solo se pinta.
DS, DF = "DS", "DF"
FILL_DESCANSO = {DS: "D9E1F2", DF: "E4DFEC", DO: "D9D9D9"}


def _municipio_trabajador(datos: Datos) -> dict[str, str]:
    """Municipio de referencia de cada trabajador, para saber qué festivos le afectan. No es un dato
    de `trabajadores.csv`: el municipio vive en los TURNOS, así que se deduce de las líneas que hace
    —la de su patrón si lo tiene, la suya si es fijo, o sus capacidades— quedándose con la más
    frecuente. Solo se usa para PINTAR el calendario; cuando el trabajador tiene turno asignado ese
    día se usa el municipio de ese turno, que es exacto."""
    muni: dict[str, str] = {}
    for w, t in datos.trabajadores.items():
        lineas: list[str] = []
        if t.tipo == "patron" and t.patron:
            lineas = [s for fila in datos.patrones.get(t.patron, []) for s in fila.values()
                      if s and s in datos.turnos]
        elif t.tipo == "fijo" and t.linea:
            lineas = [t.linea]
        if not lineas:
            lineas = [s for (ww, s) in datos.capacidades if ww == w and s in datos.turnos]
        cuenta = Counter(datos.turnos[s].municipio for s in lineas)
        muni[w] = cuenta.most_common(1)[0][0] if cuenta else "Valladolid"
    return muni


def _categoria(datos: Datos, d: date, municipio: str | None = None) -> str:
    """Categoría de un día PARA UN MUNICIPIO concreto: festivo, finde o laborable. Si no se pasa
    municipio se mira solo el calendario común (para la cabecera, que es de toda la plantilla)."""
    if municipio is None:
        if d in datos.festivos.get("Nacional", set()):
            return "festivo"
    elif datos.es_festivo(d, municipio):
        return "festivo"
    return "finde" if d.weekday() >= 5 else "lv"


def _ajustar_anchos(ws, desde_fila: int = 1, saltar: set[int] | None = None,
                    minimo: int = 4, maximo: int = 24) -> None:
    """Ajusta cada columna al contenido más largo que tenga, para que no haya que tocar el Excel a
    mano. openpyxl no trae autoajuste: hay que medir el texto y fijar el ancho.

    Se ignoran las filas de TÍTULO (`saltar`) y todo lo anterior a `desde_fila`: son textos largos
    que viven en la columna A —el encabezado del cuadrante, la línea de KPIs— y la dejarían
    absurdamente ancha sin aportar nada. Ahí van también las dos filas de cabecera del calendario:
    el mes y la fecha `dd/mm/aaaa`, que al ir GIRADA ocupa alto, no ancho, y ensancharía cada
    columna de día para nada. Los turnos más largos son de 9 caracteres (VADU47127, REF CAL M), así
    que el ancho fijo de 8 que había antes los cortaba."""
    saltar = saltar or set()
    anchos: dict[int, int] = {}
    for fila in ws.iter_rows(min_row=desde_fila):
        for c in fila:
            if c.value is None or c.row in saltar:
                continue
            if isinstance(c.value, str) and c.value.startswith("="):
                continue        # una fórmula mide 200 caracteres y no es lo que se ve en la celda
            largo = max(len(t) for t in str(c.value).split("\n"))
            if largo > anchos.get(c.column, 0):
                anchos[c.column] = largo
    for col, largo in anchos.items():
        ws.column_dimensions[get_column_letter(col)].width = min(maximo, max(minimo, largo + 2))


def _calendarios_festivos(datos: Datos) -> dict[str, set[date]]:
    """calendario -> fechas festivas EFECTIVAS (las comunes más las suyas). Es lo que mira
    `Datos.es_festivo`, resuelto de una vez para poder volcarlo como banderas por día."""
    comun = datos.festivos.get("Nacional", set())
    cals = {datos.calendario_municipio.get(t.municipio, t.municipio) for t in datos.turnos.values()}
    return {c: comun | datos.festivos.get(c, set()) for c in sorted(cals)}


def _calendario_de(datos: Datos, turno: str) -> str:
    t = datos.turnos[turno]
    return datos.calendario_municipio.get(t.municipio, t.municipio)


def _bloques(datos: Datos) -> list[tuple[str, list[str]]]:
    """Orden simple de filas para el cuadrante: fijos, patrones (agrupados por patrón), mixtos,
    correturnos. Sin la lógica de "dedicados"/pueblos de `_bloques` (esa usa `turno.prioridad`,
    que no existe en el modelo de datos)."""
    def sel(tipo: str) -> list[str]:
        return sorted((w for w, t in datos.trabajadores.items() if t.tipo == tipo),
                      key=lambda w: (datos.trabajadores[w].patron or "", w))
    bloques = [("FIJOS", sel("fijo")), ("PATRONES", sel("patron")),
               ("MIXTOS", sel("mixto")), ("CORRETURNOS", sel("correturno"))]
    return [(tit, g) for tit, g in bloques if g]


def _meses(fechas: list[date]) -> list[tuple[str, str, int, int]]:
    """Trocea el calendario en meses: (identificador, rótulo, primer índice, último índice).

    El identificador va a un rango con nombre, así que solo lleva el año cuando hace falta —si el
    horizonte cruza de año, "Enero" sería ambiguo y pasan a ser `CalendarioEnero2026`/`...2027`—."""
    bloques: list[list] = []
    for j, d in enumerate(fechas):
        if bloques and bloques[-1][0] == (d.year, d.month):
            bloques[-1][2] = j
        else:
            bloques.append([(d.year, d.month), j, j])
    varios_anios = len({a for (a, _), _, _ in bloques}) > 1
    return [(f"{MESES[m - 1]}{a if varios_anios else ''}",
             f"{MESES[m - 1]} {a}" if varios_anios else MESES[m - 1], j0, j1)
            for (a, m), j0, j1 in bloques]


def _cobertura(plan: dict[tuple[str, date], str]) -> dict[tuple[str, date], int]:
    contador: dict[tuple[str, date], int] = {}
    for (_, f), s in plan.items():
        if s in DESCANSOS:              # un DO ocupa el día, pero no cubre plaza de nada
            continue
        contador[(s, f)] = contador.get((s, f), 0) + 1
    return contador


def _etiquetas_descanso(datos: Datos, plan: dict[tuple[str, date], str]) -> dict[tuple[str, date], str]:
    """(trabajador, fecha) -> DS | DF, para los días que el cuadrante deja libres.

    No decide NADA de la planificación: reparte nombres sobre los descansos que ya hay.

      * DS — hasta DOS por semana ISO. Si esa semana hay una pareja de días seguidos se cogen
        esos, que es como se da el descanso semanal de verdad; si no los hay, se cogen los
        sueltos que tenga, hasta dos.
      * DF — uno por cada festivo trabajado, en el día libre más próximo que no sea ya DS:
        primero se busca hacia delante y, si no queda año por delante, hacia atrás. Solo se
        quedan sin colocar si esa persona no tiene NINGÚN día libre suelto (el caso de UVI, con
        todos sus descansos marcados DO por el patrón).

    El DO no se toca: ya viene en el plan desde el patrón (y heredado por el cubridor cuando
    asume un bloque de 24 h), así que ni se calcula ni se pisa.
    """
    etiquetas: dict[tuple[str, date], str] = {}
    fechas = datos.lista_dias_calendario

    for trabajador_id in datos.trabajadores:
        libres = [f for f in fechas
                  if datos.disponible(trabajador_id, f)
                  and (trabajador_id, f) not in plan]     # el DO sí está en el plan: queda fuera

        # -- DS: hasta dos por semana ISO, prefiriendo la pareja consecutiva ------------------ #
        por_semana: dict[tuple[int, int], list[date]] = {}
        for f in libres:
            por_semana.setdefault(f.isocalendar()[:2], []).append(f)

        ds: set[date] = set()
        for dias_libres in por_semana.values():
            pareja = next(((a, b) for a, b in zip(dias_libres, dias_libres[1:])
                           if (b - a).days == 1), None)
            elegidos = list(pareja) if pareja else dias_libres[:2]
            ds.update(elegidos)

        for f in ds:
            etiquetas[(trabajador_id, f)] = DS

        # -- DF: uno por festivo trabajado, en el primer libre posterior que no sea DS -------- #
        sobrantes = [f for f in libres if f not in ds]
        festivos_trabajados = [f for f in fechas
                               if (s := turno_de(plan, trabajador_id, f)) is not None
                               and datos.tipo_dia(f, datos.turnos[s].municipio) == "FEST"]
        for festivo in festivos_trabajados:
            # El día libre más próximo POSTERIOR, que es la compensación natural. Si no queda
            # ninguno vale uno ANTERIOR: lo que justifica el DF es haber trabajado el festivo, no
            # el orden. Sin esta vuelta atrás, los festivos de diciembre se quedaban sin compensar
            # simplemente porque no les quedaba año por delante.
            hueco = next((f for f in sobrantes if f > festivo), None)
            if hueco is None:
                hueco = next((f for f in reversed(sobrantes) if f < festivo), None)
            if hueco is None:
                continue                                  # sin día libre donde descontarlo
            sobrantes.remove(hueco)
            etiquetas[(trabajador_id, hueco)] = DF

    return etiquetas


def escribir_excel(datos: Datos, plan: dict[tuple[str, date], str],
                      nombre: str = "calendario.xlsx") -> Path:
    """Vuelca el cuadrante a XLSX.

    Hoja `Cuadrante`: nombre, las cinco métricas vivas de la persona (sábados, domingos, festivos,
    días de vacaciones y horas), la rejilla trabajador x día y, debajo, la tabla COBERTURA DÍA A DÍA — una fila por línea, con fórmulas que muestran
    "asignados/demanda" de esa línea cada día y un semáforo por formato condicional (verde cubierto
    exacto, amarillo sobra gente, rojo falta) para poder comprobar, incluso después de retocar el
    cuadrante a mano, si un día se queda o no cubierto.

    Hoja `Conversión Comité`: el PLAN FUNCIONAL —una fila por línea con sus días de operación,
    horario, horas, demanda diaria, demanda anual y "Asignados" en vivo (fórmula que lee de
    `Cuadrante`)— más una columna Leyenda Comité en blanco para que el comité la rellene, una
    leyenda por turno.

    Una hoja `Aux` oculta lleva las banderas por día (sábado, domingo y festivo de cada calendario)
    y la demanda diaria de cada línea (0 los días que no opera); es de donde leen las fórmulas
    anteriores, y no cabe en las hojas visibles sin ensuciarlas.

    Todo día sin turno asignado —descanso de rotación, libranza cedida o simplemente sin decidir— se
    deja como CELDA VACÍA: en el cuadrante que se entrega no hay más marcas que el turno, la V de
    vacaciones o nada.
    """
    SALIDA.mkdir(parents=True, exist_ok=True)
    fechas = datos.lista_dias_calendario
    wb = Workbook()
    # Explícito y no confiado al valor por defecto de cada programa: cálculo automático, y
    # recalcular todo la primera vez que se abre (por si el programa lo dejó a medias al generarlo).
    # OJO: nada de forceFullCalc/calcOnSave — eso pide recalcular el libro ENTERO en cada pasada, no
    # solo lo que depende de la celda tocada, y con una hoja de este tamaño se nota mucho, se vuelve
    # lento editar. calcMode="auto" ya basta para que se refresque solo edición a edición.
    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True
    ws = wb.active
    ws.title = "Cuadrante"

    centro = Alignment(horizontal="center", vertical="center", wrap_text=True)
    vertical = Alignment(horizontal="center", vertical="center", textRotation=90)
    izq = Alignment(horizontal="left", vertical="center")
    lado = Side(style="thin", color="CCCCCC")
    borde = Border(left=lado, right=lado, top=lado, bottom=lado)
    negrita = Font(bold=True)
    negro = Font(color="000000", bold=True)

    cobertura = _cobertura(plan)
    demanda = sum(t.dem for s, t in datos.turnos.items() for f in fechas if datos.opera(s, f))
    faltan = {(s, f): max(0, t.dem - cobertura.get((s, f), 0))
              for s, t in datos.turnos.items() for f in fechas if datos.opera(s, f)}
    n_deficit = sum(faltan.values())

    # Ordenado por CALENDARIO: la fórmula de festivos referencia las líneas de cada calendario como
    # un RANGO, así que tienen que ocupar filas contiguas en Conversión Comité.
    fes_cal = _calendarios_festivos(datos)
    lineas = sorted(datos.turnos, key=lambda s: (_calendario_de(datos, s), s))
    tramo_cal = {c: [i for i, s in enumerate(lineas) if _calendario_de(datos, s) == c]
                 for c in fes_cal}

    ws.cell(1, 1, f"Cuadrante {fechas[0]:%d/%m/%Y} – {fechas[-1]:%d/%m/%Y}"
            ).font = Font(bold=True, size=14)
    ws.cell(2, 1, f"Cobertura {demanda - n_deficit}/{demanda} "
                  f"({(demanda - n_deficit) / demanda:.1%})  |  sin cubrir {n_deficit}  |  "
                  f"{len(plan)} asignaciones").alignment = izq
    ndias = len(fechas)
    COL_DN = COL_D0 + ndias - 1
    L_D0, L_DN = get_column_letter(COL_D0), get_column_letter(COL_DN)
    meses = _meses(fechas)

    # Nombre y métricas: una sola celda alta, fusionada por las tres filas de cabecera, para que la
    # rejilla de días no arrastre rótulos a media altura.
    for col, titulo in [(COL_NOMBRE, "Nombre")] + [(COL_M0 + k, tit)
                                                   for k, (tit, _) in enumerate(METRICAS)]:
        ws.merge_cells(start_row=F_MES, start_column=col, end_row=F_DIA, end_column=col)
        for fr in range(F_MES, F_DIA + 1):
            ws.cell(fr, col).border = borde
        c = ws.cell(F_MES, col, titulo)
        c.alignment, c.font = centro, negrita

    # Fila del mes: una celda fusionada por mes, alternando gris para que se vea el corte.
    for i, (_, rotulo, j0, j1) in enumerate(meses):
        ws.merge_cells(start_row=F_MES, start_column=COL_D0 + j0,
                       end_row=F_MES, end_column=COL_D0 + j1)
        for j in range(j0, j1 + 1):
            c = ws.cell(F_MES, COL_D0 + j)
            c.border = borde
            c.fill = PatternFill("solid", fgColor="D9D9D9" if i % 2 else "F2F2F2")
        c = ws.cell(F_MES, COL_D0 + j0, rotulo)
        c.alignment, c.font = centro, negrita

    # Fecha completa en VERTICAL (texto girado 90°): así la columna no tiene que ensancharse para
    # que quepa "05/01/2026", y el día entero sigue siendo una sola celda. Y debajo, la inicial del
    # día de la semana —o F si es festivo—, con el color de siempre.
    for j, d in enumerate(fechas):
        cat = _categoria(datos, d)
        relleno = PatternFill("solid", fgColor=CAT_FILL[cat])
        c = ws.cell(F_FECHA, COL_D0 + j, f"{d:%d/%m/%Y}")
        c.alignment, c.font, c.border, c.fill = vertical, negrita, borde, relleno
        c = ws.cell(F_DIA, COL_D0 + j, "F" if cat == "festivo" else DIA_INI[d.weekday()])
        c.alignment, c.font, c.border, c.fill = centro, negrita, borde, relleno
    ws.row_dimensions[F_FECHA].height = 70

    muni = _municipio_trabajador(datos)
    etiquetas = _etiquetas_descanso(datos, plan)
    fila_de: dict[str, int] = {}
    titulos: set[int] = {F_MES, F_FECHA}
    r = F_DIA + 1
    # Un único bloque continuo: sin fila de título ni hueco entre fijos/patrones/mixtos/correturnos.
    # El orden de `_bloques` se conserva (agrupa por tipo), así que la separación visual no hacía
    # falta y complicaba el cuadrante.
    for _, gente in _bloques(datos):
        for trab in gente:
            t = datos.trabajadores[trab]
            ws.cell(r, COL_NOMBRE, t.nombre).alignment = izq
            for j, d in enumerate(fechas):
                c = ws.cell(r, COL_D0 + j)
                c.alignment, c.border = centro, borde
                # Descansos con nombre: el DO viene en el plan (del patrón, o heredado por el
                # cubridor con el bloque) y DS/DF los reparte `_etiquetas_descanso`.
                en_plan = plan.get((trab, d))
                etiqueta = en_plan if en_plan in DESCANSOS else etiquetas.get((trab, d))

                if not datos.disponible(trab, d):
                    c.value, color = "V", FILL_VAC_XL
                    c.font = negro
                elif etiqueta is not None:
                    # Llevan su propio color en vez del color de categoría del día: qué día es
                    # (festivo, finde) se sigue leyendo en la cabecera de la columna.
                    c.value, color = etiqueta, FILL_DESCANSO[etiqueta]
                    c.font = negro
                else:
                    turno = plan.get((trab, d), "")
                    # El festivo se mira con el municipio de SU turno de ese día, que es exacto; si
                    # libra, con el municipio de referencia del trabajador.
                    m = datos.turnos[turno].municipio if turno else muni[trab]
                    c.value, color = turno, CAT_FILL[_categoria(datos, d, m)]
                c.fill = PatternFill("solid", fgColor=color)
            fila_de[trab] = r
            r += 1
    W0, W1 = F_DIA + 1, r - 1

    # -- Hoja Aux (oculta): qué día es sábado, domingo o festivo ------------ #
    #
    # Las tres banderas son EXCLUYENTES y van por calendario, igual que `Datos.tipo_dia`: un sábado
    # que además es festivo cuenta como festivo y NO como sábado. Con banderas crudas de día de la
    # semana la hoja lo sumaba en las dos columnas y se separaba de las métricas del pipeline (26
    # trabajadores con un sábado de más). Y tiene que ser por calendario porque hay municipios con
    # festivos propios: el mismo día es festivo para unos y sábado normal para otros.
    aux = wb.create_sheet("Aux")
    aux.cell(1, 1, "banderas por día y calendario (excluyentes); las usan las fórmulas del Cuadrante")
    for j, d in enumerate(fechas):
        aux.cell(2, COL_D0 + j, f"{d:%d/%m/%Y}")
    F_CAL: dict[str, dict[str, int]] = {}
    for i, c in enumerate(fes_cal):
        filas_c = {"SAB": 3 + 3 * i, "DOM": 4 + 3 * i, "FEST": 5 + 3 * i}
        F_CAL[c] = filas_c
        for clase, fr in filas_c.items():
            aux.cell(fr, 1, f"{clase.lower()} {c}")
            for j, d in enumerate(fechas):
                festivo = d in fes_cal[c]
                marca = (festivo if clase == "FEST" else
                         (not festivo and d.weekday() == (5 if clase == "SAB" else 6)))
                aux.cell(fr, COL_D0 + j, 1 if marca else 0)
    aux.sheet_state = "hidden"

    # -- Cobertura día a día: semáforo vivo por línea y día ------------------ #
    # El relleno de un formato CONDICIONAL (a diferencia del relleno fijo de la rejilla) sí respeta
    # el canal alfa: sin "FF" por delante queda transparente y la regla se aplica pero no se ve nada.
    fill_ok = PatternFill("solid", start_color="FF" + FILL_COBERTURA_OK,end_color="FF" + FILL_COBERTURA_OK)
    fill_exceso = PatternFill("solid", start_color="FF" + FILL_COBERTURA_EXCESO,end_color="FF" + FILL_COBERTURA_EXCESO)
    fill_falta = PatternFill("solid", start_color="FF" + FILL_COBERTURA_FALTA,end_color="FF" + FILL_COBERTURA_FALTA)

    r = W1 + 2
    ws.cell(r, 1, "COBERTURA DÍA A DÍA").font = Font(bold=True, size=12)
    titulos.add(r)
    COB0 = r + 1

    # Demanda diaria de cada línea (0 los días que no opera), EN LA MISMA HOJA que el semáforo y
    # oculta bajo la tabla: el formato condicional no puede fiarse de una referencia a OTRA hoja
    # (Aux) para su fórmula — es un caso especial que varios motores (no solo Excel) resuelven mal o
    # no evalúan nunca, a diferencia de una fórmula normal de celda, que sí cruza de hoja sin líos.
    DEM_ROW: dict[str, int] = {}
    dem_fila0 = COB0 + len(lineas) + 2
    for i, s in enumerate(lineas):
        fr = dem_fila0 + i
        DEM_ROW[s] = fr
        t = datos.turnos[s]
        ws.cell(fr, 1, f"dem {s}")
        for j, d in enumerate(fechas):
            ws.cell(fr, COL_D0 + j, t.dem if datos.opera(s, d) else 0)
        ws.row_dimensions[fr].hidden = True
    titulos.update(range(dem_fila0, dem_fila0 + len(lineas)))

    for i, s in enumerate(lineas):
        fr = COB0 + i
        ws.cell(fr, 1, s).alignment = izq
        demrow = DEM_ROW[s]
        for j, d in enumerate(fechas):
            col = get_column_letter(COL_D0 + j)
            c = ws.cell(fr, COL_D0 + j)
            c.alignment, c.border = centro, borde
            c.value = (f'=IF({col}{demrow}=0,"",'
                       f'COUNTIF({col}${W0}:{col}${W1},$A{fr})&"/"&{col}{demrow})')
        rng = f"{L_D0}{fr}:{L_DN}{fr}"
        cuenta = f"COUNTIF({L_D0}${W0}:{L_D0}${W1},$A${fr})"
        dem = f"{L_D0}${demrow}"
        # Verde también los días que la línea NO opera (dem=0): no hay nada que cubrir, así que no
        # es un problema — y de paso la fila no se queda con huecos en blanco sin más.
        ws.conditional_formatting.add(
            rng, FormulaRule(formula=[f"OR({dem}=0,{cuenta}={dem})"], fill=fill_ok))
        ws.conditional_formatting.add(
            rng, FormulaRule(formula=[f"AND({dem}<>0,{cuenta}>{dem})"], fill=fill_exceso))
        ws.conditional_formatting.add(
            rng, FormulaRule(formula=[f"AND({dem}<>0,{cuenta}<{dem})"], fill=fill_falta))

    # -- Hoja Conversión Comité: plan funcional + leyenda a rellenar por el comité ---------- #
    ws2 = wb.create_sheet("Conversión Comité")
    ws2.cell(1, 1, "PLAN FUNCIONAL").font = Font(bold=True, size=12)
    PF_HDR = 2
    CAMPOS = ("Línea", "Municipio", "LV", "Sábado", "Domingo", "Festivo", "Entrada", "Salida",
              "Horas","Leyenda Comité")
    for k, titulo in enumerate(CAMPOS):
        c = ws2.cell(PF_HDR, 1 + k, titulo)
        c.alignment, c.font, c.border = centro, negrita, borde

    PF0 = PF_HDR + 1
    for i, s in enumerate(lineas):
        t, fr = datos.turnos[s], PF0 + i
        opera = sum(1 for d in fechas if datos.opera(s, d))
        vals = (s, t.municipio, t.lv, t.sab, t.dom, t.fes,
                f"{t.hora_entrada:%H:%M}", f"{t.hora_salida:%H:%M}", t.horas,None)
        for k, v in enumerate(vals):
            c = ws2.cell(fr, 1 + k, v)
            c.alignment, c.border = (izq if k < 2 else centro), borde
            if k == 8:
                c.number_format = "0.00"
    PF1 = PF0 + len(lineas) - 1
    T_ID = f"'Conversión Comité'!$A${PF0}:$A${PF1}"
    T_HORAS = f"'Conversión Comité'!$I${PF0}:$I${PF1}"                    # columna 9 = Horas

    # -- Las cinco métricas de la persona, ya como fórmulas ----------------- #
    for trab, fr in fila_de.items():
        D = f"{L_D0}{fr}:{L_DN}{fr}"
        # `COUNTIF(lista_de_líneas, fila_de_días)` da un 1 por cada día en que la celda contiene una
        # línea real y 0 en libres, vacaciones o texto suelto. Se usa esto y no MATCH porque MATCH
        # sobre un rango solo devuelve vector como fórmula matricial, y esta tiene que funcionar tal
        # cual la escribe el fichero.
        # Las tres clases de día dependen del CALENDARIO de la línea que se hace ese día, así que
        # cada una suma un término por calendario contra el rango de SUS líneas.
        def por_calendario(clase: str) -> str:
            return "=" + "+".join(
                f"SUMPRODUCT(Aux!${L_D0}${F_CAL[c][clase]}:${L_DN}${F_CAL[c][clase]},"
                f"COUNTIF('Conversión Comité'!$A${PF0 + tramo[0]}:$A${PF0 + tramo[-1]},{D}))"
                for c, tramo in tramo_cal.items() if tramo)

        formulas = [
            por_calendario("SAB"),
            por_calendario("DOM"),
            por_calendario("FEST"),
            # Vacaciones: los días de ausencia son los únicos que llevan V en la rejilla.
            f'=COUNTIF({D},"V")',
            f"=SUMPRODUCT(COUNTIF({D},{T_ID}),{T_HORAS})",
        ]
        for k, v in enumerate(formulas):
            c = ws.cell(fr, COL_M0 + k, v)
            c.alignment, c.border, c.font = centro, borde, negrita
            c.number_format = "0.0" if k == len(formulas) - 1 else "0"

    _ajustar_anchos(ws, desde_fila=F_MES, saltar=titulos)
    # El rótulo de cada métrica vive en una celda fusionada de la fila del mes, que el autoajuste no
    # mide: se le da el ancho a mano para que no lo corte.
    for k, (titulo, _) in enumerate(METRICAS):
        ws.column_dimensions[get_column_letter(COL_M0 + k)].width = len(titulo) + 2
    # El nombre es ahora la ÚNICA identificación de la fila, así que se sale del tope general de
    # anchura: cortado no identifica a nadie.
    ws.column_dimensions[get_column_letter(COL_NOMBRE)].width = min(
        36, max(len(datos.trabajadores[w].nombre) for w in fila_de) + 2)
    ws.freeze_panes = f"{L_D0}{W0}"
    _ajustar_anchos(ws2, desde_fila=1, saltar={2})
    ws2.freeze_panes = f"A{PF0}"

    # -- Rangos con nombre: para que un lector externo (p.ej. el script del comité que traduce el
    # cuadrante a PDF) localice las tablas por NOMBRE en vez de tener que buscar celdas por su texto
    # ("Nombre", "Línea"...), que es frágil si algún día cambia una etiqueta. --------------------- #
    # La columna de Leyenda Comité se calcula desde CAMPOS, no se hardcodea la letra: si el día de
    # mañana se añade o se quita una columna al plan funcional, este rango no se desincroniza solo.
    col_leyenda = get_column_letter(1 + CAMPOS.index("Leyenda Comité"))
    L_NOM = get_column_letter(COL_NOMBRE)
    rangos = {
        "CuadranteTitulo": "Cuadrante!$A$1",
        # La columna de la gente, del primer trabajador al último.
        "Trabajadores": f"Cuadrante!${L_NOM}${W0}:${L_NOM}${W1}",
        # El calendario entero y las dos filas que lo encabezan.
        "Calendario": f"Cuadrante!${L_D0}${W0}:${L_DN}${W1}",
        "CalendarioFechas": f"Cuadrante!${L_D0}${F_FECHA}:${L_DN}${F_FECHA}",
        "CalendarioDiaSemana": f"Cuadrante!${L_D0}${F_DIA}:${L_DN}${F_DIA}",
        "ComiteLinea": f"'Conversión Comité'!$A${PF0}:$A${PF1}",
        "ComiteLeyenda": f"'Conversión Comité'!${col_leyenda}${PF0}:${col_leyenda}${PF1}",
    }
    # Una métrica, un nombre: quien lea el fichero desde fuera no tiene que contar columnas.
    for k, (_, ident) in enumerate(METRICAS):
        col = get_column_letter(COL_M0 + k)
        rangos[ident] = f"Cuadrante!${col}${W0}:${col}${W1}"
    # Y un rango por mes, con el mismo alto que `Calendario` pero solo sus columnas.
    for ident, _, j0, j1 in meses:
        a, b = get_column_letter(COL_D0 + j0), get_column_letter(COL_D0 + j1)
        rangos[f"Calendario{ident}"] = f"Cuadrante!${a}${W0}:${b}${W1}"
    for nombre_rango, referencia in rangos.items():
        wb.defined_names[nombre_rango] = DefinedName(nombre_rango, attr_text=referencia)

    ruta = SALIDA / nombre
    wb.save(ruta)
    print(f"\nCuadrante: {ruta.relative_to(RAIZ)}  "
          f"(asignaciones={len(plan)}, sin cubrir={n_deficit})")
    return ruta

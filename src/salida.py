"""
salida.py — El cuadrante a XLSX: rejilla trabajador x dia, comprobante de cobertura y plan
funcional para el comité.

La hoja Cuadrante es VIVA: los cuatro recuentos de la derecha —sábados, domingos, festivos y
horas— y la tabla COBERTURA DÍA A DÍA de debajo son FÓRMULAS, no números pegados. Si el
planificador escribe una línea en una celda del calendario, se recalculan solos: los recuentos con
los datos DE ESA LÍNEA (sus horas, y si ese día es festivo según el calendario de su municipio) y la
tabla de cobertura con su semáforo —verde cubierto exacto, amarillo sobra gente, rojo falta— de esa
línea ese día. Así una alteración manual posterior a la generación del cuadrante se ve al momento.

La hoja Conversión Comité lleva el plan funcional (una fila por línea, con su demanda y su
"Asignados" en vivo) y una columna Leyenda Comité en blanco para que el comité la rellene, una
leyenda por turno.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

from cargar_datos import Datos

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "data" / "output"

DIA_INI = ["L", "M", "X", "J", "V", "S", "D"]

# Colores del Excel. El de FESTIVO se aplica CELDA A CELDA, no a la columna entera: un festivo local
# solo tiñe a quien trabaja en un municipio que se acoge a ese calendario.
CAT_FILL = {"lv": "FFF2CC", "finde": "F8CBAD", "festivo": "FFC000"}
FILL_VAC_XL = "FFFF00"
# Semáforo de COBERTURA DÍA A DÍA, por formato condicional (no relleno fijo) para que siga vivo si
# el planificador retoca el cuadrante después de generarlo: verde asignados = demanda, amarillo
# asignados > demanda (sobra gente), rojo asignados < demanda (falta gente).
FILL_COBERTURA_OK, FILL_COBERTURA_EXCESO, FILL_COBERTURA_FALTA = "C6EFCE", "FFEB9C", "FFC7CE"


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
    que viven en la columna A —el encabezado del cuadrante, la línea de KPIs, el rótulo del bloque
    de pueblos— y la dejarían absurdamente ancha sin aportar nada. En las cabeceras de día
    ("L\\n05/01") se mide la línea más larga, no el total. Los turnos más largos son de 9 caracteres
    (VADU47127, REF CAL M), así que el ancho fijo de 8 que había antes los cortaba."""
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


def _cobertura(plan: dict[tuple[str, date], str]) -> dict[tuple[str, date], int]:
    contador: dict[tuple[str, date], int] = {}
    for (_, f), s in plan.items():
        contador[(s, f)] = contador.get((s, f), 0) + 1
    return contador


def escribir_excel(datos: Datos, plan: dict[tuple[str, date], str],
                      nombre: str = "calendario.xlsx") -> Path:
    """Vuelca el cuadrante a XLSX.

    Hoja `Cuadrante`: SOLO la rejilla trabajador x día (con sus cuatro recuentos vivos de la
    derecha) y, debajo, la tabla COBERTURA DÍA A DÍA — una fila por línea, con fórmulas que muestran
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
    HDR = 3
    ws.cell(HDR, 1, "Trabajador").font = negrita
    ws.cell(HDR, 2, "Nombre").font = negrita
    ws.cell(HDR, 3, "Tipo").font = negrita
    ndias = len(fechas)
    COL_D0, COL_DN = 4, 3 + ndias
    L_D0, L_DN = get_column_letter(COL_D0), get_column_letter(COL_DN)
    COL_EXTRA = 4 + ndias

    for j, d in enumerate(fechas):
        c = ws.cell(HDR, COL_D0 + j, f"{DIA_INI[d.weekday()]}\n{d:%d/%m}")
        c.alignment, c.font, c.border = centro, negrita, borde
        c.fill = PatternFill("solid", fgColor=CAT_FILL[_categoria(datos, d)])
    for k, titulo in enumerate(("Sábados", "Domingos", "Festivos", "Horas")):
        c = ws.cell(HDR, COL_EXTRA + k, titulo)
        c.alignment, c.font, c.border = centro, negrita, borde

    muni = _municipio_trabajador(datos)
    fila_de: dict[str, int] = {}
    titulos: set[int] = set()
    r = HDR + 1
    # Un único bloque continuo: sin fila de título ni hueco entre fijos/patrones/mixtos/correturnos.
    # El orden de `_bloques` se conserva (agrupa por tipo), y la columna Tipo ya dice a qué grupo
    # pertenece cada fila, así que la separación visual no hacía falta y complicaba el cuadrante.
    for _, gente in _bloques(datos):
        for trab in gente:
            t = datos.trabajadores[trab]
            ws.cell(r, 1, trab).alignment = izq
            ws.cell(r, 2, t.nombre).alignment = izq
            ws.cell(r, 3, t.patron if t.tipo == "patron" else t.tipo).alignment = izq
            # Columna oculta con el tipo MACRO (fijo/patron/mixto/correturno), sin diluir con el
            # nombre del patrón como hace la columna Tipo visible: es de aquí de donde separa por
            # secciones el PDF del comité, y necesita el grupo grueso, no la fila de detalle.
            ws.cell(r, COL_EXTRA + 4, t.tipo)
            for j, d in enumerate(fechas):
                c = ws.cell(r, COL_D0 + j)
                c.alignment, c.border = centro, borde
                if not datos.disponible(trab, d):
                    c.value, color = "V", FILL_VAC_XL
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
    W0, W1 = HDR + 1, r - 1

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

    # -- Los cuatro recuentos, ya como fórmulas ----------------------------- #
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
            f"=SUMPRODUCT(COUNTIF({D},{T_ID}),{T_HORAS})",
        ]
        for k, v in enumerate(formulas):
            c = ws.cell(fr, COL_EXTRA + k, v)
            c.alignment, c.border, c.font = centro, borde, negrita
            if k == 3:
                c.number_format = "0.0"

    _ajustar_anchos(ws, desde_fila=HDR, saltar=titulos)
    ws.freeze_panes = "D5"
    _ajustar_anchos(ws2, desde_fila=1, saltar={2})
    ws2.freeze_panes = f"A{PF0}"

    # -- Rangos con nombre: para que un lector externo (p.ej. el script del comité que traduce el
    # cuadrante a PDF) localice las tablas por NOMBRE en vez de tener que buscar celdas por su texto
    # ("Trabajador", "Línea"...), que es frágil si algún día cambia una etiqueta. ------------------ #
    # La columna de Leyenda Comité se calcula desde CAMPOS, no se hardcodea la letra: si el día de
    # mañana se añade o se quita una columna al plan funcional, este rango no se desincroniza solo.
    col_leyenda = get_column_letter(1 + CAMPOS.index("Leyenda Comité"))
    for nombre_rango, referencia in {
        "CuadranteTitulo": "Cuadrante!$A$1",
        "CuadranteDNI": f"Cuadrante!$A${W0}:$A${W1}",
        "CuadranteNombre": f"Cuadrante!$B${W0}:$B${W1}",
        "CuadranteDias": f"Cuadrante!${L_D0}${W0}:${L_DN}${W1}",
        "CuadranteCabecera": f"Cuadrante!${L_D0}${HDR}:${L_DN}${HDR}",
        "CuadranteGrupo": f"Cuadrante!${get_column_letter(COL_EXTRA + 4)}${W0}:"
                           f"${get_column_letter(COL_EXTRA + 4)}${W1}",
        "ComiteLinea": f"'Conversión Comité'!$A${PF0}:$A${PF1}",
        "ComiteLeyenda": f"'Conversión Comité'!${col_leyenda}${PF0}:${col_leyenda}${PF1}",
    }.items():
        wb.defined_names[nombre_rango] = DefinedName(nombre_rango, attr_text=referencia)
    ws.column_dimensions[get_column_letter(COL_EXTRA + 4)].hidden = True

    ruta = SALIDA / nombre
    wb.save(ruta)
    print(f"\nCuadrante: {ruta.relative_to(RAIZ)}  "
          f"(asignaciones={len(plan)}, sin cubrir={n_deficit})")
    return ruta

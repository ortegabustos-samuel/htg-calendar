#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
salida.py — Visualización y volcado del cuadrante resuelto.

A partir de un modelo resuelto genera en data/output/calendario.xlsx: rejilla
trabajadores × días (coloreada por tipo), con vacaciones/libres, KPIs y turnos
sin cubrir. También imprime un resumen por consola.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from cargar_datos import Datos
from modelo import (JORNADA_LOCALIZADO_SEMANA, LAMBDA, METRICAS,
                    _patrones_uvi, jornada_minutos, lineas_localizadas,
                    peso_cobertura, semana)

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "data" / "output"

DIA_INI = ["L", "M", "X", "J", "V", "S", "D"]

# Colores del Excel. El de FESTIVO se aplica CELDA A CELDA, no a la columna entera: un festivo local
# solo tiñe a quien trabaja en un municipio que se acoge a ese calendario (hay 6 municipios repartidos
# entre 2 calendarios locales, así que un mismo día es festivo para unos y laborable para otros).
CAT_FILL = {"lv": "FFF2CC", "finde": "F8CBAD", "festivo": "FFC000"}
FILL_VAC_XL, FILL_HUECO_XL = "FFFF00", "F4B084"


# --------------------------------------------------------------------------- #
#  Extracción de la solución
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
#  Excel (formato de la empresa)
# --------------------------------------------------------------------------- #
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
        if d in datos.festivos.get("Comun", set()):
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


def _bloques(datos: Datos, muni: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Orden en que se apilan las filas del cuadrante, agrupando gente que se lee junta:
    fijos · patrones dedicados (UVI y noches) · mixtos · patrón grande de la ciudad · correturnos,
    y luego, separados, los patrones de los pueblos.

    Los criterios salen de los datos, no de nombres concretos: un patrón es DEDICADO si su rotación
    incluye alguna línea crítica (prioridad>=2), que es lo que distingue a UVI y noches; y la ciudad
    es el municipio con más plantilla, así que "los pueblos" son los demás. Devuelve una lista de
    (título, trabajadores); el título vacío no imprime separación."""
    principal = Counter(muni.values()).most_common(1)[0][0]
    dedicados = {p for p, filas in datos.patrones.items()
                 if any(datos.turnos[s].prioridad >= 2
                        for fila in filas for s in fila.values() if s in datos.turnos)}

    def sel(cond) -> list[str]:
        return sorted((w for w, t in datos.trabajadores.items() if cond(w, t)),
                      key=lambda w: (datos.trabajadores[w].patron or "", w))

    bloques = [
        ("", sel(lambda w, t: t.tipo == "fijo")),
        ("", sel(lambda w, t: t.tipo == "patron" and t.patron in dedicados)),
        ("", sel(lambda w, t: t.tipo == "mixto")),
        ("", sel(lambda w, t: t.tipo == "patron" and t.patron not in dedicados
                 and muni[w] == principal)),
        ("", sel(lambda w, t: t.tipo == "correturno")),
        (f"PATRONES DE LOS PUEBLOS (fuera de {principal})",
         sel(lambda w, t: t.tipo == "patron" and t.patron not in dedicados
             and muni[w] != principal)),
    ]
    colocados = {w for _, g in bloques for w in g}
    resto = sorted(set(datos.trabajadores) - colocados)
    if resto:                                    # red de seguridad: que no se pierda nadie
        bloques.append(("OTROS", resto))
    return [(tit, g) for tit, g in bloques if g]


def _min(horas: float) -> float:
    """Horas redondeadas al MINUTO. `modelo.jornada_minutos` trabaja en minutos enteros por turno,
    así que el Excel tiene que partir del mismo número: con el decimal exacto (80/7 h de un
    localizado) la hoja se desviaba unos segundos por turno del CSV de métricas, y dos cifras que
    deberían ser la misma no pueden bailar."""
    return round(horas * 60) / 60


def _calendarios_festivos(datos: Datos) -> dict[str, set[date]]:
    """calendario -> fechas festivas EFECTIVAS (las comunes más las suyas). Es lo que mira
    `Datos.es_festivo`, resuelto de una vez para poder volcarlo como banderas por día."""
    comun = datos.festivos.get("Comun", set())
    cals = {datos.calendario_municipio.get(t.municipio, t.municipio) for t in datos.turnos.values()}
    return {c: comun | datos.festivos.get(c, set()) for c in sorted(cals)}


def _calendario_de(datos: Datos, turno: str) -> str:
    t = datos.turnos[turno]
    return datos.calendario_municipio.get(t.municipio, t.municipio)


def _semanas_de(fechas: list[date]) -> list[list[int]]:
    """Índices de columna (0-based sobre `fechas`) agrupados por semana ISO, en orden. Como el
    horizonte arranca en lunes, cada grupo es un bloque de columnas CONTIGUAS: eso es lo que
    permite que una fórmula de Excel pregunte por 'la semana' sin celdas auxiliares por día."""
    grupos: dict[tuple[int, int], list[int]] = defaultdict(list)
    for j, f in enumerate(fechas):
        grupos[semana(f)].append(j)
    return [grupos[k] for k in sorted(grupos)]


def escribir_excel(datos: Datos, fechas: list[date], asign, huecos, kpis: dict) -> None:
    """Vuelca el cuadrante a XLSX. La hoja es VIVA: los cuatro recuentos de la derecha —sábados,
    domingos, festivos y horas— son FÓRMULAS, no números pegados. Si el planificador escribe una
    línea en una celda del calendario, los cuatro se recalculan solos y con los datos DE ESA LÍNEA
    (sus horas, y si el día es festivo según el calendario de su municipio).

    Para que las fórmulas tengan de dónde leer, debajo del calendario va el PLAN FUNCIONAL: una
    fila por línea con sus días de operación (LV/sábado/domingo/festivo), horario, horas computadas,
    horas de jornada, demanda y prioridad. Las fórmulas de horas y de festivos leen de ahí.

    Una hoja `Aux` (oculta) lleva lo que no cabe en el calendario sin ensuciarlo: qué día es sábado,
    domingo o festivo de cada calendario, y en qué semanas asume cada trabajador una plaza de
    localizado — que es lo que permite computarla como semana entera (JORNADA_LOCALIZADO_SEMANA)
    también dentro de Excel."""
    SALIDA.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Cuadrante"

    centro = Alignment(horizontal="center", vertical="center", wrap_text=True)
    izq = Alignment(horizontal="left", vertical="center")
    lado = Side(style="thin", color="CCCCCC")
    borde = Border(left=lado, right=lado, top=lado, bottom=lado)
    negrita = Font(bold=True)

    ws.cell(1, 1, f"Cuadrante {fechas[0]:%d/%m/%Y} – {fechas[-1]:%d/%m/%Y}").font = Font(bold=True, size=14)
    ws.cell(2, 1, f"Cobertura {kpis['cubiertos']}/{kpis['demanda']} ({kpis['pct']:.1f}%)  |  "
                  f"sin cubrir {kpis['huecos']}  |  P1 {kpis['p1']}  |  P2 {kpis['p2']}  |  "
                  f"{kpis['estado']}")

    HDR = 4                                              # fila de cabecera
    ws.cell(HDR, 1, "Trabajador").font = negrita
    ws.cell(HDR, 2, "Tipo").font = negrita
    for j, d in enumerate(fechas):
        c = ws.cell(HDR, 3 + j, f"{DIA_INI[d.weekday()]}\n{d:%d/%m}")
        c.alignment, c.font, c.border = centro, negrita, borde
        c.fill = PatternFill("solid", fgColor=CAT_FILL[_categoria(datos, d)])

    muni = _municipio_trabajador(datos)
    negro = Font(color="000000", bold=True)

    ndias = len(fechas)
    COL_D0, COL_DN = 3, 2 + ndias                        # primera y última columna de día
    L_D0, L_DN = get_column_letter(COL_D0), get_column_letter(COL_DN)
    COL_EXTRA = 3 + ndias                                # recuentos, a la derecha del calendario

    RECUENTOS = ("Sábados", "Domingos", "Festivos", "Horas")
    for k, titulo in enumerate(RECUENTOS):
        c = ws.cell(HDR, COL_EXTRA + k, titulo)
        c.alignment, c.font, c.border = centro, negrita, borde

    def fila(trab: str, t) -> None:
        nonlocal r
        ws.cell(r, 1, trab).alignment = izq
        ws.cell(r, 2, t.patron if t.tipo == "patron" else t.tipo).alignment = izq
        for j, d in enumerate(fechas):
            c = ws.cell(r, COL_D0 + j)
            c.alignment, c.border = centro, borde
            if not datos.disponible(trab, d):
                c.value, color = "V", FILL_VAC_XL
                c.font = negro
            else:
                turno = asign.get((trab, d), "")
                # el festivo se mira con el municipio de SU turno de ese día (exacto) y, si libra,
                # con el municipio de referencia del trabajador
                m = datos.turnos[turno].municipio if turno else muni[trab]
                c.value, color = turno, CAT_FILL[_categoria(datos, d, m)]
            c.fill = PatternFill("solid", fgColor=color)
        fila_de[trab] = r
        r += 1

    r = HDR + 1
    fila_de: dict[str, int] = {}                 # trabajador -> fila, para las fórmulas de después
    titulos: set[int] = set()                    # filas de rótulo: no cuentan para el ancho
    for titulo, gente in _bloques(datos, muni):
        if titulo:                                        # separación del bloque de los pueblos
            r += 1
            c = ws.cell(r, 1, titulo)
            c.font = Font(bold=True, size=12)
            titulos.add(r)
            r += 1
        for trab in gente:
            fila(trab, datos.trabajadores[trab])
    W0, W1 = HDR + 1, r - 1                      # franja de filas de trabajador (con rótulos dentro)

    # Turnos sin cubrir: apilados bajo la columna de su día
    por_dia = defaultdict(list)
    for d, s in huecos:
        por_dia[d].append(s)
    r += 1
    ws.cell(r, 1, "SIN CUBRIR").font = negrita
    base = r + 1
    for j, d in enumerate(fechas):
        for k, s in enumerate(por_dia.get(d, [])):
            c = ws.cell(base + k, COL_D0 + j, s)
            c.alignment, c.border = centro, borde
            c.fill = PatternFill("solid", fgColor=FILL_HUECO_XL)
    r = base + max((len(v) for v in por_dia.values()), default=0) + 1

    # ---------------------------------------------------------------- #
    #  PLAN FUNCIONAL: la tabla de líneas de la que leen las fórmulas
    # ---------------------------------------------------------------- #
    fes_cal = _calendarios_festivos(datos)
    cals = list(fes_cal)
    # Ordenado por CALENDARIO: las fórmulas de festivos necesitan que las líneas de un mismo
    # calendario ocupen filas contiguas (se referencian como un rango, no una a una).
    lineas = sorted(datos.turnos, key=lambda s: (_calendario_de(datos, s), s))
    tramo_cal = {c: [i for i, s in enumerate(lineas) if _calendario_de(datos, s) == c] for c in cals}

    r += 1
    ws.cell(r, 1, "PLAN FUNCIONAL").font = Font(bold=True, size=12)
    titulos.add(r)
    ws.cell(r, 3, "una fila por línea: es de aquí de donde leen las fórmulas de la derecha del "
                  "calendario (horas, sábados, domingos y festivos)").alignment = izq
    r += 1
    PF_HDR = r
    CAMPOS = ("Línea", "Municipio", "LV", "Sábado", "Domingo", "Festivo", "Entrada", "Salida",
              "h. computadas", "h. jornada", "Demanda/día", "Prioridad", "Demanda anual",
              "Asignados")
    for k, titulo in enumerate(CAMPOS):
        c = ws.cell(PF_HDR, 1 + k, titulo)
        c.alignment, c.font, c.border = centro, negrita, borde

    PF0 = PF_HDR + 1
    for i, s in enumerate(lineas):
        t, fr = datos.turnos[s], PF0 + i
        vals = (s, t.municipio, t.lv, t.sab, t.dom, t.fes,
                f"{t.hora_entrada:%H:%M}", f"{t.hora_salida:%H:%M}",
                t.horas, _min(t.horas_consumo), t.dem, t.prioridad,
                t.dem * sum(1 for d in fechas if datos.opera(s, d)),
                f"=COUNTIF({L_D0}${W0}:{L_DN}${W1},$A{fr})")
        for k, v in enumerate(vals):
            c = ws.cell(fr, 1 + k, v)
            c.alignment, c.border = (izq if k < 2 else centro), borde
            if k in (8, 9):
                c.number_format = "0.00"
    PF1 = PF0 + len(lineas) - 1
    # Las filas del plan funcional caen bajo las columnas de DÍA del calendario, así que se dejan
    # fuera del autoajuste: si no, "h. computadas" ensancharía la columna del 3 de enero.
    titulos.update(range(PF_HDR, PF1 + 1))
    T_ID = f"$A${PF0}:$A${PF1}"
    T_JOR = f"$J${PF0}:$J${PF1}"                          # columna 10 = h. jornada

    # ---------------------------------------------------------------- #
    #  Hoja Aux (oculta): banderas por día y semanas de plaza de localizado
    # ---------------------------------------------------------------- #
    aux = wb.create_sheet("Aux")
    aux.cell(1, 1, "banderas por día y semanas de localizado; las usan las fórmulas del Cuadrante")
    for j, d in enumerate(fechas):
        aux.cell(2, COL_D0 + j, f"{d:%d/%m/%Y}")
        aux.cell(3, COL_D0 + j, 1 if d.weekday() == 5 else 0)
        aux.cell(4, COL_D0 + j, 1 if d.weekday() == 6 else 0)
    aux.cell(3, 1, "sábado")
    aux.cell(4, 1, "domingo")
    F_CAL = {}
    for i, c in enumerate(cals):
        fr = 5 + i
        F_CAL[c] = fr
        aux.cell(fr, 1, f"festivo {c}")
        for j, d in enumerate(fechas):
            aux.cell(fr, COL_D0 + j, 1 if d in fes_cal[c] else 0)

    # Semanas: peso de cada una (una semana de plaza de localizado computa JORNADA_LOCALIZADO_SEMANA
    # entera; la que el horizonte parte por la mitad, su parte proporcional) y, por trabajador, en
    # cuáles asume esa plaza. Es lo que hace que Excel cuente igual que `modelo.jornada_minutos`.
    semanas = _semanas_de(fechas)
    FILA_PESO = 5 + len(cals)
    aux.cell(FILA_PESO, 1, "peso semana (h)")
    for k, cols in enumerate(semanas):
        aux.cell(FILA_PESO, 2 + k, _min(JORNADA_LOCALIZADO_SEMANA * min(7, len(cols)) / 7))
    L_SN = get_column_letter(1 + len(semanas))
    R_PESO = f"Aux!$B${FILA_PESO}:${L_SN}${FILA_PESO}"

    loc = sorted(lineas_localizadas(datos))
    lista_loc = "{" + ";".join(f'"{s}"' for s in loc) + "}"
    aux_de: dict[str, int] = {}
    for n, (trab, fr_cal) in enumerate(sorted(fila_de.items())):
        fr = FILA_PESO + 1 + n
        aux_de[trab] = fr
        aux.cell(fr, 1, trab)
        for k, cols in enumerate(semanas):
            a, b = get_column_letter(COL_D0 + cols[0]), get_column_letter(COL_D0 + cols[-1])
            aux.cell(fr, 2 + k, f"=IF(SUMPRODUCT(COUNTIF(Cuadrante!{a}{fr_cal}:{b}{fr_cal},"
                                f"{lista_loc}))>0,1,0)" if loc else 0)
    aux.sheet_state = "hidden"

    # ---------------------------------------------------------------- #
    #  Recuentos del trabajador, ya como FÓRMULAS
    # ---------------------------------------------------------------- #
    # La jornada de una plaza de localizado NO es la suma de sus turnos (ver
    # modelo.JORNADA_LOCALIZADO_SEMANA): se descuenta lo que aportaron turno a turno y se suma la
    # semana entera. Sin esa corrección, Excel y metricas_trabajadores.csv darían cifras distintas.
    descuento = "".join(f'-{_min(datos.turnos[s].horas_consumo):.6f}*COUNTIF({{D}},"{s}")'
                        for s in loc)
    uvi_xl = _patrones_uvi(datos)
    jornada_xl = {w: m / 60 for w, m in jornada_minutos(datos, asign, fechas).items()}

    for trab, fr in fila_de.items():
        D = f"{L_D0}{fr}:{L_DN}{fr}"
        # AJUSTE pactado, sumado dentro de la fórmula de horas. Solo lo llevan los titulares de una
        # rotación de localizado: cubrir su plaza el año entero salvo vacaciones ES su jornada
        # completa por acuerdo con la empresa, aunque la cuenta del calendario dé otra cosa. Se suma
        # como constante y no se recalcula, así que la celda sigue viva: si se les cambia un turno,
        # la parte calculada se mueve y el pacto se mantiene.
        t = datos.trabajadores[trab]
        ajuste = (datos.config.horas_objetivo * t.factor_jornada - jornada_xl.get(trab, 0.0)
                  if t.patron in uvi_xl else 0.0)
        # `COUNTIF(lista_de_líneas, fila_de_días)` da un 1 por cada día en que la celda contiene una
        # línea real y 0 en libres, vacaciones o texto suelto. Se usa esto y no MATCH porque MATCH
        # sobre un rango solo devuelve vector si se introduce como fórmula matricial, y aquí tiene
        # que funcionar tal cual la escribe el fichero.
        pertenece = f"COUNTIF({T_ID},{D})"
        formulas = [
            f"=SUMPRODUCT(Aux!${L_D0}$3:${L_DN}$3,{pertenece})",
            f"=SUMPRODUCT(Aux!${L_D0}$4:${L_DN}$4,{pertenece})",
            # El festivo depende del CALENDARIO de la línea que se hace ese día (hay municipios con
            # festivos propios), así que se suma un término por calendario contra sus líneas.
            "=" + "+".join(
                f"SUMPRODUCT(Aux!${L_D0}${F_CAL[c]}:${L_DN}${F_CAL[c]},"
                f"COUNTIF($A${PF0 + tramo[0]}:$A${PF0 + tramo[-1]},{D}))"
                for c, tramo in tramo_cal.items() if tramo),
            f"=SUMPRODUCT(COUNTIF({D},{T_ID}),{T_JOR})"
            f"+SUMPRODUCT(Aux!$B${aux_de[trab]}:${L_SN}${aux_de[trab]},{R_PESO})"
            f"{descuento.format(D=D)}"
            + (f"{ajuste:+.2f}" if abs(ajuste) > 0.005 else ""),
        ]
        for k, v in enumerate(formulas):
            c = ws.cell(fr, COL_EXTRA + k, v)
            c.alignment, c.border, c.font = centro, borde, negrita
            if k == 3:
                c.number_format = "0.0"

    ws.cell(3, 1, f"Hoja VIVA: los recuentos de la derecha del calendario (sábados, domingos, "
                  f"festivos y horas) son fórmulas que leen del PLAN FUNCIONAL de abajo. Escribe "
                  f"una línea en una celda del calendario y se recalculan solos. Objetivo "
                  f"{datos.config.horas_objetivo} h/año.").alignment = izq

    _ajustar_anchos(ws, desde_fila=HDR, saltar=titulos | {PF_HDR - 1})
    ws.freeze_panes = "C5"                               # fija trabajador/tipo y la cabecera
    wb.save(SALIDA / "calendario.xlsx")


# --------------------------------------------------------------------------- #
#  Resumen por consola
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
#  Salida a partir de un PLAN anual (horizonte rodante): dict {(trab,fecha):turno}
# --------------------------------------------------------------------------- #
def _contribuye(datos: Datos, metrica: str, turno: str, f: date) -> bool:
    """¿La asignación (turno, día) suma a la carga indeseable 'metrica'? (réplica de Modelo)."""
    t = datos.turnos[turno]
    if metrica == "noche":
        return t.tipo == "noche"
    if metrica == "sabado":
        return f.weekday() == 5
    if metrica == "domingo":
        return f.weekday() == 6
    if metrica == "finde":
        return f.weekday() >= 5
    if metrica == "festivo":
        return datos.es_festivo(f, t.municipio)
    if metrica == "24h":
        return t.tipo == "24h"
    if metrica == "12h":
        return t.tipo == "12h"
    if metrica == "partido":
        return t.tipo == "partido"
    return False


def huecos_del_plan(datos: Datos, fechas: list[date], plan: dict) -> list[tuple[date, str]]:
    """Turnos con DEMANDA real sin cubrir del plan: por cada turno operativo de prioridad>=1,
    dem − asignados (una entrada/unidad). Los turnos COMODÍN (prioridad 0, p.ej. REF CAL) NO son
    demanda —son relleno de horas opcional (ver modelo._c1_cobertura)— así que sus plazas sin asignar
    NO son huecos: no ensucian el listado de 'sin cubrir'."""
    cubiertos = defaultdict(int)
    for (w, f), s in plan.items():
        cubiertos[(s, f)] += 1
    huecos = []
    for turno, t in datos.turnos.items():
        if t.prioridad == 0:
            continue                       # comodín: sin demanda -> nunca es hueco
        for f in fechas:
            if not datos.opera(turno, f):
                continue
            faltan = t.dem - cubiertos.get((turno, f), 0)
            huecos += [(f, turno)] * max(0, faltan)
    return sorted(huecos)


def _kpis_plan(datos: Datos, fechas: list[date], plan: dict, huecos: list, estado: str) -> dict:
    # 'huecos' ya viene SOLO con demanda real (prioridad>=1): los comodín REF CAL no son demanda
    # (relleno de horas opcional, ver huecos_del_plan). Así la demanda del KPI es la prioritaria.
    demanda = sum(datos.turnos[t].dem for t in datos.turnos for f in fechas
                  if datos.opera(t, f) and datos.turnos[t].prioridad >= 1)
    n_huecos = len(huecos)
    p1 = sum(peso_cobertura(datos.turnos[s]) for _, s in huecos)
    # Refuerzos de calendario (comodín, prioridad 0) EFECTIVAMENTE asignados: excedente aprovechado
    # para repartir horas. No es cobertura (no tenían demanda) -> se informa como conteo, por turno.
    refuerzos: dict[str, int] = defaultdict(int)
    for (w, f), s in plan.items():
        if datos.turnos[s].prioridad == 0:
            refuerzos[s] += 1
    # P2 anual (aprox.): dispersión de cargas indeseables por trabajador (fijos fuera)
    cargas = {m: defaultdict(int) for m in METRICAS}
    for (w, f), s in plan.items():
        if datos.trabajadores[w].tipo == "fijo":
            continue
        for m in METRICAS:
            if _contribuye(datos, m, s, f):
                cargas[m][w] += 1
    p2 = 0
    for m in METRICAS:
        vals = [cargas[m][w] for w in datos.trabajadores
                if datos.trabajadores[w].tipo != "fijo"]
        if len(vals) >= 2:
            p2 += LAMBDA[m] * (max(vals) - min(vals))
    return {
        "demanda": demanda, "huecos": n_huecos, "cubiertos": demanda - n_huecos,
        "pct": 100 * (demanda - n_huecos) / demanda if demanda else 100,
        "huecos_prio": n_huecos, "demanda_prio": demanda,
        "refuerzos": dict(refuerzos),
        "p1": p1, "p2": p2, "estado": estado,
    }


def _carga_por_trabajador(datos: Datos, plan: dict,
                          fechas: list[date] | None = None) -> dict[str, dict]:
    """Por trabajador: horas COMPUTADAS (legales), horas de JORNADA (la moneda del objetivo anual:
    turno normal = horas_consumo, semana de plaza de localizado = semana entera; ver
    modelo.jornada_minutos) y nº de findes y festivos trabajados. Base del report de equidad.

    `fechas` = horizonte que se cuenta. El plan puede traer días de ANTES del 1 de enero (arranca el
    lunes de esa semana, para que las semanas ISO estén completas) y esos son del año anterior."""
    dentro = set(fechas) if fechas is not None else None
    carga = {w: {"comp": 0.0, "cons": 0.0, "finde": 0, "festivo": 0}
             for w in datos.trabajadores}
    for w, minutos in jornada_minutos(datos, plan, fechas).items():
        carga[w]["cons"] = minutos / 60
    for (w, f), s in plan.items():
        if dentro is not None and f not in dentro:
            continue
        t = datos.turnos[s]
        carga[w]["comp"] += t.horas
        if f.weekday() >= 5:
            carga[w]["finde"] += 1
        if datos.es_festivo(f, t.municipio):
            carga[w]["festivo"] += 1
    return carga


def _resumen(vals: list[float]) -> str:
    """min–max (media ±σ) de una lista; '—' si vacía."""
    import statistics as st
    if not vals:
        return "—"
    sigma = st.pstdev(vals) if len(vals) > 1 else 0.0
    return f"{min(vals):.0f}–{max(vals):.0f} (μ={st.mean(vals):.0f} σ={sigma:.1f})"


def reporte_equidad(datos: Datos, fechas: list[date], plan: dict) -> None:
    """Etapa 6: report de equidad por GRUPO. Para cada grupo de equidad (columna `grupo`; los
    sin grupo caen en '—' = pool global) muestra la dispersión de horas de CONSUMO y del nº de
    findes/festivos entre sus miembros — lo que el modelo intenta igualar. Los fijos van aparte
    (jornada cuadrada por retirada de días; deben rondar 1776 h computadas). Objetivo = 1776 h."""
    carga = _carga_por_trabajador(datos, plan, fechas)
    uvi = _patrones_uvi(datos)

    # -- No-fijos: agrupados por grupo de equidad --------------------------- #
    # Los TITULARES de una rotación de localizado quedan fuera: cubren su plaza el año entero salvo
    # vacaciones y por acuerdo eso ES su jornada, computen sus turnos lo que computen. Compararlos
    # con el resto solo ensuciaría la dispersión del grupo (ver _c9_jornada_anual).
    grupos: dict[str, list[str]] = defaultdict(list)
    for w, t in datos.trabajadores.items():
        if t.tipo == "fijo" or t.patron in uvi:
            continue
        grupos[t.grupo or "—"].append(w)

    print("\n" + "=" * 78)
    print(f"EQUIDAD  (objetivo {datos.config.horas_objetivo} h/año · jornada = ocupación: localizado 24h ×80/7, "
          f"semana de plaza localizada = semana entera)")
    print("=" * 78)
    print(f"{'grupo':<10} {'n':>3}  {'horas jornada':<24} {'findes':<20} {'festivos'}")
    print("-" * 78)
    for gid in sorted(grupos):
        miembros = grupos[gid]
        cons = [carga[w]["cons"] for w in miembros]
        fin = [float(carga[w]["finde"]) for w in miembros]
        fes = [float(carga[w]["festivo"]) for w in miembros]
        print(f"{gid:<10} {len(miembros):>3}  {_resumen(cons):<24} "
              f"{_resumen(fin):<20} {_resumen(fes)}")

    # -- Titulares de localizado: fuera de la equidad, jornada dada por acuerdo -- #
    titulares = sorted(w for w, t in datos.trabajadores.items() if t.patron in uvi)
    if titulares:
        print("-" * 78)
        print(f"Localizado 24h (rotación completa = {datos.config.horas_objetivo} h por acuerdo; "
              f"no entran en la equidad):")
        for w in titulares:
            c = carga[w]
            print(f"  {w:<12} {datos.trabajadores[w].patron:<12} computadas={c['comp']:>6.0f} h   "
                  f"findes={c['finde']:<3} festivos={c['festivo']}")

    # -- Fijos: horas computadas (deben rondar 1776) ------------------------ #
    fijos = [w for w, t in datos.trabajadores.items() if t.tipo == "fijo"]
    if fijos:
        print("-" * 78)
        print(f"Fijos (horas COMPUTADAS, deben rondar {datos.config.horas_objetivo}):")
        for w in sorted(fijos):
            c = carga[w]
            print(f"  {w:<12} {c['comp']:>6.0f} h   findes={c['finde']:<3} festivos={c['festivo']}")

    # -- No-fijos más alejados de 1776 en jornada --------------------------- #
    desv = sorted(((abs(carga[w]["cons"] - datos.config.horas_objetivo), w)
                   for g in grupos.values() for w in g), reverse=True)[:8]
    if desv:
        print("-" * 78)
        print(f"No-fijos más alejados de {datos.config.horas_objetivo} (jornada):")
        for _, w in desv:
            c = carga[w]
            print(f"  {w:<12} {datos.trabajadores[w].tipo:<10} jornada={c['cons']:>6.0f}  "
                  f"computadas={c['comp']:>6.0f}  (Δ{c['cons']-datos.config.horas_objetivo:+.0f})")
    print("=" * 78)


def metricas_trabajadores(datos: Datos, plan: dict,
                          fechas: list[date] | None = None) -> list[dict]:
    """Por trabajador: nº de turnos en SÁBADO, DOMINGO y FESTIVO, y horas de JORNADA totales.
    Las tres cuentas de día son INDEPENDIENTES (un turno en festivo que caiga en sábado/domingo
    suma en las dos columnas que le apliquen). `fechas` acota lo que se cuenta al año que se
    entrega: el plan trae también los días del año anterior con que arranca la primera semana ISO.

    `horas_totales` es la jornada frente al objetivo anual, no la suma de horas legales de los
    turnos: una semana de plaza de LOCALIZADO computa entera (ver modelo.jornada_minutos), porque
    quien la asume se lleva también sus descansos y no hace nada más. Y el TITULAR de una rotación
    de localizado sale directamente al objetivo: cubrir su plaza el año entero salvo vacaciones es,
    por acuerdo, su jornada completa.

    Escribe data/output/metricas_trabajadores.csv (una fila por trabajador) y devuelve las filas
    para el resumen por consola."""
    uvi = _patrones_uvi(datos)
    dentro = set(fechas) if fechas is not None else None
    m = {w: {"sab": 0, "dom": 0, "fes": 0, "horas": 0.0} for w in datos.trabajadores}
    for w, minutos in jornada_minutos(datos, plan, fechas).items():
        m[w]["horas"] = minutos / 60
    for (w, f), s in plan.items():
        if dentro is not None and f not in dentro:
            continue
        t = datos.turnos[s]
        if f.weekday() == 5:
            m[w]["sab"] += 1
        if f.weekday() == 6:
            m[w]["dom"] += 1
        if datos.es_festivo(f, t.municipio):
            m[w]["fes"] += 1

    filas = []
    for w, t in datos.trabajadores.items():
        horas = (datos.config.horas_objetivo * t.factor_jornada) if t.patron in uvi else m[w]["horas"]
        filas.append({
            "id_trab": w, "tipo": t.tipo, "grupo": t.grupo or (t.patron or ""),
            "sabados": m[w]["sab"], "domingos": m[w]["dom"], "festivos": m[w]["fes"],
            "horas_totales": round(horas),
        })
    filas.sort(key=lambda r: (r["tipo"], r["grupo"], r["id_trab"]))

    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / "metricas_trabajadores.csv"
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=["id_trab", "tipo", "grupo", "sabados",
                                            "domingos", "festivos", "horas_totales"])
        wr.writeheader()
        wr.writerows(filas)

    # resumen por consola (tabla completa; una línea por trabajador)
    print("\n" + "=" * 78)
    print("MÉTRICAS POR TRABAJADOR  (sáb / dom / fes independientes; horas = computadas)")
    print("=" * 78)
    print(f"{'id_trab':<12} {'tipo':<11} {'grupo':<16} {'sáb':>4} {'dom':>4} {'fes':>4} {'horas':>6}")
    print("-" * 78)
    for r in filas:
        print(f"{r['id_trab']:<12} {r['tipo']:<11} {r['grupo']:<16} "
              f"{r['sabados']:>4} {r['domingos']:>4} {r['festivos']:>4} {r['horas_totales']:>6}")
    try:
        print(f"\nCSV: {ruta.relative_to(RAIZ)}")
    except ValueError:                      # SALIDA fuera del repo (pruebas): ruta absoluta
        print(f"\nCSV: {ruta}")

    # Aviso de tope: si alguien supera el tope anual duro, el plan NO es válido — casi siempre
    # significa que una ventana del rodante se resolvió sin solución y se cosieron valores basura.
    excedidos = [(r["id_trab"], r["horas_totales"],
                  round(datos.config.horas_objetivo * datos.trabajadores[r["id_trab"]].factor_jornada))
                 for r in filas
                 if r["horas_totales"] > datos.config.horas_objetivo * datos.trabajadores[r["id_trab"]].factor_jornada]
    if excedidos:
        print(f"\n*** AVISO: {len(excedidos)} trabajador(es) SUPERAN el tope anual ***")
        for w, h, tope in sorted(excedidos, key=lambda x: -x[1]):
            print(f"  {w:<12} {h} h  (tope {tope} h, +{h-tope})")
    return filas


def escribir_informe_cobertura(datos: Datos, huecos: list[tuple[date, str]]) -> Path:
    """Un turno sin cubrir por línea, CON SU PRIORIDAD. La prioridad es la columna que decide si un
    hueco importa —una tarde ordinaria y una noche no son lo mismo— y sin ella el informe obliga a
    cruzarlo a mano con turnos.csv para saber cuántos son críticos."""
    ruta = SALIDA / "informe_cobertura.csv"
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fecha", "id_turno", "prioridad", "tipo", "municipio"])
        for f, s in huecos:
            t = datos.turnos[s]
            w.writerow([f"{f:%d/%m/%Y}", s, t.prioridad, t.tipo, t.municipio])
    return ruta


def generar_anual(datos: Datos, plan: dict) -> None:
    """Vuelca a Excel el plan anual del horizonte rodante e imprime el report de equidad."""
    fechas = datos.fechas
    estado = "HORIZONTE RODANTE"
    huecos = huecos_del_plan(datos, fechas, plan)
    escribir_informe_cobertura(datos, huecos)
    kpis = _kpis_plan(datos, fechas, plan, huecos, estado)
    escribir_excel(datos, fechas, plan, huecos, kpis)
    print(f"Estado: {kpis['estado']}")
    print(f"Cobertura PRIORITARIA: {kpis['demanda_prio']-kpis['huecos_prio']}/{kpis['demanda_prio']} "
          f"({kpis['pct']:.1f}%)  | huecos que importan: {kpis['huecos_prio']}  | P2={kpis['p2']}")
    if kpis['refuerzos']:
        total_ref = sum(kpis['refuerzos'].values())
        detalle = ", ".join(f"{s}: {n}" for s, n in sorted(kpis['refuerzos'].items()))
        print(f"Refuerzos de calendario asignados (relleno de horas, sin demanda): {total_ref}  "
              f"({detalle})")
    # Días con más huecos QUE IMPORTAN (los comodín no cuentan)
    por_dia = defaultdict(int)
    for d, s in huecos:
        if datos.turnos[s].prioridad >= 1:
            por_dia[d] += 1
    if por_dia:
        peor = sorted(por_dia.items(), key=lambda kv: -kv[1])[:5]
        print("Días con más huecos prioritarios: " + ", ".join(f"{d:%d/%m}:{n}" for d, n in peor))
    reporte_equidad(datos, fechas, plan)
    metricas_trabajadores(datos, plan, fechas)
    print(f"\nFicheros en {SALIDA.relative_to(RAIZ)}/: calendario.xlsx · "
          f"metricas_trabajadores.csv · informe_cobertura.csv")

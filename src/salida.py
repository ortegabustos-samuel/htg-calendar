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

from cargar_datos import Datos, cargar
from modelo import LAMBDA, METRICAS, Modelo, peso_cobertura, rango_fechas

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
def asignaciones(modelo: Modelo, solver) -> dict[tuple[str, date], str]:
    """(trabajador, fecha) -> id_turno asignado."""
    return {(w, f): s for (w, f, s), var in modelo.x.items() if solver.value(var)}


def sin_cubrir(modelo: Modelo, solver) -> list[tuple[date, str]]:
    """Lista de (fecha, id_turno) no cubiertos (una entrada por unidad de demanda)."""
    huecos = []
    for (turno, f), u in modelo.u.items():
        huecos += [(f, turno)] * solver.value(u)
    return sorted(huecos)

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


def escribir_excel(datos: Datos, fechas: list[date], asign, huecos, kpis: dict) -> None:
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

    # Columnas de recuento a la derecha del calendario
    COL_EXTRA = 3 + len(fechas)
    for k, titulo in enumerate(("Sábados", "Domingos", "Festivos", "Horas")):
        c = ws.cell(HDR, COL_EXTRA + k, titulo)
        c.alignment, c.font, c.border = centro, negrita, borde

    def recuento(trab: str) -> tuple[int, int, int, float]:
        """Sábados, domingos, festivos y horas que acumula el trabajador en todo el horizonte. Las
        tres cuentas de día son INDEPENDIENTES: un turno en festivo que caiga en sábado suma en las
        dos. El festivo se mira con el municipio del turno que hace ese día."""
        sab = dom = fes = 0
        horas = 0.0
        for d in fechas:
            s = asign.get((trab, d))
            if not s:
                continue
            if d.weekday() == 5:
                sab += 1
            if d.weekday() == 6:
                dom += 1
            if datos.es_festivo(d, datos.turnos[s].municipio):
                fes += 1
            horas += datos.turnos[s].horas
        return sab, dom, fes, horas

    def fila(trab: str, t) -> None:
        nonlocal r
        ws.cell(r, 1, trab).alignment = izq
        ws.cell(r, 2, t.patron if t.tipo == "patron" else t.tipo).alignment = izq
        for j, d in enumerate(fechas):
            c = ws.cell(r, 3 + j)
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
        for k, val in enumerate(recuento(trab)):
            c = ws.cell(r, COL_EXTRA + k, round(val) if k == 3 else val)
            c.alignment, c.border, c.font = centro, borde, negrita
        r += 1

    r = HDR + 1
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

    # Turnos sin cubrir: apilados bajo la columna de su día
    por_dia = defaultdict(list)
    for d, s in huecos:
        por_dia[d].append(s)
    r += 1
    ws.cell(r, 1, "SIN CUBRIR").font = negrita
    base = r + 1
    for j, d in enumerate(fechas):
        for k, s in enumerate(por_dia.get(d, [])):
            c = ws.cell(base + k, 3 + j, s)
            c.alignment, c.border = centro, borde
            c.fill = PatternFill("solid", fgColor=FILL_HUECO_XL)

    _ajustar_anchos(ws, desde_fila=HDR, saltar=titulos)
    ws.freeze_panes = "C5"                               # fija trabajador/tipo y la cabecera
    wb.save(SALIDA / "calendario.xlsx")


# --------------------------------------------------------------------------- #
#  Resumen por consola
# --------------------------------------------------------------------------- #
def resumen_consola(datos: Datos, fechas: list[date], huecos, kpis: dict,
                    desviaciones, solver) -> None:
    print(f"Estado: {kpis['estado']}")
    print(f"Cobertura: {kpis['cubiertos']}/{kpis['demanda']} ({kpis['pct']:.1f}%)  "
          f"| sin cubrir: {kpis['huecos']}  | P1={kpis['p1']}  P2={kpis['p2']}")
    print("Equidad (desviación ponderada por métrica): "
          + ", ".join(f"{m}={sum(solver.value(v) for v in vars_desv)}"
                      for m, vars_desv in desviaciones.items()))
    # sin cubrir por día
    por_dia = {}
    for d, _ in huecos:
        por_dia[d] = por_dia.get(d, 0) + 1
    if por_dia:
        peor = sorted(por_dia.items(), key=lambda kv: -kv[1])[:5]
        print("Días con más huecos: " + ", ".join(f"{d:%d/%m}:{n}" for d, n in peor))
    print(f"\nFichero en {SALIDA.relative_to(RAIZ)}/: calendario.xlsx")


# --------------------------------------------------------------------------- #
def generar(modelo: Modelo, solver, estado) -> None:
    datos, fechas = modelo.datos, modelo.fechas
    asign = asignaciones(modelo, solver)
    huecos = sin_cubrir(modelo, solver)

    n_huecos = len(huecos)
    demanda = sum(datos.turnos[t].dem for (t, _) in modelo.u)
    kpis = {
        "demanda": demanda, "huecos": n_huecos, "cubiertos": demanda - n_huecos,
        "pct": 100 * (demanda - n_huecos) / demanda if demanda else 0,
        "p1": sum(peso_cobertura(datos.turnos[t]) * solver.value(u)
                  for (t, _), u in modelo.u.items()),
        "p2": sum(LAMBDA[m] * sum(solver.value(v) for v in vars_desv)
                  for m, vars_desv in modelo.desviaciones.items()),
        "estado": solver.status_name(estado),
    }
    escribir_excel(datos, fechas, asign, huecos, kpis)
    resumen_consola(datos, fechas, huecos, kpis, modelo.desviaciones, solver)


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


def _carga_por_trabajador(datos: Datos, plan: dict) -> dict[str, dict]:
    """Por trabajador: horas COMPUTADAS (legales), horas de CONSUMO (capacidad, localizados ×80/7),
    y nº de findes y festivos trabajados. Base del report de equidad."""
    carga = {w: {"comp": 0.0, "cons": 0.0, "finde": 0, "festivo": 0}
             for w in datos.trabajadores}
    for (w, f), s in plan.items():
        t = datos.turnos[s]
        carga[w]["comp"] += t.horas
        carga[w]["cons"] += t.horas_consumo
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
    from modelo import HORAS_OBJETIVO

    carga = _carga_por_trabajador(datos, plan)

    # -- No-fijos: agrupados por grupo de equidad --------------------------- #
    grupos: dict[str, list[str]] = defaultdict(list)
    for w, t in datos.trabajadores.items():
        if t.tipo == "fijo":
            continue
        grupos[t.grupo or "—"].append(w)

    print("\n" + "=" * 78)
    print(f"EQUIDAD  (objetivo {HORAS_OBJETIVO} h/año · consumo = capacidad, localizado 24h ×80/7)")
    print("=" * 78)
    print(f"{'grupo':<10} {'n':>3}  {'horas consumo':<24} {'findes':<20} {'festivos'}")
    print("-" * 78)
    for gid in sorted(grupos):
        miembros = grupos[gid]
        cons = [carga[w]["cons"] for w in miembros]
        fin = [float(carga[w]["finde"]) for w in miembros]
        fes = [float(carga[w]["festivo"]) for w in miembros]
        print(f"{gid:<10} {len(miembros):>3}  {_resumen(cons):<24} "
              f"{_resumen(fin):<20} {_resumen(fes)}")

    # -- Fijos: horas computadas (deben rondar 1776) ------------------------ #
    fijos = [w for w, t in datos.trabajadores.items() if t.tipo == "fijo"]
    if fijos:
        print("-" * 78)
        print("Fijos (horas COMPUTADAS, deben rondar 1776):")
        for w in sorted(fijos):
            c = carga[w]
            print(f"  {w:<12} {c['comp']:>6.0f} h   findes={c['finde']:<3} festivos={c['festivo']}")

    # -- No-fijos más alejados de 1776 en consumo --------------------------- #
    desv = sorted(((abs(carga[w]["cons"] - HORAS_OBJETIVO), w)
                   for g in grupos.values() for w in g), reverse=True)[:8]
    if desv:
        print("-" * 78)
        print("No-fijos más alejados de 1776 (consumo):")
        for _, w in desv:
            c = carga[w]
            print(f"  {w:<12} {datos.trabajadores[w].tipo:<10} consumo={c['cons']:>6.0f}  "
                  f"computadas={c['comp']:>6.0f}  (Δ{c['cons']-HORAS_OBJETIVO:+.0f})")
    print("=" * 78)


def metricas_trabajadores(datos: Datos, plan: dict) -> list[dict]:
    """Por trabajador: nº de turnos en SÁBADO, DOMINGO y FESTIVO, y horas COMPUTADAS totales.
    Las tres cuentas de día son INDEPENDIENTES (un turno en festivo que caiga en sábado/domingo
    suma en las dos columnas que le apliquen). Escribe data/output/metricas_trabajadores.csv
    (una fila por trabajador) y devuelve las filas para el resumen por consola."""
    m = {w: {"sab": 0, "dom": 0, "fes": 0, "horas": 0.0} for w in datos.trabajadores}
    for (w, f), s in plan.items():
        t = datos.turnos[s]
        if f.weekday() == 5:
            m[w]["sab"] += 1
        if f.weekday() == 6:
            m[w]["dom"] += 1
        if datos.es_festivo(f, t.municipio):
            m[w]["fes"] += 1
        m[w]["horas"] += t.horas

    filas = []
    for w, t in datos.trabajadores.items():
        filas.append({
            "id_trab": w, "tipo": t.tipo, "grupo": t.grupo or (t.patron or ""),
            "sabados": m[w]["sab"], "domingos": m[w]["dom"], "festivos": m[w]["fes"],
            "horas_totales": round(m[w]["horas"]),
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
    from modelo import HORAS_OBJETIVO
    excedidos = [(r["id_trab"], r["horas_totales"],
                  round(HORAS_OBJETIVO * datos.trabajadores[r["id_trab"]].factor_jornada))
                 for r in filas
                 if r["horas_totales"] > HORAS_OBJETIVO * datos.trabajadores[r["id_trab"]].factor_jornada]
    if excedidos:
        print(f"\n*** AVISO: {len(excedidos)} trabajador(es) SUPERAN el tope anual ***")
        for w, h, tope in sorted(excedidos, key=lambda x: -x[1]):
            print(f"  {w:<12} {h} h  (tope {tope} h, +{h-tope})")
    return filas


def generar_anual(datos: Datos, fechas: list[date], plan: dict,
                  estado: str = "HORIZONTE RODANTE") -> None:
    """Vuelca a Excel el plan anual del horizonte rodante e imprime el report de equidad."""
    huecos = huecos_del_plan(datos, fechas, plan)
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
    metricas_trabajadores(datos, plan)
    print(f"\nFichero en {SALIDA.relative_to(RAIZ)}/: calendario.xlsx")


if __name__ == "__main__":
    # Prueba de resolución COMPLETA (sin horizonte rodante ni ventanas): todo el
    # mes se decide en un único modelo, para ver hasta dónde llega el solver en
    # un tiempo razonable. Para producción (año completo) usar resolver_anual.
    datos = cargar("data/input")
    inicio, fin = date(2026, 1, 1), date(2026, 1, 31)
    fechas = rango_fechas(inicio, fin)
    modelo = Modelo(datos, fechas)
    solver, estado = modelo.resolver(trabajadores_cpu=16, log=True)
    generar(modelo, solver, estado)

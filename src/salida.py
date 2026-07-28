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
from collections import defaultdict
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

# Colores por categoría de día para el Excel (cuerpo / cabecera)
CAT_FILL = {"lv": "EAF1FB", "finde": "FFF2CC", "festivo": "FCE4D6"}
CAT_HEAD = {"lv": "BDD7EE", "finde": "FFE699", "festivo": "F4B084"}
FILL_VAC_XL, FILL_HUECO_XL = "D9D9D9", "F4B084"


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
def _categoria(datos: Datos, d: date) -> str:
    """Categoría de día para colorear la columna: festivo (Común), finde o L-V.
    (Los festivos regionales no tiñen la columna; solo los comunes, que afectan a todos.)"""
    if d in datos.festivos.get("Comun", set()):
        return "festivo"
    return "finde" if d.weekday() >= 5 else "lv"


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
        c.fill = PatternFill("solid", fgColor=CAT_HEAD[_categoria(datos, d)])

    def orden(kv):
        _, t = kv
        return (t.tipo, t.patron or "", _)

    r = HDR + 1
    for trab, t in sorted(datos.trabajadores.items(), key=orden):
        ws.cell(r, 1, trab).alignment = izq
        ws.cell(r, 2, t.patron if t.tipo == "patron" else t.tipo).alignment = izq
        for j, d in enumerate(fechas):
            c = ws.cell(r, 3 + j)
            c.alignment, c.border = centro, borde
            if not datos.disponible(trab, d):
                c.value, color = "VAC", FILL_VAC_XL
            else:
                c.value, color = asign.get((trab, d), ""), CAT_FILL[_categoria(datos, d)]
            c.fill = PatternFill("solid", fgColor=color)
        r += 1

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

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 12
    for j in range(len(fechas)):
        ws.column_dimensions[get_column_letter(3 + j)].width = 8
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
    from modelo import HMAX_AÑO
    excedidos = [(r["id_trab"], r["horas_totales"],
                  round(HMAX_AÑO * datos.trabajadores[r["id_trab"]].factor_jornada))
                 for r in filas
                 if r["horas_totales"] > HMAX_AÑO * datos.trabajadores[r["id_trab"]].factor_jornada]
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

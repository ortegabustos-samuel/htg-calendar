#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
salida.py — Visualización y volcado del cuadrante resuelto.

A partir de un modelo resuelto genera en data/output/calendario.xlsx: rejilla
trabajadores × días (coloreada por tipo), con vacaciones/libres, KPIs y turnos
sin cubrir. También imprime un resumen por consola.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from cargar_datos import Datos, cargar
from modelo import LAMBDA, METRICAS, PESO_COBERTURA, Modelo, rango_fechas

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
        "p1": sum(PESO_COBERTURA * datos.turnos[t].prioridad * solver.value(u)
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
    """Turnos sin cubrir del plan: por cada turno operativo, dem − asignados (una entrada/unidad)."""
    cubiertos = defaultdict(int)
    for (w, f), s in plan.items():
        cubiertos[(s, f)] += 1
    huecos = []
    for turno, t in datos.turnos.items():
        for f in fechas:
            if not datos.opera(turno, f):
                continue
            faltan = t.dem - cubiertos.get((turno, f), 0)
            huecos += [(f, turno)] * max(0, faltan)
    return sorted(huecos)


def _kpis_plan(datos: Datos, fechas: list[date], plan: dict, huecos: list, estado: str) -> dict:
    demanda = sum(datos.turnos[t].dem for t in datos.turnos for f in fechas if datos.opera(t, f))
    n_huecos = len(huecos)
    p1 = sum(PESO_COBERTURA * datos.turnos[s].prioridad for _, s in huecos)
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
        "pct": 100 * (demanda - n_huecos) / demanda if demanda else 0,
        "p1": p1, "p2": p2, "estado": estado,
    }


def generar_anual(datos: Datos, fechas: list[date], plan: dict,
                  estado: str = "HORIZONTE RODANTE") -> None:
    """Vuelca a Excel el plan anual del horizonte rodante."""
    huecos = huecos_del_plan(datos, fechas, plan)
    kpis = _kpis_plan(datos, fechas, plan, huecos, estado)
    escribir_excel(datos, fechas, plan, huecos, kpis)
    print(f"Estado: {kpis['estado']}")
    print(f"Cobertura: {kpis['cubiertos']}/{kpis['demanda']} ({kpis['pct']:.1f}%)  "
          f"| sin cubrir: {kpis['huecos']}  | P1={kpis['p1']}  P2={kpis['p2']}")
    por_dia = defaultdict(int)
    for d, _ in huecos:
        por_dia[d] += 1
    if por_dia:
        peor = sorted(por_dia.items(), key=lambda kv: -kv[1])[:5]
        print("Días con más huecos: " + ", ".join(f"{d:%d/%m}:{n}" for d, n in peor))
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
salida.py — Visualización y volcado del cuadrante resuelto.

A partir de un modelo resuelto genera en data/output/:
  * calendario.html          — rejilla visual trabajadores × días (coloreada por tipo),
                               con vacaciones/libres, KPIs y turnos sin cubrir.
  * calendario.csv           — asignación por trabajador y día.
  * informe_cobertura.csv    — turnos sin cubrir (día, turno, tipo, municipio).
También imprime un resumen por consola.
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
from modelo import LAMBDA, PESO, Modelo, cp_model, rango_fechas

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "data" / "output"

DIA_INI = ["L", "M", "X", "J", "V", "S", "D"]
CODIGO = {"mañana": "M", "tarde": "T", "noche": "N", "24h": "G", "partido": "P"}
COLOR = {"mañana": "#fff3b0", "tarde": "#ffd6a5", "noche": "#a0c4ff",
         "24h": "#ffadad", "partido": "#caffbf"}
COLOR_LIBRE, COLOR_VAC = "#ffffff", "#e0e0e0"

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
#  Volcado CSV
# --------------------------------------------------------------------------- #
def escribir_csv(datos: Datos, fechas: list[date], asign, huecos) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)

    with open(SALIDA / "calendario.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id_trab", "tipo"] + [d.strftime("%d/%m") for d in fechas])
        for trab, t in datos.trabajadores.items():
            fila = []
            for d in fechas:
                if not datos.disponible(trab, d):
                    fila.append("VAC")
                else:
                    fila.append(asign.get((trab, d), "LIBRE"))
            w.writerow([trab, t.tipo] + fila)

    with open(SALIDA / "informe_cobertura.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "id_turno", "tipo", "municipio"])
        for d, turno in huecos:
            t = datos.turnos[turno]
            w.writerow([d.strftime("%d/%m/%Y"), turno, t.tipo, t.municipio])


# --------------------------------------------------------------------------- #
#  Visualización HTML
# --------------------------------------------------------------------------- #
def _celda(datos: Datos, trab: str, d: date, asign) -> str:
    if not datos.disponible(trab, d):
        return f'<td style="background:{COLOR_VAC}" title="vacaciones">VAC</td>'
    turno = asign.get((trab, d))
    if turno is None:
        return f'<td style="background:{COLOR_LIBRE};color:#ccc">·</td>'
    t = datos.turnos[turno]
    return (f'<td style="background:{COLOR[t.tipo]}" title="{turno} · {t.tipo} · {t.municipio}">'
            f'{turno}</td>')


def escribir_html(datos: Datos, fechas: list[date], asign, huecos, kpis: dict) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    filas = []

    # Cabecera de días
    cab = "".join(f'<th>{d.day}<br><small>{DIA_INI[d.weekday()]}</small></th>' for d in fechas)
    filas.append(f"<tr><th>Trabajador</th><th>Tipo</th>{cab}</tr>")

    # Una fila por trabajador (ordenados por tipo y patrón)
    def orden(kv):
        _, t = kv
        return (t.tipo, t.patron or "", _)
    for trab, t in sorted(datos.trabajadores.items(), key=orden):
        celdas = "".join(_celda(datos, trab, d, asign) for d in fechas)
        etiqueta = t.patron if t.tipo == "patron" else t.tipo
        filas.append(f'<tr><th class="w">{trab}</th><td class="tp">{etiqueta}</td>{celdas}</tr>')

    # Nº de turnos sin cubrir por día (fila resumen)
    por_dia = {d: 0 for d in fechas}
    for d, _ in huecos:
        por_dia[d] += 1
    resumen = "".join(f'<td class="{"h" if por_dia[d] else ""}">{por_dia[d] or ""}</td>'
                      for d in fechas)
    filas.append(f'<tr><th class="w">SIN CUBRIR</th><td></td>{resumen}</tr>')

    # Tabla de turnos sin cubrir
    filas_huecos = "".join(
        f"<tr><td>{d:%d/%m}</td><td>{s}</td><td>{datos.turnos[s].tipo}</td>"
        f"<td>{datos.turnos[s].municipio}</td></tr>" for d, s in huecos)

    leyenda = " ".join(
        f'<span style="background:{c};padding:2px 8px;border:1px solid #999">{k}</span>'
        for k, c in COLOR.items())

    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Cuadrante {fechas[0]:%d/%m/%Y} – {fechas[-1]:%d/%m/%Y}</title>
<style>
 body{{font-family:sans-serif;font-size:12px;margin:16px}}
 h1{{font-size:18px}}
 table{{border-collapse:collapse;margin-top:10px}}
 td,th{{border:1px solid #ccc;padding:2px 4px;text-align:center;white-space:nowrap}}
 th.w{{position:sticky;left:0;background:#f4f4f4;text-align:left}}
 td.tp{{color:#666;font-size:10px}}
 td.h{{background:#ff6b6b;color:#fff;font-weight:bold}}
 .kpi{{display:inline-block;background:#f4f4f4;border:1px solid #ddd;padding:6px 12px;margin:4px}}
 .cal{{overflow:auto;max-height:80vh;border:1px solid #ddd}}
</style></head><body>
<h1>Cuadrante {fechas[0]:%d/%m/%Y} – {fechas[-1]:%d/%m/%Y}</h1>
<div>
 <span class="kpi">Cobertura: <b>{kpis['cubiertos']}/{kpis['demanda']}</b> ({kpis['pct']:.1f}%)</span>
 <span class="kpi">Sin cubrir: <b>{kpis['huecos']}</b></span>
 <span class="kpi">P1 coste: <b>{kpis['p1']}</b></span>
 <span class="kpi">P2 equidad: <b>{kpis['p2']}</b></span>
 <span class="kpi">Estado: <b>{kpis['estado']}</b></span>
</div>
<p>Leyenda: {leyenda}
 <span style="background:{COLOR_VAC};padding:2px 8px;border:1px solid #999">VAC</span>
 <span style="border:1px solid #999;padding:2px 8px">· libre</span></p>
<div class="cal"><table>{''.join(filas)}</table></div>
<h2>Turnos sin cubrir ({len(huecos)})</h2>
<table><tr><th>Fecha</th><th>Turno</th><th>Tipo</th><th>Municipio</th></tr>{filas_huecos}</table>
</body></html>"""
    (SALIDA / "calendario.html").write_text(html, encoding="utf-8")


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
                    rangos, solver) -> None:
    print(f"Estado: {kpis['estado']}")
    print(f"Cobertura: {kpis['cubiertos']}/{kpis['demanda']} ({kpis['pct']:.1f}%)  "
          f"| sin cubrir: {kpis['huecos']}  | P1={kpis['p1']}  P2={kpis['p2']}")
    print("Equidad (rango max-min por métrica): "
          + ", ".join(f"{m}={solver.value(r)}" for m, r in rangos.items()))
    # sin cubrir por día
    por_dia = {}
    for d, _ in huecos:
        por_dia[d] = por_dia.get(d, 0) + 1
    if por_dia:
        peor = sorted(por_dia.items(), key=lambda kv: -kv[1])[:5]
        print("Días con más huecos: " + ", ".join(f"{d:%d/%m}:{n}" for d, n in peor))
    print(f"\nFicheros en {SALIDA.relative_to(RAIZ)}/: calendario.xlsx, calendario.html, "
          f"calendario.csv, informe_cobertura.csv")


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
        "p1": sum(PESO[datos.turnos[t].tipo] * solver.value(u) for (t, _), u in modelo.u.items()),
        "p2": sum(LAMBDA[m] * solver.value(r) for m, r in modelo.rangos.items()),
        "estado": solver.status_name(estado),
    }
    escribir_csv(datos, fechas, asign, huecos)
    escribir_html(datos, fechas, asign, huecos, kpis)
    escribir_excel(datos, fechas, asign, huecos, kpis)
    resumen_consola(datos, fechas, huecos, kpis, modelo.rangos, solver)


if __name__ == "__main__":
    datos = cargar("data/input")
    fechas = rango_fechas(date(2026, 1, 1), date(2026, 1, 31))
    modelo = Modelo(datos, fechas)
    solver, estado = modelo.resolver(segundos=30)
    if estado in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        generar(modelo, solver, estado)
    else:
        print("Sin solución:", solver.status_name(estado))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
solver_cpsat.py — Esqueleto del modelo de cuadrantes en OR-Tools CP-SAT.

Implementa las restricciones del MODELO.md sobre una instancia pequeña y REPRESENTATIVA
(no son datos reales). Demuestra que las codificaciones resuelven y cómo se encadenan:
cobertura con holgura, descanso de 12 h, días consecutivos, 48 h/7 días, descanso tras
24 h, fijos congelados, equidad min-max, warm-start con el patrón y objetivo lexicográfico.

Sustituye la sección DATOS por tus 73 líneas, tus trabajadores y tu matriz de patrón.
"""
from datetime import date, timedelta
from ortools.sat.python import cp_model

RMIN, HMAX7, CMAX, RHO_24 = 12, 48, 6, 3      # parámetros legales (a fijar con convenio)
DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]

# ----------------------------------------------------------------------------- #
#  DATOS (instancia ilustrativa; reemplazar por los reales)
# ----------------------------------------------------------------------------- #
# turno: (entrada, salida, mun, req_cualif, a_lv, a_sab, a_dom, a_fest, tipo)
TURNOS = {
    "M_VLL": (7,  15, "Valladolid", None,  1, 0, 0, 0, "manana"),
    "T_VLL": (15, 23, "Valladolid", None,  1, 1, 0, 0, "tarde"),
    "N_VLL": (23, 31, "Valladolid", None,  1, 1, 1, 1, "noche"),   # 23->07 (+24)
    "P_VLL": (9,  21, "Valladolid", None,  1, 0, 0, 0, "partido"),
    "G24":   (8,  32, "Valladolid", "24h", 0, 1, 1, 1, "24h"),     # 24 h
    "M_MED": (7,  15, "Medina",     None,  1, 0, 0, 0, "manana"),
}
# trabajador: (tipo, mun_ok, cualifs, fila_patron, turno_fijo)
TRAB = {
    "P1": ("patron", {"Valladolid"}, set(),       0, None),
    "P2": ("patron", {"Valladolid"}, set(),       1, None),
    "P3": ("patron", {"Valladolid"}, {"24h"},     2, None),
    "P4": ("patron", {"Valladolid"}, set(),       3, None),
    "F1": ("fijo",   {"Medina"},     set(),    None, "M_MED"),
    "C1": ("corre",  {"Valladolid"}, {"24h"},  None, None),
}
# matriz base del patrón B[fila][dia] -> línea o "LIBRE"
B = {
    0: {"lun":"M_VLL","mar":"M_VLL","mie":"M_VLL","jue":"M_VLL","vie":"M_VLL","sab":"LIBRE","dom":"LIBRE"},
    1: {"lun":"T_VLL","mar":"T_VLL","mie":"T_VLL","jue":"T_VLL","vie":"T_VLL","sab":"T_VLL","dom":"LIBRE"},
    2: {"lun":"N_VLL","mar":"N_VLL","mie":"N_VLL","jue":"N_VLL","vie":"N_VLL","sab":"N_VLL","dom":"N_VLL"},
    3: {"lun":"P_VLL","mar":"P_VLL","mie":"P_VLL","jue":"P_VLL","vie":"P_VLL","sab":"LIBRE","dom":"LIBRE"},
}
T_PAT = len(B)
FESTIVOS = {(date(2026, 1, 6), "Valladolid")}          # Reyes (ámbito Común/Valladolid)
VACACIONES = {("P3", date(2026, 1, 12), date(2026, 1, 18))}  # P3 (el de 24h) fuera 1 semana
FECHA_INI, N_SEM = date(2026, 1, 5), 3                  # lunes; 3 semanas

# ----------------------------------------------------------------------------- #
#  Derivaciones
# ----------------------------------------------------------------------------- #
def dur(s):  return TURNOS[s][1] - TURNOS[s][0]
def noct(s):
    e, o = TURNOS[s][0], TURNOS[s][1]
    tot = 0.0
    for a, b in [(e, o)]:
        for na, nb in [(0,6),(22,30),(46,48)]:   # 22-06 replicado por cruce de medianoche
            tot += max(0, min(b, nb) - max(a, na))
    return tot
def es_finde(d): return d.weekday() >= 5
def es_fest(d, z): return (d, z) in FESTIVOS or (d, "Común") in FESTIVOS
def opera(s, d):
    e,o,mun,req,lv,sab,dom,fes,tipo = TURNOS[s]
    if es_fest(d, mun): return fes == 1
    wd = d.weekday()
    return (lv if wd < 5 else sab if wd == 5 else dom) == 1
def canDo(w, s):
    tipo, munok, cual, _, _ = TRAB[w]
    req = TURNOS[s][3]
    return TURNOS[s][2] in munok and (req is None or req in cual)
def avail(w, d):
    for ww, ini, fin in VACACIONES:
        if ww == w and ini <= d <= fin: return False
    return True

DIAS_H = [FECHA_INI + timedelta(days=i) for i in range(7 * N_SEM)]
def semana_idx(d): return (d - FECHA_INI).days // 7
def pi(w, d):       # esqueleto del patrón
    if TRAB[w][0] != "patron": return None
    fila = (TRAB[w][3] + semana_idx(d)) % T_PAT
    return B[fila][DIAS[d.weekday()]]

# pares de transición prohibidos por descanso < RMIN (fin de s el día d -> inicio s' en d+1)
INCOMP = set()
for s in TURNOS:
    for s2 in TURNOS:
        gap = (24 + TURNOS[s2][0]) - TURNOS[s][1]
        if gap < RMIN: INCOMP.add((s, s2))

# ----------------------------------------------------------------------------- #
#  Modelo
# ----------------------------------------------------------------------------- #
m = cp_model.CpModel()
x = {}
for w in TRAB:
    for d in DIAS_H:
        if not avail(w, d): continue
        for s in TURNOS:
            if canDo(w, s) and opera(s, d):
                x[(w, d, s)] = m.NewBoolVar(f"x_{w}_{d}_{s}")

def work(w, d):  return [x[(w, d, s)] for s in TURNOS if (w, d, s) in x]

# C1 cobertura con holgura
slack = {}
for s in TURNOS:
    for d in DIAS_H:
        if opera(s, d):
            u = m.NewIntVar(0, 1, f"u_{s}_{d}"); slack[(s, d)] = u
            m.Add(sum(x[(w, d, s)] for w in TRAB if (w, d, s) in x) + u == 1)

# C2 un turno por día
for w in TRAB:
    for d in DIAS_H:
        if work(w, d): m.AddAtMostOne(work(w, d))

# C4 descanso 12 h (transiciones prohibidas)
for w in TRAB:
    for i in range(len(DIAS_H) - 1):
        d, d2 = DIAS_H[i], DIAS_H[i + 1]
        for s in TURNOS:
            for s2 in TURNOS:
                if (s, s2) in INCOMP and (w, d, s) in x and (w, d2, s2) in x:
                    m.Add(x[(w, d, s)] + x[(w, d2, s2)] <= 1)

# C5 días consecutivos
for w in TRAB:
    for i in range(len(DIAS_H) - CMAX):
        vs = [v for j in range(CMAX + 1) for v in work(w, DIAS_H[i + j])]
        if vs: m.Add(sum(vs) <= CMAX)

# C6 48 h / 7 días
for w in TRAB:
    for i in range(len(DIAS_H) - 6):
        vs = [int(dur(s)) * x[(w, DIAS_H[i + j], s)]
              for j in range(7) for s in TURNOS if (w, DIAS_H[i + j], s) in x]
        if vs: m.Add(sum(vs) <= HMAX7)

# C7 tras 24 h, RHO_24 días de descanso
for w in TRAB:
    for i, d in enumerate(DIAS_H):
        if (w, d, "G24") in x:
            futuros = [v for j in range(1, RHO_24 + 1) if i + j < len(DIAS_H)
                       for v in work(w, DIAS_H[i + j])]
            if futuros: m.Add(RHO_24 * x[(w, d, "G24")] + sum(futuros) <= RHO_24)

# C8 fijos congelados
for w in TRAB:
    if TRAB[w][0] == "fijo":
        f = TRAB[w][4]
        for d in DIAS_H:
            if (w, d, f) in x: m.Add(x[(w, d, f)] == 1)

# Equidad: cargas por trabajador y rango min-max
METRICAS = {
    "noche":   lambda w, d, s: 1 if TURNOS[s][8] == "noche" else 0,
    "finde":   lambda w, d, s: 1 if es_finde(d) else 0,
    "festivo": lambda w, d, s: 1 if es_fest(d, TURNOS[s][2]) else 0,
    "24h":     lambda w, d, s: 1 if TURNOS[s][8] == "24h" else 0,
    "partido": lambda w, d, s: 1 if TURNOS[s][8] == "partido" else 0,
}
rangos = []
load = {}
for mt, coef in METRICAS.items():
    loads_w = []
    for w in TRAB:
        lw = m.NewIntVar(0, len(DIAS_H), f"load_{mt}_{w}")
        m.Add(lw == sum(coef(w, d, s) * x[(w, d, s)]
                        for d in DIAS_H for s in TURNOS if (w, d, s) in x))
        load[(mt, w)] = lw; loads_w.append(lw)
    hi = m.NewIntVar(0, len(DIAS_H), f"max_{mt}"); lo = m.NewIntVar(0, len(DIAS_H), f"min_{mt}")
    m.AddMaxEquality(hi, loads_w); m.AddMinEquality(lo, loads_w)
    rng = m.NewIntVar(0, len(DIAS_H), f"rng_{mt}"); m.Add(rng == hi - lo)
    rangos.append(rng)

# Warm start: pega la búsqueda al patrón
for (w, d, s), v in x.items():
    if pi(w, d) == s: m.AddHint(v, 1)

# ----------------------------------------------------------------------------- #
#  Resolución lexicográfica:  P1 cobertura  ->  P2 equidad
# ----------------------------------------------------------------------------- #
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 10
solver.parameters.num_search_workers = 8

total_slack = sum(slack.values())
m.Minimize(total_slack)
solver.Solve(m)
best_cov = int(solver.ObjectiveValue())
m.Add(total_slack <= best_cov)                 # fija P1

m.Minimize(sum(rangos))                          # P2 equidad
st = solver.Solve(m)

# ----------------------------------------------------------------------------- #
#  Salida
# ----------------------------------------------------------------------------- #
print("Estado:", solver.StatusName(st))
print(f"P1 huecos sin cubrir = {best_cov}   |   P2 suma de rangos = {int(solver.ObjectiveValue())}\n")
print("Cuadrante (· = libre):")
hdr = "      " + " ".join(d.strftime("%d") for d in DIAS_H)
print(hdr)
for w in TRAB:
    fila = []
    for d in DIAS_H:
        cell = "·"
        if not avail(w, d): cell = "V"
        else:
            for s in TURNOS:
                if (w, d, s) in x and solver.Value(x[(w, d, s)]): cell = s.split("_")[0][:2]
        fila.append(f"{cell:>2}")
    print(f"{w:>4}  " + " ".join(fila))
print("\nCargas por trabajador (equidad):")
print("      " + "  ".join(f"{mt:>7}" for mt in METRICAS))
for w in TRAB:
    print(f"{w:>4}  " + "  ".join(f"{solver.Value(load[(mt, w)]):>7}" for mt in METRICAS))
huecos = [(s, d) for (s, d), u in slack.items() if solver.Value(u)]
if huecos:
    print("\nSin cubrir (la capa de reparación lo reportaría a un humano):")
    for s, d in huecos: print(f"  {d}  {s}")

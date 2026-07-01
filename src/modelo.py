#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modelo.py — Construcción del modelo CP-SAT de cuadrantes.

Sobre una ventana de fechas construye variables x[w,d,s] (solo elegibles) y las
restricciones duras, con objetivo P1 (cobertura ponderada por criticidad):

  C1 cobertura con holgura · C2 un turno/día · C4 descanso 12 h ·
  C5 días consecutivos · C6 48 h/7 días · C6b 160 h/4 semanas ·
  C7 descanso semanal (2 días consecutivos + 1 sáb-dom cada 4 semanas).

Parámetros legales tomados del V Convenio de Transporte Sanitario de CyL
(ver doc/restricciones_convenio.md).

Pendiente para siguientes iteraciones: equidad (P2), preferencia por refuerzo V,
fijos/patrón congelados, jornada anual (1776 h, sobre el año completo), lexicográfico
y horizonte rodante.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from cargar_datos import Datos, cargar, hora_a_float
from ortools.sat.python import cp_model

# Parámetros legales (V Convenio CyL)
RMIN = 12          # descanso mínimo entre jornadas (h)          — art. 23, 31
HMAX7 = 48         # máx. trabajo efectivo en 7 días (h)         — art. 23.2
HMAX_4SEM = 160    # máx. trabajo efectivo en 4 semanas (h)      — art. 23 A
HMAX_AÑO = 1776    # jornada anual efectiva (h) — se aplica en el año completo, no por ventana
CMAX = 6           # máx. días consecutivos trabajados

# Criticidad de dejar sin cubrir cada tipo (a mayor peso, más se evita el hueco)
PESO = {"24h": 8, "noche": 4, "tarde": 2, "partido": 2, "mañana": 1}

# Cargas indeseables cuya dispersión se reparte (equidad, P2) y su importancia relativa
METRICAS = ("noche", "finde", "festivo", "24h", "partido")
LAMBDA = {"noche": 3, "finde": 2, "festivo": 3, "24h": 4, "partido": 1}

# Lexicográfico por objetivo único: minimizar  BIG_P1 * P1 + P2.  Como P2 = Σ λ·rango está
# acotado (rango ≤ nº días ~31, Σλ ≈ 13 -> P2 ≲ 400), con BIG_P1 mayor que ese máximo P1
# domina siempre y P2 solo desempata. Una sola resolución, sin segunda fase "en frío".
BIG_P1 = 1000


def rango_fechas(inicio: date, fin: date) -> list[date]:
    """Lista de fechas [inicio, fin] inclusive."""
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


def semana(f: date) -> tuple[int, int]:
    """Clave (año, nº) de la semana ISO (lunes-domingo) a la que pertenece la fecha."""
    a, n, _ = f.isocalendar()
    return (a, n)


class Modelo:
    def __init__(self, datos: Datos, fechas: list[date]):
        self.datos = datos
        self.fechas = fechas
        self.m = cp_model.CpModel()

        self.x: dict[tuple[str, date, str], cp_model.IntVar] = {}
        self.refuerzo: set[tuple[str, date, str]] = set()
        self.turnos_wd: dict[tuple[str, date], list[str]] = defaultdict(list)
        self.trabaja: dict[tuple[str, date], cp_model.IntVar] = {}   # ¿w trabaja el día d?
        self.u: dict[tuple[str, date], cp_model.IntVar] = {}         # holgura de cobertura

        self._crear_variables()
        self._c1_cobertura()
        self._c2_un_turno_dia()
        self._c4_descanso()
        self._c5_dias_consecutivos()
        self._c6_horas_semana()
        self._c6b_horas_cuatrisemana()
        self._c7_descanso_semanal()

        self.coste_p1 = self._coste_cobertura()   # P1
        self.rangos = self._equidad()             # rango por métrica
        self.coste_p2 = sum(LAMBDA[m] * r for m, r in self.rangos.items())  # P2

    # -- Variables ----------------------------------------------------------- #
    def _crear_variables(self) -> None:
        d = self.datos
        for (trab, turno) in d.capacidades:
            for f in self.fechas:
                elegible, es_refuerzo = d.elegible(trab, turno, f)
                if not elegible:
                    continue
                self.x[(trab, f, turno)] = self.m.new_bool_var(f"x_{trab}_{f:%Y%m%d}_{turno}")
                self.turnos_wd[(trab, f)].append(turno)
                if es_refuerzo:
                    self.refuerzo.add((trab, f, turno))

    def _turnos(self, trab: str, f: date) -> list[cp_model.IntVar]:
        return [self.x[(trab, f, s)] for s in self.turnos_wd.get((trab, f), [])]

    def _libre(self, trab: str, f: date):
        """Expresión 1 - trabaja: vale 1 si el trabajador descansa ese día."""
        return 1 - self.trabaja[(trab, f)]

    # -- Restricciones duras ------------------------------------------------- #
    def _c1_cobertura(self) -> None:
        """Cada turno operativo se cubre con su demanda; la holgura u recoge lo no cubierto."""
        d = self.datos
        for turno, t in d.turnos.items():
            for f in self.fechas:
                if not d.opera(turno, f):
                    continue
                asignados = [self.x[(w, f, turno)] for w in d.trabajadores
                             if (w, f, turno) in self.x]
                holgura = self.m.new_int_var(0, t.dem, f"u_{turno}_{f:%Y%m%d}")
                self.u[(turno, f)] = holgura
                self.m.add(sum(asignados) + holgura == t.dem)

    def _c2_un_turno_dia(self) -> None:
        """trabaja[w,d] = Σ_s x (a lo sumo un turno al día)."""
        for trab in self.datos.trabajadores:
            for f in self.fechas:
                var = self.m.new_bool_var(f"trab_{trab}_{f:%Y%m%d}")
                self.trabaja[(trab, f)] = var
                self.m.add(var == sum(self._turnos(trab, f)))   # BoolVar == Σx obliga Σx ≤ 1

    def _c4_descanso(self) -> None:
        """Descanso >= 12 h entre el turno de un día y el del siguiente."""
        incompatibles = self._pares_incompatibles()
        for trab in self.datos.trabajadores:
            for hoy, manana in zip(self.fechas, self.fechas[1:]):
                for s1 in self.turnos_wd.get((trab, hoy), []):
                    for s2 in self.turnos_wd.get((trab, manana), []):
                        if (s1, s2) in incompatibles:
                            self.m.add(self.x[(trab, hoy, s1)] + self.x[(trab, manana, s2)] <= 1)

    def _pares_incompatibles(self) -> set[tuple[str, str]]:
        """(s1, s2) no encadenables en días consecutivos por dejar menos de 12 h de descanso.
        (El día->noche con >=12 h de por medio NO se prohíbe; art. 30 a confirmar con la empresa.)"""
        d = self.datos
        incompatibles = set()
        for s1, t1 in d.turnos.items():
            fin_s1 = hora_a_float(t1.hora_entrada) + t1.dur
            for s2, t2 in d.turnos.items():
                descanso = (24 + hora_a_float(t2.hora_entrada)) - fin_s1
                if descanso < RMIN:
                    incompatibles.add((s1, s2))
        return incompatibles

    def _c5_dias_consecutivos(self) -> None:
        """Como mucho CMAX días trabajados seguidos."""
        for trab in self.datos.trabajadores:
            for i in range(len(self.fechas) - CMAX):
                ventana = self.fechas[i:i + CMAX + 1]
                self.m.add(sum(self.trabaja[(trab, f)] for f in ventana) <= CMAX)

    def _minutos(self, trab: str, dias: list[date]) -> list:
        """Términos horas(s)*x (en minutos efectivos computables) del trabajador en esos días."""
        return [round(self.datos.turnos[s].horas * 60) * self.x[(trab, f, s)]
                for f in dias for s in self.turnos_wd.get((trab, f), [])]

    def _c6_horas_semana(self) -> None:
        """<= 48 h de trabajo efectivo en cualquier ventana de 7 días."""
        for trab in self.datos.trabajadores:
            for i in range(len(self.fechas) - 6):
                minutos = self._minutos(trab, self.fechas[i:i + 7])
                if minutos:
                    self.m.add(sum(minutos) <= HMAX7 * 60)

    def _c6b_horas_cuatrisemana(self) -> None:
        """<= 160 h de trabajo efectivo en cualquier ventana de 28 días."""
        for trab in self.datos.trabajadores:
            for i in range(len(self.fechas) - 27):
                minutos = self._minutos(trab, self.fechas[i:i + 28])
                if minutos:
                    self.m.add(sum(minutos) <= HMAX_4SEM * 60)

    def _c7_descanso_semanal(self) -> None:
        """Descanso semanal (art. 24): (a) 2 días de descanso CONSECUTIVOS por semana;
        (b) al menos un sábado+domingo libres en cada ventana de 4 semanas."""
        sabados = {semana(f): f for f in self.fechas if f.weekday() == 5}
        domingos = {semana(f): f for f in self.fechas if f.weekday() == 6}
        semanas = sorted({semana(f) for f in self.fechas})

        for trab in self.datos.trabajadores:
            # (a) al menos un par de días consecutivos ambos libres, por semana
            pares_por_semana: dict[tuple[int, int], list] = defaultdict(list)
            for hoy, manana in zip(self.fechas, self.fechas[1:]):
                par = self.m.new_bool_var(f"descanso2_{trab}_{hoy:%Y%m%d}")
                self.m.add(par <= self._libre(trab, hoy))
                self.m.add(par <= self._libre(trab, manana))
                pares_por_semana[semana(hoy)].append(par)
            for pares in pares_por_semana.values():
                self.m.add(sum(pares) >= 1)

            # (b) sábado+domingo libres, al menos una vez cada 4 semanas
            finde_libre = {}
            for sem in semanas:
                if sem in sabados and sem in domingos:
                    fl = self.m.new_bool_var(f"findelibre_{trab}_{sem[0]}w{sem[1]}")
                    self.m.add(fl <= self._libre(trab, sabados[sem]))
                    self.m.add(fl <= self._libre(trab, domingos[sem]))
                    finde_libre[sem] = fl
            findes = [finde_libre[s] for s in semanas if s in finde_libre]
            for i in range(len(findes) - 3):
                self.m.add(sum(findes[i:i + 4]) >= 1)

    # -- Objetivos ----------------------------------------------------------- #
    def _coste_cobertura(self):
        """P1: turnos no cubiertos ponderados por criticidad."""
        return sum(PESO[self.datos.turnos[turno].tipo] * holgura
                   for (turno, _), holgura in self.u.items())

    def _contribuye(self, metrica: str, turno: str, f: date) -> bool:
        """¿La asignación (turno, día) suma a la carga indeseable 'metrica'?"""
        t = self.datos.turnos[turno]
        if metrica == "noche":
            return t.tipo == "noche"
        if metrica == "finde":
            return f.weekday() >= 5
        if metrica == "festivo":
            return self.datos.es_festivo(f, t.municipio)
        if metrica == "24h":
            return t.tipo == "24h"
        if metrica == "partido":
            return t.tipo == "partido"
        return False

    def _equidad(self) -> dict[str, cp_model.IntVar]:
        """Para cada métrica: carga por trabajador (fijos fuera) y su rango max-min, medido
        solo entre quienes pueden soportar esa carga."""
        pool = [w for w, t in self.datos.trabajadores.items() if t.tipo != "fijo"]
        n = len(self.fechas)
        rangos = {}
        for metrica in METRICAS:
            cargas = []
            for w in pool:
                terminos = [self.x[(w, f, s)] for f in self.fechas
                            for s in self.turnos_wd.get((w, f), [])
                            if self._contribuye(metrica, s, f)]
                if not terminos:
                    continue                          # este trabajador no soporta esta carga
                carga = self.m.new_int_var(0, n, f"carga_{metrica}_{w}")
                self.m.add(carga == sum(terminos))
                cargas.append(carga)
            if len(cargas) < 2:
                continue                              # sin dispersión que repartir
            lo = self.m.new_int_var(0, n, f"min_{metrica}")
            hi = self.m.new_int_var(0, n, f"max_{metrica}")
            self.m.add_min_equality(lo, cargas)
            self.m.add_max_equality(hi, cargas)
            rango = self.m.new_int_var(0, n, f"rango_{metrica}")
            self.m.add(rango == hi - lo)
            rangos[metrica] = rango
        return rangos

    # -- Resolución (lexicográfica por objetivo único) ----------------------- #
    def resolver(self, segundos: float = 60, trabajadores_cpu: int = 8):
        """Minimiza BIG_P1·P1 (cobertura) + P2 (equidad): P1 domina, P2 desempata."""
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = segundos
        solver.parameters.num_search_workers = trabajadores_cpu
        self.m.minimize(BIG_P1 * self.coste_p1 + self.coste_p2)
        return solver, solver.solve(self.m)


# --------------------------------------------------------------------------- #
#  Prueba: resolver una ventana (enero de 2026)
# --------------------------------------------------------------------------- #
def _prueba() -> None:
    datos = cargar("data/input")
    fechas = rango_fechas(date(2026, 1, 1), date(2026, 1, 31))

    modelo = Modelo(datos, fechas)
    print(f"Ventana: {fechas[0]:%d/%m} a {fechas[-1]:%d/%m}  ({len(fechas)} días)")
    print(f"Variables x: {len(modelo.x)}  (refuerzo: {len(modelo.refuerzo)})  "
          f"holguras u: {len(modelo.u)}")

    solver, estado = modelo.resolver(segundos=60)
    print(f"Estado: {solver.status_name(estado)}")
    if estado not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return

    huecos = sum(solver.value(u) for u in modelo.u.values())
    demanda = sum(datos.turnos[t].dem for (t, _) in modelo.u)
    coste_p1 = sum(PESO[datos.turnos[t].tipo] * solver.value(u) for (t, _), u in modelo.u.items())
    coste_p2 = sum(LAMBDA[m] * solver.value(r) for m, r in modelo.rangos.items())
    print(f"P1 (cobertura): coste {coste_p1}  ->  {demanda - huecos}/{demanda} cubiertos "
          f"({huecos} sin cubrir)")
    print(f"P2 (equidad): coste {coste_p2}  |  rangos por métrica:")
    for metrica, rango in modelo.rangos.items():
        print(f"    {metrica:>8}: rango {solver.value(rango)}")


if __name__ == "__main__":
    _prueba()

"""
modelo.py — Construcción del modelo CP-SAT de cuadrantes.

Sobre una ventana de fechas construye variables x[w,d,s] (solo elegibles) y las
restricciones duras
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta, time, datetime

from cargar_datos import DIAS, DATA, LIBRE, Datos, cargar

# pyarrow (conda) carga libprotobuf 5.29 y choca con el 6.33 que trae OR-Tools al importar
# ortools (que importa pandas -> pyarrow). Se bloquea ANTES de importar ortools. Inofensivo
# en entornos sin ese conflicto.
sys.modules.setdefault("pyarrow", None)

from ortools.sat.python import cp_model

FECHA_INI = date(2026, 1, 1)
FECHA_FIN = date(2026, 1, 31)

# Parámetros legales (V Convenio CyL)
RMIN = 12          # descanso mínimo entre jornadas (h)          — art. 23, 31
HMAX7 = 48         # máx. trabajo efectivo en 7 días (h)         — art. 23.2
HMAX_4SEM = 160    # máx. trabajo efectivo en 4 semanas (h)      — art. 23 A
HMAX_AÑO = 1776    # jornada anual efectiva (h) se aplica en el año completo, no por ventana
CMAX = 6           # máx. días consecutivos trabajados

PESO_TRABAJADOR = {
    "correturno": 1,
    "mixto": 2,
    "turno": 4,
    "fijo": 999,
}

# Cargas indeseables cuya dispersión se reparte (equidad, P2) y su importancia relativa
METRICAS = ("noche", "finde", "festivo", "12h", "24h", "partido")
PESO_TURNOS = {
    "24h": 10,
    "12h": 8,
    "noche": 6,
    "partido": 4,
    "tarde": 3,
    "mañana": 2,
}

LAMBDA = {
    "24h": 6,
    "noche": 5,
    "festivo": 5,
    "12h": 4,
    "finde": 3,
    "partido": 2,
}

PESO_FLEX = {
    "correturno": 4,
    "mixto": 2,
    "turno": 1,
}
# Lexicográfico por objetivo único: minimizar  W·P1 + P2, con W > max(P2). Como P2 = Σ λ·rango
# y cada rango ≤ nº de días, max(P2) = Σλ·nº_días -> W se calcula según el horizonte
# (W = Σλ·nº_días + 1). Así P1 domina SIEMPRE y P2 solo desempata (evita que al crecer el
# horizonte P2 compita con P1 y el solver deje gente ociosa para bajar rangos).


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

        #Conjunto de las variables del modelo x_trab_fecha_turno
        self.x: dict[tuple[str, date, str], cp_model.BoolVar] = {} 
        #Conjunto de trabajadores los cuales para esa fecha son refuerzo
        self.refuerzo: set[tuple[str, date, str]] = set() 
        #Conjunto de turnos que puede hacer un trabajador en una fecha
        self.turnos_wd: dict[tuple[str, date], list[str]] = defaultdict(list) 

        self.trabaja: dict[tuple[str, date], cp_model.BoolVar] = {}   # ¿w trabaja el día d? 1 si si 0 sino
        self.u: dict[tuple[str, date], cp_model.IntVar] = {}         # holgura de cobertura intVar n trabajadores se requieren (turno,fecha)

        self._crear_variables()
        self._c1_cobertura()
        self._c2_un_turno_dia()
        self._c4_descanso()
        self._c5_dias_consecutivos()
        self._c6_horas_semana()
        self._c6b_horas_cuatrisemana()
        self._c7_descanso_semanal()
        self._c8_fijos()
        self._warm_start_patron()                 # arranque pegado al patrón

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

    #Funcion que devuelve listado de turnos para los que el trabajador puede trabajar en la fecha f
    def _turnos(self, trab: str, f: date) -> list[cp_model.BoolVar]:
        return [self.x[(trab, f, s)] for s in self.turnos_wd.get((trab, f), [])]


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
                turnos = self._turnos(trab, f)
                self.m.add_at_most_one(turnos) #Como mucho trabajara un turno al dia

                trabaja = self.m.new_bool_var(f"trab_{trab}_{f:%Y%m%d}")
                self.trabaja[(trab, f)] = trabaja
                self.m.add(trabaja == sum(turnos))


    
    def _c4_descanso(self) -> None:
        """Descanso >= RMIN horas entre el turno de un día y el del siguiente. De manera que de como mucho sea 1"""
        incompatibles = self._pares_incompatibles()
        
        for trab in self.datos.trabajadores:
            for hoy, manana in zip(self.fechas, self.fechas[1:]):
                for s1 in self.turnos_wd.get((trab, hoy), []):
                    for s2 in self.turnos_wd.get((trab, manana), []):

                        if (s1, s2) in incompatibles:
                            self.m.add_at_most_one([
                                self.x[(trab, hoy, s1)],
                                self.x[(trab, manana, s2)],
                            ])




    def _intervalo_turno(self, fecha, turno):
        inicio = datetime.combine(fecha, turno.hora_entrada)
        fin = datetime.combine(fecha, turno.hora_salida)

        # Si la salida es menor o igual que la entrada,
        # asumimos que el turno termina al día siguiente.
        if fin <= inicio:
            fin += timedelta(days=1)

        return inicio, fin

        
    def _pares_incompatibles(self) -> set[tuple[str, str]]:
        """
        Devuelve pares (s1, s2) no encadenables en días consecutivos
        porque dejan menos de RMIN horas de descanso.
        """
        base = date(2000, 1, 1)
        dia_siguiente = base + timedelta(days=1)
        descanso_minimo = timedelta(hours=RMIN)

        incompatibles = set()

        for s1, t1 in self.datos.turnos.items():
            _, fin_s1 = self._intervalo_turno(base, t1)

            for s2, t2 in self.datos.turnos.items():
                inicio_s2, _ = self._intervalo_turno(dia_siguiente, t2)

                descanso = inicio_s2 - fin_s1

                if descanso < descanso_minimo:
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
        (b) al menos un sábado+domingo libres en cada ventana de 4 semanas.
        Solo se aplica a SEMANAS COMPLETAS del horizonte (las semanas truncadas del borde
        se ignoran; se cubren al alinear el horizonte a semanas / con el horizonte rodante)."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            dias_semana[semana(f)].append(f)
        completas = sorted(s for s, ds in dias_semana.items() if len(ds) == 7)

        for trab in self.datos.trabajadores:
            # (a) al menos un par de días consecutivos ambos libres, por semana completa
            for sem in completas:
                dias = dias_semana[sem]
                pares = []
                for hoy, manana in zip(dias, dias[1:]):
                    par = self.m.new_bool_var(f"descanso2_{trab}_{hoy:%Y%m%d}")

                    #Define si trabaja hoy no puede pertencer al par descanso, y si pertenece al par descanso entonces 
                    #Ni hoy ni mañana debe trabajar
                    self.m.add(par == 0).only_enforce_if(self.trabaja[(trab,hoy)])
                    self.m.add(par == 0).only_enforce_if(self.trabaja[(trab,manana)])
                    self.m.add(par == 1).only_enforce_if([
                        self.trabaja[(trab,hoy)].Not(),
                        self.trabaja[(trab,manana)].Not(),
                    ])
                    pares.append(par)

                self.m.add(sum(pares) >= 1)

            # (b) sábado+domingo libres, al menos una vez cada 4 semanas completas
            finde_libre = []
            for sem in completas:
                sab = next(d for d in dias_semana[sem] if d.weekday() == 5)
                dom = next(d for d in dias_semana[sem] if d.weekday() == 6)
                fl = self.m.new_bool_var(f"findelibre_{trab}_{sem[0]}w{sem[1]}")
                # Si trabaja sábado, no cuenta como finde libre
                self.m.add(fl == 0).only_enforce_if(self.trabaja[(trab, sab)])
                # Si trabaja domingo, no cuenta como finde libre
                self.m.add(fl == 0).only_enforce_if(self.trabaja[(trab, dom)])
                # Si sábado y domingo son libres, entonces sí cuenta como finde libre
                self.m.add(fl == 1).only_enforce_if([
                    self.trabaja[(trab,sab)].Not(),
                    self.trabaja[(trab,dom)].Not(),
                ])
                finde_libre.append(fl)
            # En cada ventana de 4 semanas completas, al menos un finde completo libre
        for i in range(len(finde_libre) - 3):
            self.m.add(sum(finde_libre[i:i + 4]) >= 1)

    def _linea_fija(self, trab: str) -> str | None:
        """Devuelve la única línea de capacidad normal de un fijo, si existe."""
        turnos = []

        for (w, turno), cap in self.datos.capacidades.items():
            if w != trab:
                continue

            if cap.v == 0 and (cap.lv or cap.sab or cap.dom or cap.fest):
                turnos.append(turno)

        if not turnos:
            return None

        if len(turnos) > 1:
            raise ValueError(
                f"El trabajador fijo {trab} tiene más de una línea fija: {turnos}"
            )

        return turnos[0]


    def _c8_fijos(self) -> None:
        """Fijos congelados (capa 1): trabajan su línea siempre que opere y estén disponibles."""
        for w, t in self.datos.trabajadores.items():
            if t.tipo != "fijo":
                continue
            phi = self._linea_fija(w)
            # Una línea de más de 8 h no la puede sostener una sola persona 5 días/semana
            # (superaría 48 h/7d o 160 h/4sem): esos fijos se dejan libres, no se congelan.
            if phi is None or self.datos.turnos[phi].horas > 8:
                continue
            for f in self.fechas:
                if (w, f, phi) in self.x:
                    self.m.add(self.x[(w, f, phi)] == 1)

    def _warm_start_patron(self) -> None:
        """Siembra la búsqueda con el patrón: cada trabajador de patrón sugiere la línea que le
        tocaría según la rotación (una fila por semana). Es un hint, no obliga."""
        ancla = self.fechas[0] - timedelta(days=self.fechas[0].weekday())   # lunes de la 1ª semana
        grupos: dict[str, list[str]] = defaultdict(list)
        for w, t in self.datos.trabajadores.items():
            if t.tipo == "patron" and t.patron:
                grupos[t.patron].append(w)
        for patron, trabs in grupos.items():
            filas = self.datos.patrones.get(patron)
            if not filas:
                continue
            T = len(filas)
            for offset, w in enumerate(sorted(trabs)):
                for f in self.fechas:
                    turno = filas[(offset + (f - ancla).days // 7) % T][DIAS[f.weekday()]]
                    if turno != LIBRE and (w, f, turno) in self.x:
                        self.m.add_hint(self.x[(w, f, turno)], 1)


    # -- Objetivos ----------------------------------------------------------- #
    def _coste_cobertura(self):
        """P1: turnos no cubiertos ponderados por criticidad."""
        return sum(PESO_TURNOS[self.datos.turnos[turno].tipo] * holgura
                for (turno, _), holgura in self.u.items())

    def _coste_preferencia_trabajador(self):
        """
        Penalización suave por asignar cargas indeseables a trabajadores menos flexibles.
        No sustituye a la equidad ponderada.
        Solo actúa como desempate.
        """
        coste = []
        for w, trabajador in self.datos.trabajadores.items():
            if trabajador.tipo == "fijo":
                continue

            peso_w = PESO_TRABAJADOR.get(trabajador.tipo, 1)

            for f in self.fechas:
                for s in self.turnos_wd.get((w, f), []):
                    for metrica in METRICAS:
                        if self._contribuye(metrica, s, f):
                            coste.append(
                                peso_w * LAMBDA[metrica] * self.x[(w, f, s)]
                            )
        return sum(coste)


    def _contribuye(self, metrica: str, turno: str, f: date) -> bool:
        """¿La asignación (turno, día) suma a la carga indeseable 'metrica'?"""
        t = self.datos.turnos[turno]
        match metrica:
            case "noche":
                return t.tipo == "noche"
            case "finde":
                return f.weekday() >= 5
            case "festivo":
                return self.datos.es_festivo(f, t.municipio)
            case "24h":
                return t.tipo == "24h"
            case "12h":
                return t.tipo == "12h"
            case "partido":
                return t.tipo == "partido"
            case _:
                return False

    def _equidad_ponderada(self) -> dict[str, cp_model.IntVar]:
        """Para cada métrica: carga por trabajador (fijos fuera) y su rango max-min, medido
        solo entre quienes pueden soportar esa carga."""
        pool = [w for w, t in self.datos.trabajadores.items() if t.tipo != "fijo"]
        n = len(self.fechas)    
        desviaciones: dict[str, list[cp_model.IntVar]] = {}

        rangos = {}
        for metrica in METRICAS:
            cargas = []
            for w in pool:
                trabajador = self.datos.trabajadores[w]
                peso_w = PESO_FLEX.get(trabajador.tipo, 1)
                terminos = [
                                self.x[(w, f, s)]
                                for f in self.fechas
                                for s in self.turnos_wd.get((w, f), [])
                                if self._contribuye(metrica, s, f)
                            ]
                if not terminos:
                    continue                          # este trabajador no soporta esta carga
                carga = self.m.new_int_var(0, n, f"carga_{metrica}_{w}")
                self.m.add(carga == sum(terminos))
                cargas.append((w,carga,peso_w))
                
            if len(cargas) < 2:
                continue                              # sin dispersión que repartir
            peso_total = sum(peso_w for _,_,peso_w in cargas)
            carga_total = self.m.new_int_var(0,n*len(cargas),f"total_{metrica}")

            self.m.add(carga_total == sum(carga for _, carga, _ in cargas))
            desviaciones[metrica] = []

            for w, carga_w, peso_w in cargas:    
                """
                Queremos minimizar la diferencia entre:
                    carga_w / peso_w
                y:
                    carga_total / peso_total
                Para evitar divisiones:
                    peso_total * carga_w ~= peso_w * carga_total
                """
                expr = peso_total * carga_w - peso_w * carga_total
                max_desv = n * peso_total
                desv = self.m.new_int_var(0,max_desv,f"desv_{metrica}_{w}")
                self.m.add_abs_equality(desv, expr)
                desviaciones[metrica].append(desv)
        return desviaciones

    # -- Resolución (objetivo jerárquico por pesos) -------------------------- #
    def resolver(self, segundos: int = 120, trabajadores_cpu: int = 4, log: bool = False):
        """Objetivo jerárquico en uno solo:  W1·P1 + W2·P2 + W3·P3, con W1 > W2 > W3.
        P1: cobertura (turnos no cubiertos)  ->  P2: equidad ponderada  ->  P3: preferencia
        por perfil. Los pesos garantizan que P1 domine a P2 y P2 a P3."""
        p1 = self._coste_cobertura()
        desviaciones = self._equidad_ponderada()
        p2 = sum(LAMBDA[metrica] * sum(vars_desv) for metrica, vars_desv in desviaciones.items())
        p3 = self._coste_preferencia_trabajador()

        n = len(self.fechas)
        num_trab = len(self.datos.trabajadores)
        max_lambda = sum(LAMBDA.values())
        # Cotas conservadoras de P2 y P3 para escalar los pesos (W1 > max aporte de P2+P3).
        max_p2 = max_lambda * n * num_trab * max(PESO_FLEX.values())
        max_p3 = max_lambda * n * num_trab * max(PESO_TRABAJADOR.values())
        W3 = 1
        W2 = max_p3 + 1
        W1 = (max_p2 * W2) + max_p3 + 1

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = segundos
        solver.parameters.num_search_workers = trabajadores_cpu
        solver.parameters.log_search_progress = log
        self.m.minimize(W1 * p1 + W2 * p2 + W3 * p3)      # <-- el objetivo que faltaba
        return solver, solver.solve(self.m)

def imprimir_resumen(modelo: Modelo, solver: cp_model.CpSolver, status: int) -> None:
    """Imprime un resumen básico de la solución."""
    print("\n" + "=" * 80)
    print("RESUMEN DEL SOLVER")
    print("=" * 80)
    estados = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }
    print(f"Estado: {estados.get(status, status)}")
    print(f"Tiempo: {solver.WallTime():.2f} s")
    print(f"Conflictos: {solver.NumConflicts()}")
    print(f"Ramas: {solver.NumBranches()}")

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Objetivo: {solver.ObjectiveValue():.0f}")
        print(f"Mejor cota: {solver.BestObjectiveBound():.0f}")

        if solver.ObjectiveValue() != 0:
            gap = abs(solver.ObjectiveValue() - solver.BestObjectiveBound()) / abs(solver.ObjectiveValue())
            print(f"Gap aproximado: {gap:.2%}")

        print("\n" + "-" * 80)
        print("TURNOS NO CUBIERTOS")
        print("-" * 80)

        total_holguras = 0

        if hasattr(modelo, "u"):
            for (turno, f), holgura in sorted(modelo.u.items(), key=lambda x: (x[0][1], x[0][0])):
                valor = solver.Value(holgura)
                if valor:
                    total_holguras += valor
                   #print(f"{f} | {turno}: {valor}")

        if total_holguras == 0:
            print("Todos los turnos quedaron cubiertos.")
        else:
            print(f"Total huecos sin cubrir: {total_holguras}")

        print("=" * 80 + "\n")
# --------------------------------------------------------------------------- #
#  Prueba: resolver una ventana (enero de 2026)
# --------------------------------------------------------------------------- #
def main() -> None:
    datos = cargar(DATA)
    fechas = rango_fechas(FECHA_INI,FECHA_FIN)

    print("=" * 80)
    print("CONSTRUCCIÓN DEL MODELO")
    print("=" * 80)
    print(f"Horizonte: {fechas[0]} -> {fechas[-1]}")
    print(f"Nº días: {len(fechas)}")
    print(f"Nº trabajadores: {len(datos.trabajadores)}")
    print(f"Nº turnos: {len(datos.turnos)}")
    print("=" * 80)

    modelo = Modelo(datos, fechas)

    
    solver, status = modelo.resolver(
        segundos=120,
        trabajadores_cpu=8,
        log=False,
    )

    imprimir_resumen(modelo, solver, status)

if __name__ == "__main__":
    main()

"""
zonas/valladolid/correturnos.py — Lunes a viernes de los CORRETURNOS de Valladolid: lo que dejan
los fijos.

Un CP-SAT de todo el año sobre las plazas que quedan, con las mismas reglas que el de fijos
(hereda su modelo): un turno al día, no sobrecubrir, C4, C5, C6, dos libres por semana que no sean
festivo, descanso de fin de semana y el techo de la jornada. Lo que cambia:

  * entran los correturnos de todas las bases a la vez, cada uno con su pool (sin noches ni
    guardias de otra base);
  * cada línea es su propia casilla: el modelo elige la LÍNEA directamente, y C4 es exacto;
  * no hay zona semanal: ellos absorben la inestabilidad. La coherencia de la semana es solo una
    preferencia, el último nivel;
  * no tienen que llegar a su jornada: hay más horas de plantilla que trabajo.

Objetivo por niveles: 1 cobertura · 2 equidad de horas entre ellos · 3 equidad de días duros
(tarde, partido, noche) · 4 coherencia semanal (cuantas menos zonas distintas en la semana, mejor).
"""
from __future__ import annotations

from collections import Counter, defaultdict

from cpsat import lexicografico, nivel, reparto
from dominio import Datos, Plan, TipoTrabajador
from zonas.valladolid import fijos
from zonas.valladolid.fijos import DURAS, lunes_de


class Modelo(fijos.Modelo):
    def __init__(self, datos: Datos, plan: Plan):
        self.base = "correturnos"
        self.construir(datos, plan,
                       sorted(w for w, t in datos.trabajadores.items() if t.tipo == TipoTrabajador.CORRETURNO),
                       fijos.faltan(datos, plan))

    def casilla_de(self, turno_id: str):
        """Una casilla por línea: (base, franja, minutos, línea). Los tres primeros son los de la
        casilla de fijos, así que las reglas heredadas (C6, zona) los leen igual."""
        t = self.datos.turnos[turno_id]
        return t.base, t.franja.value, t.minutos_computo, turno_id

    # -- niveles ------------------------------------------------------------ #
    def equidad_horas(self):
        """Que las horas del año se repartan por igual entre ellos."""
        techo = max(self.datos.minutos_objetivo(w) for w in self.gente)
        nuevas = defaultdict(list)
        for (w, f, k), v in self.y.items():
            nuevas[w].append(k[2] * v)
        cuentas = []
        for w in self.gente:
            h = self.m.NewIntVar(0, techo, f"minutos_{w}")
            self.m.Add(h == self.plan.minutos(w) + sum(nuevas[w]))
            cuentas.append(h)
        terminos = reparto(self.m, cuentas, techo, "horas")
        return sum(terminos) if terminos else None

    def equidad_duros(self):
        """Que los días duros (tarde, partido, noche) se repartan por igual entre ellos."""
        duros = defaultdict(list)
        for (w, f, k), v in self.y.items():
            if k[1] in DURAS:
                duros[w].append(v)
        techo = max((len(vs) for vs in duros.values()), default=0)
        cuentas = []
        for w in self.gente:
            c = self.m.NewIntVar(0, techo, f"duros_{w}")
            self.m.Add(c == sum(duros[w]))
            cuentas.append(c)
        terminos = reparto(self.m, cuentas, techo, "duros")
        return sum(terminos) if terminos else None

    def coherencia(self):
        """Por persona y semana, las zonas (base y franja) distintas que usa por encima de la
        primera: 0 si toda la semana es de la misma."""
        por_semana = defaultdict(lambda: defaultdict(list))
        for (w, f, k), v in self.y.items():
            por_semana[(w, lunes_de(f))][k[:2]].append(v)
        coste = []
        for (w, lunes), zonas in por_semana.items():
            if len(zonas) < 2:
                continue
            usadas = []
            for zn, vs in zonas.items():
                u = self.m.NewBoolVar(f"usa_{w}_{lunes:%m%d}_{zn[0]}_{zn[1]}")
                self.m.AddMaxEquality(u, vs)
                usadas.append(u)
            trabaja = self.m.NewBoolVar(f"semana_{w}_{lunes:%m%d}")
            self.m.AddMaxEquality(trabaja, usadas)
            coste.append(sum(usadas) - trabaja)
        return sum(coste) if coste else None

    def resolver(self, segundos, hilos, log):
        self.un_turno_y_cobertura()
        self.descanso()
        self.semanas()
        self.jornada()
        print(f"  correturnos: {len(self.gente)} · {sum(self.faltan.values())} plazas L-V · "
              f"{len(self.y)} variables", flush=True)
        m = self.m
        niveles = [nivel(m, "1 cobertura", lambda: sum(self.y.values()), True),
                   nivel(m, "2 equidad de horas", self.equidad_horas, False),
                   nivel(m, "3 equidad de días duros", self.equidad_duros, False),
                   nivel(m, "4 coherencia semanal", self.coherencia, False)]
        sol = lexicografico(m, niveles, segundos, hilos, log, decision=list(self.y.values()))
        if sol is None:
            return None
        return {(w, f): k for (w, f, k), v in self.y.items() if sol.Value(v)}


def resolver(datos: Datos, plan: Plan, segundos: int = 120, hilos: int = 8, log: bool = False):
    """Coloca a los correturnos de lunes a viernes. Modifica el plan. Devuelve el modelo."""
    modelo = Modelo(datos, plan)
    elegidas = modelo.resolver(segundos, hilos, log)
    if elegidas is None:
        print("  correturnos: *** sin solución ***")
        return modelo
    for (w, f), k in elegidas.items():
        plan.poner(w, f, k[3])
    print(f"  correturnos: asignados {len(elegidas)} días", flush=True)
    return modelo


def informe(datos: Datos, plan: Plan, modelo: Modelo) -> None:
    """Lo que queda sin cubrir y cómo les ha quedado el año a los correturnos."""
    huecos = fijos.faltan(datos, plan)
    print(f"\nPlazas L-V sin cubrir por nadie: {sum(huecos.values())}"
          + (" · " + " · ".join(f"{b} {fr} {n}" for (b, fr), n in sorted(Counter(
              (datos.turnos[s].base, datos.turnos[s].franja.value) for (s, f) in huecos for _ in range(huecos[(s, f)])
          ).items())) if huecos else ""))
    semanas = defaultdict(set)
    for (w, f), s in plan.items():
        if w in modelo.gente and isinstance(s, str) and f.weekday() < 5:
            t = datos.turnos[s]
            semanas[(w, lunes_de(f))].add((t.base, t.franja.value))
    print("Correturnos: horas · días L-V · % duros L-V · semanas de una sola zona")
    for w in modelo.gente:
        dias = [(f, datos.turnos[s]) for (x, f), s in plan.items()
                if x == w and isinstance(s, str) and f.weekday() < 5]
        duros = sum(1 for _, t in dias if t.franja.value in DURAS)
        mias = [z for (x, _), z in semanas.items() if x == w]
        una = sum(1 for z in mias if len(z) == 1)
        print(f"   {w}: {plan.minutos(w) / 60:.0f} h · {len(dias)} días · "
              f"{duros / len(dias) * 100 if dias else 0:.0f} % duros · {una}/{len(mias)} semanas de una zona")

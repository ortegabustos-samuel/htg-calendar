"""
findes.py — Reparto de SÁBADOS, DOMINGOS y FESTIVOS entre fijos y correturnos, antes de lunes a
viernes.

Tres procesos en cadena, cada uno sobre el plan que deja el anterior y con lo que ya hay en él
(ciclos de patrón, traspasos, vacaciones) como constante:

  1. festivos — primero, para que un sábado festivo ya tenga gente cuando llegue su domingo
  2. sábados
  3. domingos — solo quien trabaja el sábado de ese fin de semana (domingo_ok), y mejor en la
     misma línea que el sábado

Se asigna la LÍNEA directamente: en un solo tipo de día no hay semana que estabilizar, y lo que
sale son ya las semanas con sábado y con sábado y domingo. Lo que queda por decidir después
(fijos.py) son las semanas de lunes a viernes: mañana, tarde o partido.

Cada proceso es un CP-SAT LEXICOGRÁFICO:

  1. cobertura — siempre por delante de la equidad
  2. equidad   — dentro de cada pool: los fijos de cada municipio entre sí, y los correturnos
                 junto a los fijos de su municipio (Valladolid)
  3. (domingos) repetir la línea del sábado

Duras: un turno al día, no sobrecubrir, C4 contra lo que ya hay en el plan y entre días seguidos
del mismo proceso, C5 y C6 de la semana ISO y el techo de la jornada anual.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from cargar_datos import DESCANSOS, turno_de
from fijos import descansa, descanso_con_lo_fijo, faltan, lineas_de_fijos, pool
from modelo import decimas, lexicografico, lunes_de, nivel, reparto

# Los festivos van primero: un sábado festivo tiene que estar ya repartido cuando llega su
# domingo, o domingo_ok no deja a nadie hacerlo.
CLASES = (("FEST", "festivos"), ("SAB", "sábados"), ("DOM", "domingos"))


def repartir(datos, plan, libro, segundos=120, hilos=8, log=False):
    """Los tres repartos en orden. Modifica plan y libro."""
    for clase, nombre in CLASES:
        print(f"  — {nombre}")
        reparto_de(datos, plan, libro, clase, segundos, hilos, log)


def reparto_de(datos, plan, libro, clase, segundos, hilos, log):
    gente = pool(datos)
    pendientes = {(s, f): n for (s, f), n in faltan(datos, plan, lineas_de_fijos(datos)).items()
                  if datos.tipo_dia(f, datos.turnos[s].municipio) == clase}
    m = cp_model.CpModel()

    x = {}
    for (s, f) in pendientes:
        for w in gente:
            if (w, f) in plan or not datos.elegible(w, s, f)[0]:
                continue
            if clase == "DOM" and turno_de(plan, w, f - timedelta(days=1)) is None:
                continue                                    # domingo_ok: sin sábado no hay domingo
            if descanso_con_lo_fijo(datos, plan, w, f, s):
                x[(w, f, s)] = m.NewBoolVar(f"x_{w}_{f:%m%d}_{s}")
    if not x:
        print("    nada que repartir")
        return

    por_dia, por_plaza, por_trab = defaultdict(list), defaultdict(list), defaultdict(list)
    for (w, f, s), v in x.items():
        por_dia[(w, f)].append((s, v))
        por_plaza[(s, f)].append(v)
        por_trab[w].append((f, s, v))

    # Un turno al día y no sobrecubrir
    for vs in por_dia.values():
        m.AddAtMostOne(v for _, v in vs)
    for (s, f), vs in por_plaza.items():
        m.Add(sum(vs) <= pendientes[(s, f)])

    # C4 entre días seguidos del mismo proceso (dos festivos seguidos, por ejemplo)
    for (w, f), hoy in por_dia.items():
        for s2, v2 in por_dia.get((w, f + timedelta(days=1)), ()):
            for s1, v1 in hoy:
                if not descansa(datos, s1, s2):
                    m.AddBoolOr([v1.Not(), v2.Not()])

    # C5 y C6 de la semana ISO, contando lo que ya tiene en el plan
    cfg = datos.config
    por_semana = defaultdict(list)
    for (w, f, s), v in x.items():
        por_semana[(w, lunes_de(f))].append((s, v))
    for (w, lunes), vs in por_semana.items():
        previos = [t for t in (turno_de(plan, w, lunes + timedelta(days=i)) for i in range(7)) if t]
        m.Add(sum(v for _, v in vs) <= cfg.dias_max_semana - len(previos))
        m.Add(sum(decimas(datos.turnos[s].horas) * v for s, v in vs)
              <= decimas(cfg.horas_max_semana - sum(datos.turnos[t].horas for t in previos)))

    # Techo de la jornada anual
    for w, lista in por_trab.items():
        m.Add(sum(decimas(datos.turnos[s].horas) * v for _, s, v in lista)
              <= decimas(libro.objetivo(w) - libro.horas(w)))

    # -- Niveles ------------------------------------------------------------ #
    niveles = [nivel(m, "1 cobertura", lambda: sum(x.values()), True),
               nivel(m, "2 equidad", lambda: equidad(m, datos, plan, clase, por_trab), False)]
    if clase == "DOM":
        repite = [v for (w, f, s), v in x.items() if plan.get((w, f - timedelta(days=1))) == s]
        if repite:
            niveles.append(nivel(m, "3 misma línea que el sábado", lambda: sum(repite), True))
    sol = lexicografico(m, niveles, segundos, hilos, log, sangria="  ", decision=list(x.values()))
    if sol is None:
        print("    *** sin solución ***")
        return

    for (w, f, s), v in x.items():
        if sol.Value(v):
            plan[(w, f)] = s
            libro.apunta(w, s)
    resumen(datos, plan, clase, pendientes, gente)


def equidad(m, datos, plan, clase, por_trab):
    """Lo desigual que se reparte la clase dentro de cada pool, entre quienes pueden hacerla. El
    pool es el municipio del trabajador: los correturnos caen en el de Valladolid con sus fijos.
    Lo que ya trae el plan de esa clase (ciclos heredados) cuenta como constante."""
    previas = Counter(w for (w, f), s in plan.items()
                      if w in por_trab and s not in DESCANSOS
                      and datos.tipo_dia(f, datos.turnos[s].municipio) == clase)
    por_pool = defaultdict(list)
    for w, lista in por_trab.items():
        techo = len(lista) + previas[w]
        c = m.NewIntVar(0, techo, f"n_{clase}_{w}")
        m.Add(c == previas[w] + sum(v for _, _, v in lista))
        por_pool[datos.trabajadores[w].municipio].append((c, techo))
    terminos = []
    for muni, cuentas in por_pool.items():
        terminos += reparto(m, [c for c, _ in cuentas], max(t for _, t in cuentas), f"{clase}_{muni}")
    return sum(terminos) if terminos else None


def resumen(datos, plan, clase, pendientes, gente):
    """Plazas que quedan sin cubrir y cómo ha quedado el reparto en cada pool."""
    cubiertas = Counter((s, f) for (w, f), s in plan.items() if (s, f) in pendientes)
    sin = sum(max(0, n - cubiertas[(s, f)]) for (s, f), n in pendientes.items())
    print(f"    {sum(pendientes.values()) - sin} de {sum(pendientes.values())} plazas cubiertas")
    cuenta = Counter(w for (w, f), s in plan.items() if w in set(gente) and s not in DESCANSOS
                     and datos.tipo_dia(f, datos.turnos[s].municipio) == clase)
    por_pool = defaultdict(list)
    for w in gente:
        por_pool[datos.trabajadores[w].municipio].append((cuenta[w], datos.trabajadores[w].tipo))
    for muni, cs in sorted(por_pool.items()):
        trabajan = [n for n, _ in cs if n]
        if trabajan:
            corr = [n for n, t in cs if t == "correturno"]
            print(f"    {muni:12} {len(trabajan)} personas, de {min(trabajan)} a {max(trabajan)}"
                  + (f" · correturnos de {min(corr)} a {max(corr)}" if corr else ""))

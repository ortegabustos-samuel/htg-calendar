"""PASO 4 — reparte lo que queda al pool (mixtos y correturnos), semana a semana.

Se recorre por SEMANA ISO y no por días porque el convenio se mide así (hmax7, cmax) y porque el
correturno necesita estabilidad de franja y de localización dentro de la semana. Dentro de cada
semana se va de MÁS DIFÍCIL A MÁS FÁCIL: asignar el lunes sin saber que el sábado hay un turno que
solo podía hacer esa misma persona es exactamente como se pierden los turnos escasos.

Cada turno va al elegible CON MÁS DEUDA. Es autoequilibrante y se verifica a ojo."""
from __future__ import annotations

from datetime import date

from calendario import semana
from cargar_datos import Datos
from deuda import Deuda
from legal import Legal
from plan import Plan

PASO = "reparto"


def candidatos(datos: Datos, plan: Plan, ley: Legal, f: date, turno: str) -> list[str]:
    """Gente del POOL que podría hacer ese turno ese día, ahora mismo y sin romper nada."""
    out = []
    for w in sorted(datos.trabajadores):
        if datos.trabajadores[w].tipo not in ("mixto", "correturno"):
            continue
        if plan.ocupado(w, f) or not datos.elegible(w, turno, f)[0]:
            continue
        if not ley.puede(plan, w, f, turno)[0]:
            continue
        out.append(w)
    return out


def pendientes_semana(datos: Datos, plan: Plan, sem: tuple[int, int]) -> list[tuple[date, str]]:
    """(día, turno) sin cubrir de esa semana, del más ESCASO al más fácil.

    La escasez se mide con la elegibilidad estática (`Datos.elegible`), no con la dinámica: es un
    orden de recorrido y tiene que ser estable mientras se asigna dentro de la semana."""
    pend: list[tuple[date, str]] = []
    for f in datos.fechas:
        if semana(f) != sem:
            continue
        for s in sorted(datos.turnos):
            t = datos.turnos[s]
            if not datos.opera(s, f):
                continue
            if plan.cubierto(f, s) >= t.dem:
                continue
            pend.append((f, s))

    def cuantos(par: tuple[date, str]) -> int:
        f, s = par
        return sum(1 for w in datos.trabajadores
                   if datos.trabajadores[w].tipo in ("mixto", "correturno")
                   and datos.elegible(w, s, f)[0])

    # Escasez ascendente; a igualdad, la línea más crítica primero; luego fecha e id de turno.
    return sorted(pend, key=lambda x: (cuantos(x), -datos.turnos[x[1]].prioridad, x[0], x[1]))


def _estabilidad(datos: Datos, plan: Plan, w: str, f: date, turno: str) -> tuple[int, int]:
    """Penalización por romper la semana del correturno: (cambia de franja, cambia de municipio).

    0 es mejor. El CLAUDE.md pide garantizarles una semana con horario similar y cierta estabilidad
    de localización; esto es lo que lo implementa, como DESEMPATE y nunca por encima de la deuda."""
    sem = semana(f)
    suyos = [plan.turno_de(w, d) for d in plan.dias_de(w) if semana(d) == sem]
    if not suyos:
        return (0, 0)
    t = datos.turnos[turno]
    franjas = {datos.turnos[s].tipo for s in suyos}
    munis = {datos.turnos[s].municipio for s in suyos}
    return (0 if t.tipo in franjas else 1, 0 if t.municipio in munis else 1)


def repartir(datos: Datos, plan: Plan, ley: Legal, dd: Deuda) -> None:
    """Recorre el año semana a semana y reparte lo que quede al pool."""
    semanas = sorted({semana(f) for f in datos.fechas})
    for sem in semanas:
        for f, s in pendientes_semana(datos, plan, sem):
            if plan.cubierto(f, s) >= datos.turnos[s].dem:
                continue
            libres = candidatos(datos, plan, ley, f, s)
            if not libres:
                plan.hueco(f, s, "ningun trabajador del pool puede ese dia")
                continue
            # La deuda manda; la estabilidad solo desempata.
            por_deuda = dd.orden(plan, libres, f, s)
            elegido = min(por_deuda,
                          key=lambda w: (_estabilidad(datos, plan, w, f, s),
                                         por_deuda.index(w)))
            descartados = tuple(f"{w}: mas horas acumuladas" for w in por_deuda[:3]
                                if w != elegido)
            plan.asignar(elegido, f, s, PASO,
                         f"del pool, el que menos carga lleva de los {len(libres)} elegibles",
                         descartados)

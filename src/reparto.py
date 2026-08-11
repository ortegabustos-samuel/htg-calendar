"""PASO 4 — reparte lo que queda al pool (mixtos y correturnos), semana a semana.

Se recorre por SEMANA ISO y no por días porque el convenio se mide así (hmax7, cmax) y porque el
correturno necesita estabilidad de franja y de localización dentro de la semana. Dentro de cada
semana se va de MÁS DIFÍCIL A MÁS FÁCIL: asignar el lunes sin saber que el sábado hay un turno que
solo podía hacer esa misma persona es exactamente como se pierden los turnos escasos.

Cada turno va al elegible CON MÁS DEUDA. Es autoequilibrante y se verifica a ojo.

Al final, `rellenar_refuerzos` hace una pasada aparte: con la cobertura real ya fija, reparte los
turnos COMODÍN (REF CAL) entre quienes se quedaron por debajo de su objetivo anual."""
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


def _elegir(datos: Datos, plan: Plan, por_deuda: list[str], f: date, turno: str) -> str:
    """De una lista YA ordenada de más deuda a menos (`Deuda.orden`), el elegido.

    La DEUDA manda: va primero en la clave del `min()`, como la posición en `por_deuda` (0 = más
    deuda). La estabilidad de franja/localización solo desempata cuando la deuda empata de
    verdad — cosa que `Deuda.orden` no deja pasar porque su propio desempate final es el id, así
    que en la práctica el elegido es siempre `por_deuda[0]`; se deja igualmente como clave
    compuesta para que la intención quede explícita y no dependa de leer `Deuda.orden` para
    saberlo."""
    return min(por_deuda,
               key=lambda w: (por_deuda.index(w), _estabilidad(datos, plan, w, f, turno)))


def repartir(datos: Datos, plan: Plan, ley: Legal, dd: Deuda) -> None:
    """Recorre el año semana a semana y reparte lo que quede al pool.

    Dentro de una semana, un `(fecha, turno)` puede necesitar MÁS de una plaza (REF CAL pide hasta
    10 por franja): se repite mientras falte cobertura y sigan quedando candidatos, igual que
    `criticos._cubrir_demanda` — un hueco por cada unidad que se quede sin nadie, no uno por
    par.

    Dentro de cada semana, la cobertura REAL (prioridad ≥ 1) va ANTES que el comodín REF CAL
    (prioridad 0), pase lo que pase con la escasez de `pendientes_semana` (que ordena por escasez
    SIN mirar prioridad salvo para desempatar). Es el propio contrato de `Turno.prioridad` en
    `cargar_datos.py`: "0 = turno COMODÍN... SIEMPRE dominado por cualquier turno de prioridad≥1".
    Sin este reparto en dos tandas, el bucle `while` de abajo (necesario para el hallazgo 2: sin
    él, REF CAL se queda al 27%/18%) puede agotar el cupo semanal (`cmax_pool` días, `hmax7` horas)
    de gente del pool en REF CAL —que aquí compite en la lista de `pendientes_semana` porque a
    veces tiene MENOS candidatos por capacidad estática que una línea real muy compartida, y por
    eso puede ordenarse ANTES en esa lista— antes de llegar a una cobertura real de esa misma
    semana que solo esa gente podía hacer. Medido: sin las dos tandas, los pares de cobertura real
    pendientes tras el paso 4 subían de ~277 a 753 con el resto de hallazgos ya corregidos.

    REF CAL (prioridad 0) es COMODÍN, no demanda real, así que aquí también se le aplica el freno
    de `rellenar_refuerzos`: no se le manda a nadie que ya haya alcanzado su objetivo anual. Sin
    este freno, el bucle `while` de abajo llenaría el comodín hasta `dem`=10 sin mirar las horas de
    nadie, y se comería el margen que `rellenar_refuerzos` necesita para parar en seco al llegar al
    objetivo — quien ya esté servido no cuenta como hueco, solo se deja de intentar con él."""
    semanas = sorted({semana(f) for f in datos.fechas})
    for sem in semanas:
        pend = pendientes_semana(datos, plan, sem)
        reales = [x for x in pend if datos.turnos[x[1]].prioridad >= 1]
        comodines = [x for x in pend if datos.turnos[x[1]].prioridad == 0]
        for f, s in reales + comodines:
            faltan = datos.turnos[s].dem - plan.cubierto(f, s)
            while faltan > 0:
                libres = candidatos(datos, plan, ley, f, s)
                if not libres:
                    plan.hueco(f, s, "ningun trabajador del pool puede ese dia")
                    faltan -= 1
                    continue
                if datos.turnos[s].prioridad == 0:
                    h = datos.turnos[s].horas
                    libres = [w for w in libres if dd.horas(plan, w) + h <= dd.objetivo(w)]
                    if not libres:
                        break          # nadie necesita ya estas horas: no es un hueco, es que sobra
                # La deuda manda; la estabilidad solo desempata.
                por_deuda = dd.orden(plan, libres, f, s)
                elegido = _elegir(datos, plan, por_deuda, f, s)
                rank_elegido = por_deuda.index(elegido)
                descartados = tuple(
                    f"{w}: {'mas' if por_deuda.index(w) > rank_elegido else 'menos'} horas acumuladas"
                    for w in por_deuda[:3] if w != elegido
                )
                plan.asignar(elegido, f, s, PASO,
                             f"del pool, el que menos carga lleva de los {len(libres)} elegibles",
                             descartados)
                faltan -= 1


def rellenar_refuerzos(datos: Datos, plan: Plan, ley: Legal, dd: Deuda) -> int:
    """PASADA FINAL: reparte los turnos COMODÍN (REF CAL, prioridad 0) entre quienes se han
    quedado por DEBAJO de su objetivo de jornada, una vez la cobertura real (`repartir`) ya está
    decidida.

    Va al final a propósito, con la cobertura real ya fija: si el relleno compitiera por el mismo
    presupuesto de horas que la cobertura, se comería margen que hace falta en el pico de
    vacaciones — medido en la rama CP-SAT (`modelo.rellenar_refuerzos`, del que este es réplica):
    la cobertura caía del 99.5% al 98.6% si el relleno entraba antes. Aquí solo se reparte lo que
    de verdad sobra.

    El reparto NO es uniforme: en cada ronda sirve a quien va MÁS CORTO respecto a su objetivo
    (`Deuda.objetivo` / `Deuda.horas`) y PARA EN SECO en cuanto lo alcanza — el corte
    `if falta <= 0` de `modelo.py:1908-1983`, portado literal. Hace falta explícito porque nada
    más lo frena: `Legal` solo topa límites SEMANALES (C4/C5/C6), no el objetivo anual, y
    `Deuda.orden` solo reordena candidatos, no decide cuándo parar.

    `datos.elegible` ya limita REF CAL a los correturnos (`_anadir_capacidades_correturno` en
    `cargar_datos.py` no les da esa capacidad a mixtos ni a nadie más), así que no hace falta
    filtrar por tipo aquí — igual que en el original.

    A diferencia del original, no hace falta un `semanas_adoptadas` aparte para no tocar a quien
    adoptó una plaza crítica esa semana: `criticos._adoptar` ya cede la semana ENTERA
    (`Plan.ceder` en todos los días que no curre), así que `plan.ocupado` la deja fuera sola —
    aquí el `Plan` sabe algo que en la rama CP-SAT había que recalcular a mano.
    Devuelve el nº de refuerzos asignados."""
    comodines = sorted(s for s, t in datos.turnos.items() if t.prioridad == 0)
    if not comodines:
        return 0

    asignados = 0
    while True:
        # a quién le falta más jornada frente a su objetivo, de más corto a menos
        faltan = sorted(((dd.objetivo(w) - dd.horas(plan, w), w) for w in datos.trabajadores),
                        reverse=True)
        for falta, w in faltan:
            if falta <= 0:
                return asignados
            objetivo_w = dd.objetivo(w)
            horas_w = objetivo_w - falta
            hueco = None
            for f in datos.fechas:
                if plan.ocupado(w, f):
                    continue
                for s in comodines:
                    if not datos.elegible(w, s, f)[0]:
                        continue
                    if horas_w + datos.turnos[s].horas > objetivo_w:
                        continue                       # no se pasa del objetivo: para en seco
                    if not ley.puede(plan, w, f, s)[0]:
                        continue
                    hueco = (f, s)
                    break
                if hueco:
                    break
            if hueco is None:
                continue                               # este no puede coger mas: se prueba el siguiente
            f, s = hueco
            plan.asignar(w, f, s, PASO,
                         f"refuerzo de calendario: le faltaban {falta:.0f} h para su objetivo")
            asignados += 1
            break                                       # vuelve a ordenar: siempre sirve al que va mas corto
        else:
            return asignados

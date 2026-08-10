"""PASO 1 — reparte las libranzas por exceso de jornada.

Casi todo patrón prescribe más horas de las que marca el convenio, así que ceder tiempo no es un
efecto secundario: es la mecánica central del problema. Este paso decide CUÁNTO cede cada uno y
DÓNDE cae, y lo hace antes que nada porque es la decisión que más condiciona al resto.

La moneda son las HORAS LEGALES. Con ella los dos patrones UVI salen a cero solos —sus 1.352 y
1.344 h están muy por debajo del objetivo, y su exceso aparente venía de cobrar la semana de
localizado entera—, así que no hace falta ninguna lista de exenciones. PAT_MEDINA se queda muy
lejos de los 60 h que da esa misma cuenta con horas de CONSUMO, por el mismo motivo: es el único
patrón no-UVI que toca un localizado de 24 h (VADN177, doce veces al año). Con horas legales y
por TRABAJADOR real (no el promedio del patrón) la mayoría de sus 9 filas ni siquiera llega al
objetivo — 7 de 9 no ceden nada, y los 2 que sí ceden 14 h y 33 h—: el exceso de un patrón no es
un número por grupo, depende de en qué semanas de la rotación caigan las vacaciones de cada uno."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from calendario import fila_patron, semana, turno_prescrito
from cargar_datos import DIAS, LIBRE, Datos
from plan import Plan

PASO = "libranzas"

# Escotilla de escape para un patrón cuya estructura no revele que es de bloque. Vacía: en este
# dataset la derivación acierta con los cuatro binomios. La especificación contemplaba declararla
# en `config.toml`, pero eso exigiría tocar `cargar_datos.Config` (frozen) para un caso que hoy no
# existe; cuando alguien lo necesite, se mueve allí y este set desaparece.
PATRONES_BLOQUE_FORZADOS: frozenset[str] = frozenset()


def es_bloque(datos: Datos, patron: str) -> bool:
    """¿La cesión arrastra la FILA ENTERA? Sí cuando el grupo entero es una alternancia de a lo
    sumo un trabajador por día, TODOS los días de la semana.

    Se deriva de `patrones.csv`, no se declara: para cada día de la semana se cuenta cuántas filas
    trabajan. Los cuatro binomios dan `1 1 1 1 1 1 1` — cada día, sea cual sea, trabaja como mucho
    uno de los dos, así que ceder un día deja la línea a cero y la cesión tiene que llevarse la
    fila completa (con sus descansos). PAT_GRANDE_VALL da `35 33 34 34 35 14 5` y admite huecos
    sueltos intrasemanales.

    OJO: el criterio es el MÁXIMO por día, no el mínimo. `PAT_MEDINA` (9 filas, una plaza fija por
    fila) da `8 9 8 8 8 2 1` — el 1 del domingo es el localizado VADN177, que en su grupo de 9 solo
    cubre una fila, pero el resto de la semana rota con holgura de sobra. Usar el mínimo lo
    confundiría con un binomio por ese único día suelto y forzaría a arrastrar la fila entera de
    lunes a viernes sin motivo; el máximo exige que la alternancia de "como mucho 1" se sostenga
    los 7 días, que es la definición real de binomio."""
    if patron in PATRONES_BLOQUE_FORZADOS:
        return True
    filas = datos.patrones.get(patron, [])
    if not filas:
        return False
    por_dia = [sum(1 for fila in filas
                   if fila.get(d) and fila[d] != LIBRE and fila[d] in datos.turnos)
               for d in DIAS]
    return max(por_dia) == 1


def horas_patron(datos: Datos, w: str) -> float:
    """Horas LEGALES que la rotación prescribe a `w` en todo el año, descontando vacaciones."""
    total = 0.0
    for f in datos.fechas:
        if not datos.disponible(w, f):
            continue
        s = turno_prescrito(datos, w, f)
        if s:
            total += datos.turnos[s].horas
    return total


def exceso_h(datos: Datos, w: str) -> float:
    """Horas que `w` debe CEDER: lo que su patrón prescribe por encima de su objetivo anual."""
    trab = datos.trabajadores[w]
    if trab.tipo != "patron":
        return 0.0
    objetivo = datos.config.horas_objetivo * trab.factor_jornada
    return max(0.0, horas_patron(datos, w) - objetivo)


def peso_semanas(datos: Datos) -> dict[tuple[int, int], float]:
    """Capacidad residual de cada semana ISO: disponibles ÷ turnos demandados.

    Los días NO son intercambiables. En agosto la demanda es la misma pero hay mucha menos gente,
    así que soltar ahí una libranza abre un hueco justo donde menos capacidad hay para taparlo. Un
    reparto uniforme lo haría; este reparto va a las semanas de mayor peso."""
    disp: dict[tuple[int, int], int] = defaultdict(int)
    dem: dict[tuple[int, int], int] = defaultdict(int)
    for f in datos.fechas:
        sem = semana(f)
        disp[sem] += sum(1 for w in datos.trabajadores if datos.disponible(w, f))
        dem[sem] += sum(t.dem for t in datos.turnos.values()
                        if t.prioridad >= 1 and datos.opera(t.id, f))
    return {sem: disp[sem] / dem[sem] if dem[sem] else 0.0 for sem in sorted(disp)}


def _dias_prescritos(datos: Datos, w: str, sem: tuple[int, int]) -> list[date]:
    """Días de esa semana ISO en que la rotación le prescribe turno y está disponible."""
    return [f for f in datos.fechas
            if semana(f) == sem and datos.disponible(w, f) and turno_prescrito(datos, w, f)]


def _dias_de_semana(datos: Datos, sem: tuple[int, int]) -> list[date]:
    return [f for f in datos.fechas if semana(f) == sem]


def repartir(datos: Datos, plan: Plan) -> None:
    """Marca en `plan` los días que cada trabajador de patrón cede por exceso de jornada."""
    pesos = peso_semanas(datos)
    orden_sem = sorted(pesos, key=lambda s: (-pesos[s], s))

    for w in sorted(datos.trabajadores):
        trab = datos.trabajadores[w]
        if trab.tipo != "patron":
            continue
        pendiente = exceso_h(datos, w)
        if pendiente <= 0:
            continue
        bloque = es_bloque(datos, trab.patron)

        for sem in orden_sem:
            if pendiente <= 0:
                break
            dias = _dias_prescritos(datos, w, sem)
            if not dias:
                continue

            if bloque:
                # La fila entera, con sus descansos: adoptar una plaza es llevársela completa,
                # y dejar medio binomio partido rompe la complementariedad de las dos filas.
                horas = sum(datos.turnos[turno_prescrito(datos, w, f)].horas for f in dias)
                if horas > pendiente + datos.turnos[turno_prescrito(datos, w, dias[0])].horas:
                    continue           # la fila se pasa demasiado: prueba otra semana
                for f in _dias_de_semana(datos, sem):
                    if datos.disponible(w, f) and not plan.ocupado(w, f):
                        plan.ceder(w, f, PASO, f"cesion de fila entera (semana {sem[1]}, "
                                                f"binomio {trab.patron})")
                pendiente -= horas
            else:
                # Días sueltos: uno por semana como mucho, para no vaciar una semana entera.
                f = dias[0]
                if plan.ocupado(w, f):
                    continue
                h = datos.turnos[turno_prescrito(datos, w, f)].horas
                plan.ceder(w, f, PASO, f"exceso de jornada ({pendiente:.0f} h pendientes, "
                                       f"semana {sem[1]} es de las de mas holgura)")
                pendiente -= h

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


def horas_prescritas(datos: Datos, w: str) -> float:
    """Horas LEGALES que la rotación —o la línea fija— prescribe a `w` en todo el año,
    descontando vacaciones. `turno_prescrito` ya distingue patrón/fijo, así que esta función sirve
    para los dos sin ramificar. (Se llamaba `horas_patron`; el nombre mentía desde que el paso 1
    empezó a cubrir también a los fijos — ver `exceso_h`.)"""
    total = 0.0
    for f in datos.fechas:
        if not datos.disponible(w, f):
            continue
        s = turno_prescrito(datos, w, f)
        if s:
            total += datos.turnos[s].horas
    return total


def exceso_h(datos: Datos, w: str) -> float:
    """Horas que `w` debe CEDER: lo que su rotación o línea prescribe por encima de su objetivo.

    LOS FIJOS TAMBIÉN CEDEN. `CLAUDE.md` lo dice desde el principio —"trabajadores fijos cuyo turno
    es constante... generalmente tendrán exceso y por lo tanto cuando descansen por exceso de horas
    su turno ha de ser cubierto"— pero la primera versión de este paso solo miraba `tipo == "patron"`
    y dejaba a los 6 fijos sin un solo día de cesión en todo el año. Medido: el fijo de VADN022
    —línea que opera los 7 días de la semana— llegaba a 2.312 h, 536 h por encima del objetivo."""
    trab = datos.trabajadores[w]
    if trab.tipo not in ("patron", "fijo"):
        return 0.0
    objetivo = datos.config.horas_objetivo * trab.factor_jornada
    return max(0.0, horas_prescritas(datos, w) - objetivo)


def _disp_dem_semanas(datos: Datos) -> tuple[dict[tuple[int, int], int], dict[tuple[int, int], int]]:
    """Crudos de cada semana ISO: disponibles (día-persona) y demanda (turnos·día). Separados de
    `peso_semanas` porque `repartir` necesita el numerador CRUDO para poder descontarlo en vivo —
    ver la nota de `disp_restante` más abajo."""
    disp: dict[tuple[int, int], int] = defaultdict(int)
    dem: dict[tuple[int, int], int] = defaultdict(int)
    for f in datos.fechas:
        sem = semana(f)
        disp[sem] += sum(1 for w in datos.trabajadores if datos.disponible(w, f))
        dem[sem] += sum(t.dem for t in datos.turnos.values()
                        if t.prioridad >= 1 and datos.opera(t.id, f))
    return dict(disp), dict(dem)


def peso_semanas(datos: Datos) -> dict[tuple[int, int], float]:
    """Capacidad residual de cada semana ISO: disponibles ÷ turnos demandados.

    Los días NO son intercambiables. En agosto la demanda es la misma pero hay mucha menos gente,
    así que soltar ahí una libranza abre un hueco justo donde menos capacidad hay para taparlo. Un
    reparto uniforme lo haría; este reparto va a las semanas de mayor peso.

    Esta es la foto FIJA, útil como referencia y para quien solo quiera consultarla (la usa el test
    para comprobar que agosto pesa menos que marzo). `repartir` usa la versión CRUDA
    (`_disp_dem_semanas`) porque necesita actualizarla en vivo — ver más abajo."""
    disp, dem = _disp_dem_semanas(datos)
    return {sem: disp[sem] / dem[sem] if dem[sem] else 0.0 for sem in sorted(disp)}


def _dias_prescritos(datos: Datos, w: str, sem: tuple[int, int]) -> list[date]:
    """Días de esa semana ISO en que la rotación le prescribe turno y está disponible."""
    return [f for f in datos.fechas
            if semana(f) == sem and datos.disponible(w, f) and turno_prescrito(datos, w, f)]


def _dias_de_semana(datos: Datos, sem: tuple[int, int]) -> list[date]:
    return [f for f in datos.fechas if semana(f) == sem]


def _sustituibilidad(datos: Datos, w: str, f: date, s: str) -> int:
    """Cuántos OTROS trabajadores podrían cubrir la línea `s` el día `f` si `w` la cede.

    Cuantos más, más barato es ceder ahí sin dejar la línea casi huérfana. Es una foto ESTÁTICA
    (capacidad declarada en `capacidades.csv`, no lo que el plan ya lleva decidido — al ejecutarse
    este paso el plan todavía está casi vacío, así que no hay nada más que consultar), pero basta
    para lo que hace falta: distinguir un día donde `w` es de los pocos que pueden hacer esa línea
    de un día donde hay una veintena de alternativas."""
    return sum(1 for otro in datos.trabajadores
               if otro != w and datos.disponible(otro, f) and datos.elegible(otro, s, f)[0])


def repartir(datos: Datos, plan: Plan) -> None:
    """Marca en `plan` los días que cada trabajador de patrón o fijo cede por exceso de jornada.

    Reparte en tres niveles, cada uno corrigiendo un defecto medido en la primera versión:

    1. SEMANA — dinámica, no una foto fija. `orden_sem` se recalculaba UNA vez para todo el reparto,
       así que cientos de trabajadores distintos consultaban la misma foto y convergían en las
       mismas semanas «buenas» sin saber que los demás hacían lo mismo. Medido: el 94,5 % de las
       473 cesiones caía en solo 10 de las 53 semanas del año (63 en la semana del 30 de marzo).
       Aquí `disp_restante` se descuenta según se coloca cada cesión, y el ranking de semanas se
       recalcula para CADA trabajador, reflejando lo que los anteriores ya se han llevado.
    2. DÍA DE LA SEMANA — dentro de la semana elegida, el día menos usado hasta ahora
       (`cesiones_por_dow`), no el primero cronológico. Mismo mecanismo que (1), un nivel más fino:
       sin esto casi todo el mundo caía en lunes (277 de 602 cesiones, un patrón L-V empieza casi
       siempre en lunes).
    3. LÍNEA — entre los días candidatos ya filtrados por (1) y (2), el que deja más sustitutos
       (`_sustituibilidad`). Ceder un día donde otras 20 personas podrían cubrir la línea es barato;
       ceder el único día en que casi nadie más puede es caro, aunque la semana y el día de la
       semana parezcan buenos en agregado."""
    disp, dem = _disp_dem_semanas(datos)
    disp_restante = dict(disp)     # se descuenta en vivo — ver punto 1 del docstring
    cesiones_por_dow: dict[int, int] = defaultdict(int)

    for w in sorted(datos.trabajadores):
        trab = datos.trabajadores[w]
        if trab.tipo not in ("patron", "fijo"):
            continue
        pendiente = exceso_h(datos, w)
        if pendiente <= 0:
            continue
        bloque = es_bloque(datos, trab.patron) if trab.tipo == "patron" else False

        # Ranking de semanas RECALCULADO para este trabajador, con lo que los anteriores ya se han
        # llevado descontado de `disp_restante`. Es lo que evita que todos consulten la misma foto.
        def peso_efectivo(sem: tuple[int, int]) -> float:
            d = dem.get(sem, 0)
            return disp_restante.get(sem, 0) / d if d else 0.0
        orden_sem = sorted(dem, key=lambda s: (-peso_efectivo(s), s))

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
                cedidos = 0
                for f in _dias_de_semana(datos, sem):
                    if datos.disponible(w, f) and not plan.ocupado(w, f):
                        plan.ceder(w, f, PASO, f"cesion de fila entera (semana {sem[1]}, "
                                                f"binomio {trab.patron})")
                        cedidos += 1
                disp_restante[sem] = disp_restante.get(sem, 0) - cedidos
                pendiente -= horas
            else:
                # Días sueltos: uno por semana como mucho, para no vaciar una semana entera. Antes,
                # si `dias[0]` estaba ocupado se abandonaba la semana entera sin probar otro día
                # suyo; ahora se prueban todos los candidatos disponibles.
                candidatos = [f for f in dias if not plan.ocupado(w, f)]
                if not candidatos:
                    continue
                # El día que deja MÁS SUSTITUTOS gana; a igualdad, el día de la semana MENOS usado
                # hasta ahora; a igualdad de los dos, la fecha (determinismo).
                candidatos.sort(key=lambda f: (
                    -_sustituibilidad(datos, w, f, turno_prescrito(datos, w, f)),
                    cesiones_por_dow[f.weekday()], f))
                f = candidatos[0]
                h = datos.turnos[turno_prescrito(datos, w, f)].horas
                sust = _sustituibilidad(datos, w, f, turno_prescrito(datos, w, f))
                plan.ceder(w, f, PASO, f"exceso de jornada ({pendiente:.0f} h pendientes, "
                                       f"semana {sem[1]}, {sust} sustitutos posibles ese dia)")
                cesiones_por_dow[f.weekday()] += 1
                disp_restante[sem] = disp_restante.get(sem, 0) - 1
                pendiente -= h

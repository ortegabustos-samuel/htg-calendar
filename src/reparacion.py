"""PASO 5 — el único autorizado a DESHACER.

Los pasos 1 a 4 son una cascada de compromisos: cada uno decide con lo que sabe y no puede prever
que su decisión dejará sin salida a un paso posterior. Este paso es el que arregla eso, y es lo que
permite que el procedimiento no necesite backtracking global.

Sigue siendo explicable porque el ORDEN DE INTENTOS está escrito y pactado: para cada hueco se
prueban los movimientos de menos a más invasivo y se para en el primero que funciona. «Probé esto,
luego esto otro, y lo que funcionó fue lo tercero» es una frase que se dice en una reunión."""
from __future__ import annotations

from datetime import date

from calendario import semana, turno_prescrito
from cargar_datos import Datos
from deuda import Deuda
from legal import Legal
from plan import Plan

PASO = "reparacion"


def huecos_reales(datos: Datos, plan: Plan) -> list[tuple[date, str]]:
    """(día, línea) de prioridad ≥ 1 que siguen sin cubrir, de más crítico a menos.

    Se recalcula del plan y no se lee de `plan.huecos`, porque esa lista es un registro histórico
    de intentos fallidos y puede contener huecos que un paso posterior ya tapó."""
    faltan: list[tuple[date, str]] = []
    for f in datos.fechas:
        for s in sorted(datos.turnos):
            t = datos.turnos[s]
            if t.prioridad < 1 or not datos.opera(s, f):
                continue
            if plan.cubierto(f, s) < t.dem:
                faltan.append((f, s))
    return sorted(faltan, key=lambda x: (-datos.turnos[x[1]].prioridad, x[0], x[1]))


def _libres_para(datos: Datos, plan: Plan, ley: Legal, f: date, s: str) -> list[str]:
    return [w for w in sorted(datos.trabajadores)
            if not plan.ocupado(w, f) and datos.elegible(w, s, f)[0]
            and ley.puede(plan, w, f, s)[0]]


def mov_cambiar_cubridor(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                         f: date, s: str) -> bool:
    """MOVIMIENTO 1 — ¿hay alguien libre ese día que simplemente pueda hacerlo?"""
    libres = _libres_para(datos, plan, ley, f, s)
    if not libres:
        return False
    w = dd.orden(plan, libres, f, s)[0]
    plan.asignar(w, f, s, PASO, f"reparacion mov.1: quedaba libre y podia cubrir {s}")
    return True


def _paso_de_cesion(plan: Plan, w: str, f: date) -> str | None:
    """Paso que dejó a `w` cedido el día `f`, o None si ese día no está cedido ahora mismo.

    Mira la última entrada del libro sobre `(w, f)`: una cesión puede haberse liberado y vuelto a
    ceder, así que solo la más reciente cuenta."""
    for d in reversed(plan.libro):
        if d.trabajador == w and d.fecha == f:
            return d.paso if d.turno is None and not d.liberado else None
    return None


def mov_mover_libranza(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                       f: date, s: str) -> bool:
    """MOVIMIENTO 2 — alguien que CEDIÓ ese día (por el paso 1, NUNCA por adopción de plaza) y cuya
    rotación prescribía justo esta línea.

    Se le devuelve el día y se le cede otro de la misma semana, para que su cómputo anual no cambie.
    Es el movimiento que corrige un error del paso 1: haber soltado la libranza en mal sitio.

    EL DESTINO SOLO PUEDE SER UN COMODÍN (prioridad 0). Aceptar cualquier día de prioridad ≤ 1
    —como hacía la primera versión— movía la cesión encima de un turno normal y abría un hueco
    real ahí: medido, el 100 % de los "éxitos" de esa versión eran de suma cero.

    NUNCA toca una cesión heredada por adopción de plaza (paso "criticos"): son los descansos que
    vienen con la plaza, y esa regla es innegociable."""
    for w in sorted(datos.trabajadores):
        if not plan.cedido(w, f) or turno_prescrito(datos, w, f) != s:
            continue
        if _paso_de_cesion(plan, w, f) == "criticos":
            continue                     # descanso heredado por adopción: intocable
        # ¿hay otro día de su semana con un COMODÍN al que mover la cesión?
        sem = semana(f)
        alternativos = [d for d in datos.fechas
                        if semana(d) == sem and d != f
                        and plan.turno_de(w, d) is not None
                        and datos.turnos[plan.turno_de(w, d)].prioridad == 0]
        if not alternativos:
            continue
        plan.liberar(w, f, PASO, "reparacion mov.2: recupera el dia cedido")
        if not ley.puede(plan, w, f, s)[0]:
            plan.ceder(w, f, PASO, "reparacion mov.2 revertido: no era legal")
            continue
        destino = alternativos[0]
        plan.liberar(w, destino, PASO, "reparacion mov.2: la cesion se muda aqui")
        plan.ceder(w, destino, PASO, f"cesion movida desde el {f} para cubrir {s}")
        plan.asignar(w, f, s, PASO, f"reparacion mov.2: recupera el dia cedido y cubre {s}")
        return True
    return False


def mov_intercambiar_semana(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                            f: date, s: str) -> bool:
    """MOVIMIENTO 3 — cambia el turno de ese día con otro compatible, liberando a quien sí puede.

    Recicla la idea de `pulido.pulir` de la rama base: mover trabajo entre dos personas compatibles
    cuando la asignación directa no cabe. Busca directamente a alguien OCUPADO ese día con algo
    menos crítico que también sea elegible para `s`.

    OJO: la primera versión envolvía esto en un `for w in sorted(...)` que comprobaba si `w` estaba
    libre y era elegible para `s` — pero el cuerpo nunca usaba `w`; quien cubre de verdad es `v`,
    del bucle interior. Esa comprobación era una puerta arbitraria que bloqueaba el intento en el
    93 % de las llamadas. Quitarla: 9 → 19 éxitos, 10 huecos menos."""
    for v in sorted(datos.trabajadores):
        actual = plan.turno_de(v, f)
        if actual is None or datos.turnos[actual].prioridad >= datos.turnos[s].prioridad:
            continue
        if not datos.elegible(v, s, f)[0]:
            continue
        plan.liberar(v, f, PASO, f"reparacion mov.3: suelta {actual} para cubrir {s}")
        if not ley.puede(plan, v, f, s)[0]:
            plan.asignar(v, f, actual, PASO, "reparacion mov.3 revertido")
            continue
        plan.asignar(v, f, s, PASO, f"reparacion mov.3: cambia {actual} por {s}, mas critico")
        relevo = _libres_para(datos, plan, ley, f, actual)
        if relevo:
            plan.asignar(dd.orden(plan, relevo, f, actual)[0], f, actual, PASO,
                         f"reparacion mov.3: releva a {v} en {actual}")
        return True
    return False


def mov_canjear_refcal(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                       f: date, s: str) -> bool:
    """MOVIMIENTO 4 — suelta un REF CAL para liberar el día y cubrir `s`.

    Un relleno de prioridad 0 siempre vale menos que un turno real, así que se canjea sin dudarlo.
    Recicla `pulido.canjear`: la rama base no podía ver un REF CAL de enero y un hueco de agosto en
    la misma ventana; aquí el año entero está delante.

    PRIORIDAD A LA MISMA SEMANA ISO QUE EL HUECO. `Legal` valida topes semanales, no horas
    anuales, así que soltar el REF CAL de marzo no ayuda a que `w` pueda cubrir un hueco de agosto:
    coger siempre el primero cronológico (`suyos[0]`, la primera versión) dejó el movimiento
    inerte — 0 éxitos en 808 intentos sobre el dataset real."""
    comodines = [s2 for s2, t in datos.turnos.items() if t.prioridad == 0]
    if not comodines:
        return False
    sem = semana(f)
    for w in sorted(datos.trabajadores):
        if not datos.elegible(w, s, f)[0] or plan.ocupado(w, f):
            continue
        suyos = [d for d in plan.dias_de(w) if plan.turno_de(w, d) in comodines]
        if not suyos:
            continue
        suyos.sort(key=lambda d: (semana(d) != sem, d))    # su propia semana primero
        soltado = suyos[0]
        turno_soltado = plan.liberar(w, soltado, PASO,
                                     f"reparacion mov.4: suelta el REF CAL del {soltado}")
        if ley.puede(plan, w, f, s)[0]:
            plan.asignar(w, f, s, PASO,
                         f"reparacion mov.4: canjea el REF CAL del {soltado} por {s}")
            return True
        plan.asignar(w, soltado, turno_soltado, PASO, "reparacion mov.4 revertido")
    return False


MOVIMIENTOS = (mov_cambiar_cubridor, mov_mover_libranza,
               mov_intercambiar_semana, mov_canjear_refcal)


def reparar(datos: Datos, plan: Plan, ley: Legal, dd: Deuda, vueltas: int = 10) -> int:
    """Cierra huecos hasta punto fijo. Devuelve cuántos ha cerrado EN NETO.

    EL PUNTO FIJO SE MIDE EN HUECOS, NO EN MOVIMIENTOS CON ÉXITO. Un movimiento que tapa un hueco
    abriendo otro devuelve True sin mejorar nada; si la parada mirase ese contador —como la primera
    versión, que contaba `cerrados`— el bucle no converge nunca. Medido con el criterio equivocado:
    8 de las 10 vueltas daban exactamente el mismo número de "éxitos" sin mover el número de huecos
    ni una vez — puro ruido. Contar huecos antes/después de la vuelta sí detecta la convergencia
    real."""
    inicial = len(huecos_reales(datos, plan))
    antes = inicial
    for _ in range(vueltas):
        for f, s in huecos_reales(datos, plan):
            if plan.cubierto(f, s) >= datos.turnos[s].dem:
                continue
            for mov in MOVIMIENTOS:
                if mov(datos, plan, ley, dd, f, s):
                    break
        ahora = len(huecos_reales(datos, plan))
        if ahora == antes:
            break            # ya no mejora
        antes = ahora
    return inicial - len(huecos_reales(datos, plan))

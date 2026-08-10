"""PASO 2 — cubre las líneas críticas, con el calendario aún casi vacío.

Son las que no tienen grados de libertad: cuatro de las cinco tienen UN SOLO cubridor posible en
este dataset. Decidirlas antes que nada es lo único que evita llegar al final y descubrir que la
única persona que podía hacerlas ya está ocupada — que es de donde salió toda la infactibilidad de
la rama base.

Regla dura pactada: LA COBERTURA CRÍTICA GANA AL PATRÓN PROPIO DEL CUBRIDOR. Si tu línea crítica se
queda sin nadie, dejas tu patrón. En la rama base esto era un precio que había que calibrar entre
dos constantes; aquí es una prelación explícita que se dice en una frase."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from calendario import semana, turno_prescrito
from cargar_datos import Datos
from legal import Legal
from plan import Plan

PASO = "criticos"


def principales(datos: Datos) -> dict[str, list[str]]:
    """{línea → cubridores designados, del principal al último suplente}.

    El gestor designa un principal (v=1) y suplentes (v≥2). Los cubridores pueden estar CRUZADOS
    —cada uno principal de una línea y suplente de la otra—, así que el orden es por LÍNEA, nunca
    por persona."""
    orden: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (w, s), cap in sorted(datos.capacidades.items()):
        if cap.v >= 1:
            orden[s].append((cap.v, w))
    return {s: [w for _, w in sorted(pares)] for s, pares in sorted(orden.items())}


def lineas_criticas(datos: Datos) -> list[str]:
    """Líneas de prioridad ≥ 2, de más crítica a menos. Desempate por id para determinismo."""
    return sorted((s for s, t in datos.turnos.items() if t.prioridad >= 2),
                  key=lambda s: (-datos.turnos[s].prioridad, s))


def _hace_otra_critica(datos: Datos, plan: Plan, w: str, f: date, prio: int) -> bool:
    """Escape: ese día ya hace otra crítica de prioridad MAYOR O IGUAL. No se le puede pedir más."""
    s = plan.turno_de(w, f)
    return s is not None and datos.turnos[s].prioridad >= prio


def cubrir(datos: Datos, plan: Plan, ley: Legal) -> None:
    """Asigna cubridor a cada día de línea crítica que su titular no puede hacer."""
    orden = principales(datos)
    # (cubridor, semana ISO) que ya tiene una fila adoptada. Las dos filas de un binomio son
    # COMPLEMENTARIAS: darle las dos a la misma persona en la misma semana es lunes a domingo sin
    # un solo descanso. Es lo que volvía infactibles junio, agosto, septiembre y noviembre.
    adoptada: set[tuple[str, tuple[int, int]]] = set()

    for linea in lineas_criticas(datos):
        prio = datos.turnos[linea].prioridad
        cubridores = orden.get(linea, [])
        if not cubridores:
            continue

        for f in datos.fechas:
            if not datos.opera(linea, f):
                continue
            if plan.cubierto(f, linea) >= datos.turnos[linea].dem:
                continue
            # ¿Hay titular que la haga por su patrón y no la haya cedido?
            titulares = [w for w in sorted(datos.trabajadores)
                         if turno_prescrito(datos, w, f) == linea
                         and datos.disponible(w, f) and not plan.cedido(w, f)]
            if titulares:
                continue                        # el paso 3 la estampará

            descartados: list[str] = []
            for w in cubridores:
                sem = semana(f)
                if (w, sem) in adoptada:
                    descartados.append(f"{w}: ya adopta otra fila esta semana")
                    continue
                if _hace_otra_critica(datos, plan, w, f, prio):
                    descartados.append(f"{w}: ya hace {plan.turno_de(w, f)}, igual o mas critica")
                    continue
                ok, motivo = ley.puede(plan, w, f, linea)
                if not ok:
                    descartados.append(f"{w}: {motivo}")
                    continue
                # La cobertura crítica GANA al patrón propio: si su rotación le prescribía otra
                # cosa, la suelta. Es la prelación pactada.
                propio = turno_prescrito(datos, w, f)
                regla = (f"cobertura critica (prio {prio}); deja su {propio}"
                         if propio else f"cobertura critica (prio {prio})")
                plan.asignar(w, f, linea, PASO, regla, tuple(descartados))
                adoptada.add((w, sem))
                break
            else:
                motivo = ("ningun cubridor designado puede: " + "; ".join(descartados)
                          if descartados else "sin cubridores designados")
                plan.hueco(f, linea, motivo)

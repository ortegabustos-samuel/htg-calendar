"""
ritmo.py — El pulso de trabajo y descanso que el esqueleto le marca a cada trabajador.

Existe para responder a una pregunta concreta del paso B: cuando hay que quitarle horas a alguien,
¿se le quita un día suelto o el bloque entero? No hay regla escrita — la empresa lo cuadraba a ojo,
caso por caso — pero el patrón sí lleva la respuesta dentro, en el descanso que devuelve por cada
día trabajado:

    ratio = descanso modal / trabajo modal

Un patrón que devuelve un día de descanso por cada día trabajado (ratio 1,00, como el binomio de
noche: 7 días de trabajo y 7 de descanso) está diciendo que ese bloque es indivisible — el descanso
que se debe por él es tan grande como el propio bloque, así que ceder un día suelto deja al
cubridor con un turno cuyo descanso nadie sabe medir. Un patrón de semana laboral normal devuelve
0,20-0,40 y se puede trocear sin que eso signifique nada raro.

Medido sobre Valladolid 2026 la separación es limpia: VAL_NOCHES y UVI dan 1,00, y todo lo demás
0,20-0,40. El corte está en `config.ratio_rigido`.

El ritmo se mide por GRUPO (el patrón, o el tipo de trabajador si no tiene patrón) y no por
persona: las vacaciones parten bloques y ensucian la medida individual, mientras que la moda del
grupo es estable.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from cargar_datos import Datos

Plan = dict[tuple[str, date], str]


@dataclass(frozen=True)
class Ritmo:
    """El pulso de un grupo: cuánto trabaja seguido, cuánto descansa después, y qué proporción."""
    grupo: str
    trabajo: int             # longitud más frecuente del bloque de trabajo (días)
    descanso: int            # longitud más frecuente del descanso que le sigue (días)
    regularidad: float       # fracción de bloques que tienen la longitud modal
    ratio: float             # descanso / trabajo
    rigido: bool             # ratio >= config.ratio_rigido: el bloque no se fracciona


def bloques(plan: Plan, trab: str) -> list[list[date]]:
    """Rachas de días trabajados seguidos que el plan le asigna, en orden."""
    fechas = sorted(f for (w, f) in plan if w == trab)
    racha: list[list[date]] = []
    actual: list[date] = []
    for f in fechas:
        if actual and (f - actual[-1]).days > 1:
            racha.append(actual)
            actual = []
        actual.append(f)
    if actual:
        racha.append(actual)
    return racha


def grupo_de(datos: Datos, trab: str) -> str:
    t = datos.trabajadores[trab]
    return t.patron if t.tipo == "patron" and t.patron else t.tipo


def medir(datos: Datos, plan: Plan) -> dict[str, Ritmo]:
    """Ritmo de cada grupo, medido sobre el plan que se le pase."""
    trabajo: dict[str, Counter] = defaultdict(Counter)
    descanso: dict[str, Counter] = defaultdict(Counter)
    for trab in datos.trabajadores:
        g = grupo_de(datos, trab)
        bs = bloques(plan, trab)
        for b in bs:
            trabajo[g][len(b)] += 1
        for previo, siguiente in zip(bs, bs[1:]):
            descanso[g][(siguiente[0] - previo[-1]).days - 1] += 1

    ritmos: dict[str, Ritmo] = {}
    for g, cuenta_trabajo in trabajo.items():
        if not cuenta_trabajo:
            continue
        modal_trabajo, n_trabajo = cuenta_trabajo.most_common(1)[0]
        cuenta_descanso = descanso[g]
        modal_descanso = cuenta_descanso.most_common(1)[0][0] if cuenta_descanso else 0
        ratio = modal_descanso / modal_trabajo if modal_trabajo else 0.0
        ritmos[g] = Ritmo(
            grupo=g,
            trabajo=modal_trabajo,
            descanso=modal_descanso,
            regularidad=n_trabajo / sum(cuenta_trabajo.values()),
            ratio=ratio,
            rigido=ratio >= datos.config.ratio_rigido,
        )
    return ritmos


def resumen(ritmos: dict[str, Ritmo]) -> None:
    print(f"\n{'grupo':<18} {'trabajo':>8} {'descanso':>9} {'regular':>8} {'ratio':>6}  unidad de cesión")
    print("-" * 74)
    for g, r in sorted(ritmos.items()):
        unidad = "CICLO entero (no se fracciona)" if r.rigido else "bloque o día suelto"
        print(f"{g:<18} {str(r.trabajo) + 'd':>8} {str(r.descanso) + 'd':>9} "
              f"{r.regularidad:>8.0%} {r.ratio:>6.2f}  {unidad}")
    print("-" * 74)

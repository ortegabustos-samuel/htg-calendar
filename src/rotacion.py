"""PASO 3 — estampa la rotación y decide quién adopta las filas que quedaron huérfanas.

Los patrones son ENTRADA CONOCIDA desde el minuto cero, no una decisión: el paso 1 calcula el
exceso a partir de ellos y el paso 2 elige cubridores que son gente de patrón. Así que lo que este
paso decide de verdad no es «aplicar el patrón» —eso es mecánico— sino QUIÉN SE QUEDA LAS FILAS que
los pasos 1 y 2 dejaron libres.

Se ofrecen primero a otro trabajador de patrón: adoptar mantiene la plaza con sus descansos y no
gasta pool, que es el recurso escaso (17 personas para el 53 % de los huecos estructurales)."""
from __future__ import annotations

from datetime import date

from calendario import semana, turno_prescrito
from cargar_datos import Datos
from deuda import Deuda
from legal import Legal
from plan import Plan

PASO = "rotacion"


def estampar(datos: Datos, plan: Plan, ley: Legal) -> None:
    """Pone a cada trabajador de patrón y a cada fijo lo que su rotación prescribe.

    Salta los días que los pasos 1 (cesión) y 2 (cobertura crítica) ya decidieron: `Plan.ocupado`
    devuelve True para ambos, así que la precedencia sale sola."""
    for w in sorted(datos.trabajadores):
        if datos.trabajadores[w].tipo not in ("patron", "fijo"):
            continue
        for f in datos.fechas:
            if plan.ocupado(w, f) or not datos.disponible(w, f):
                continue
            s = turno_prescrito(datos, w, f)
            if not s:
                continue
            if plan.cubierto(f, s) >= datos.turnos[s].dem:
                continue
            ok, motivo = ley.puede(plan, w, f, s)
            if not ok:
                plan.hueco(f, s, f"su titular {w} no puede: {motivo}")
                continue
            plan.asignar(w, f, s, PASO, "le toca por su rotacion")


def _huerfanas(datos: Datos, plan: Plan) -> list[tuple[date, str]]:
    """(día, línea) que la rotación prescribía y nadie está haciendo, ordenadas."""
    faltan: list[tuple[date, str]] = []
    for f in datos.fechas:
        for s in sorted(datos.turnos):
            t = datos.turnos[s]
            if t.prioridad < 1 or not datos.opera(s, f):
                continue
            if plan.cubierto(f, s) < t.dem:
                faltan.append((f, s))
    return faltan


def adoptar(datos: Datos, plan: Plan, ley: Legal, dd: Deuda) -> None:
    """Ofrece las filas huérfanas a OTRO trabajador de patrón antes de mandarlas al pool."""
    for f, s in _huerfanas(datos, plan):
        if plan.cubierto(f, s) >= datos.turnos[s].dem:
            continue
        candidatos = [w for w in sorted(datos.trabajadores)
                      if datos.trabajadores[w].tipo == "patron"
                      and not plan.ocupado(w, f)
                      and datos.elegible(w, s, f)[0]]
        if not candidatos:
            continue
        descartados: list[str] = []
        for w in dd.orden(plan, candidatos, f, s):
            ok, motivo = ley.puede(plan, w, f, s)
            if not ok:
                descartados.append(f"{w}: {motivo}")
                continue
            plan.asignar(w, f, s, PASO,
                         f"adopta la plaza de {s}, que su titular no puede hacer",
                         tuple(descartados))
            break

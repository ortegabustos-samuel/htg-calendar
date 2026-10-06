"""
zonas/valladolid/descansos.py — El descanso semanal (DS) de fijos y correturnos.

Se escribe al final, con el plan ya completo. Los modelos de fijos y correturnos ya han dejado el
sitio (el descanso semanal es restricción suya): aquí solo se coloca cada DS en su día.

Dos por semana ISO en los días libres que lo pueden ser (no un festivo de lunes a viernes; uno en
sábado o domingo que no se trabaja sí). Los DS y DO que ya trae el plan (ciclos de patrón
heredados) cuentan. Se elige el par seguido más cercano al fin de semana; si no hay par, los
últimos libres.

Los descansos por festivo trabajado (DF) no se marcan: se harán a mano. El resto de días libres
se queda como hueco.
"""
from __future__ import annotations

from datetime import date, timedelta

from dominio import Datos, Plan, TipoDescanso, TipoTrabajador
from zonas.valladolid.fijos import lunes_de


def libre(datos: Datos, plan: Plan, w: str, f: date) -> bool:
    """Si ese día puede llevar un DS: dentro del año, sin vacaciones, sin nada en el plan y que no
    sea un festivo de lunes a viernes."""
    festivo_lv = f.weekday() < 5 and datos.es_festivo(f, datos.trabajadores[w].base)
    return (datos.inicio <= f <= datos.fin and datos.disponible(w, f) and plan.hueco(w, f)
            and not festivo_lv)


def elegir_ds(libres: list[date], cupo: int) -> list[date]:
    """El par seguido más cercano al fin de semana si hacen falta dos; si no, los últimos libres."""
    if cupo <= 0:
        return []
    parejas = [(a, b) for a, b in zip(libres, libres[1:]) if (b - a).days == 1]
    if cupo >= 2 and parejas:
        return list(parejas[-1])
    return libres[-cupo:]


def senalar(datos: Datos, plan: Plan) -> list[str]:
    """Escribe los DS de fijos y correturnos. Devuelve lo que no ha podido colocar.

    En una semana partida por vacaciones o por el principio o el final del año no se exige el
    descanso semanal: se ponen hasta dos con los libres que haya, y no cuenta como fallo."""
    reserva = datos.zona.convenio.libres_semana
    semanas = sorted({lunes_de(f) for f in datos.lista_dias_calendario})
    gente = [w for w, t in datos.trabajadores.items()
             if t.tipo in (TipoTrabajador.FIJO, TipoTrabajador.CORRETURNO)]
    fallos = []
    for w in gente:
        for lunes in semanas:
            fechas = [lunes + timedelta(days=i) for i in range(7)]
            libres = [f for f in fechas if libre(datos, plan, w, f)]
            ya = sum(1 for f in fechas if plan.get(w, f) in (TipoDescanso.DS, TipoDescanso.DO))
            cupo = max(0, reserva - ya)
            partida = any(not (datos.inicio <= f <= datos.fin) or not datos.disponible(w, f) for f in fechas)
            ds = elegir_ds(libres, cupo)
            if len(ds) < cupo and not partida:
                fallos.append(f"{w} semana del {lunes:%d/%m}: {len(ds)} de {cupo} DS")
            for f in ds:
                plan.poner(w, f, TipoDescanso.DS)
    return fallos

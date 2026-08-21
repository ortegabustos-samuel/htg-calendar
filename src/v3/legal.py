"""
legal.py — Las restricciones básicas del convenio, definidas UNA sola vez.

Las consumen los dos lados del pipeline: las reglas, como test ("¿puedo darle este turno a esta
persona este día?"), y el CP-SAT del paso D, como restricción del modelo. De momento solo existe
la primera forma — la segunda se añadirá sobre estas mismas definiciones, no sobre una copia.

No es el catálogo completo de MODELO.md, y eso es deliberado: solo las tres que se decidieron
básicas, más las dos estructurales que salen gratis.

  C2  un turno por día    — gratis: el plan es un dict indexado por (trabajador, fecha)
  C3  cualificación       — gratis: `datos.elegible` ya cruza capacidades con operatividad
  C4  descanso mínimo entre turnos de días consecutivos    (config.descanso_minimo, 12 h)
  C5  máx. días trabajados por SEMANA ISO                  (config.dias_max_semana, 6)
  C6  máx. horas en cualquier ventana de 7 días            (config.horas_max_semana, 48)

C5 se mide por semana ISO (lunes a domingo) y no en ventana deslizante. Es una decisión tomada a
sabiendas: permite encadenar más de 6 días trabajados a caballo de dos semanas. C6 sí es
deslizante, así que es lo que acota esas rachas por el lado de las horas.

Este módulo no deriva nada de los datos por su cuenta: cuándo empieza y acaba un turno se lo
pregunta a `Datos`, que es donde viven las derivaciones.
"""
from __future__ import annotations

from datetime import date, timedelta

from v3.cargar_datos import Datos

Plan = dict[tuple[str, date], str]


def descanso_ok(datos: Datos, plan: Plan, trab: str, f: date, turno: str,
                exento_localizado: bool = False) -> bool:
    """C4 — entre el fin de un turno y el inicio del siguiente median al menos
    `descanso_minimo` horas. Basta mirar el día anterior y el siguiente: ningún turno dura más de
    24 h, así que no puede chocar con nada más lejano.

    `exento_localizado` levanta la regla cuando uno de los dos es una guardia de localización: no
    es presencia, así que ni exige descanso después ni lo consume antes. Va en FALSO por defecto a
    propósito — el descanso debe respetarse salvo que sea la única forma de cubrir una demanda que
    de otro modo quedaría vacía, y esa excepción se paga en el objetivo del paso D, no aquí.
    """
    minimo = timedelta(hours=datos.config.descanso_minimo)
    exento = exento_localizado and datos.localizado(turno)
    inicio, fin = datos.intervalo(turno, f)

    ayer = f - timedelta(days=1)
    previo = plan.get((trab, ayer))
    if (previo is not None and not (exento or (exento_localizado and datos.localizado(previo)))
            and inicio - datos.intervalo(previo, ayer)[1] < minimo):
        return False

    manana = f + timedelta(days=1)
    posterior = plan.get((trab, manana))
    if (posterior is not None and not (exento or (exento_localizado and datos.localizado(posterior)))
            and datos.intervalo(posterior, manana)[0] - fin < minimo):
        return False
    return True


def dias_semana_ok(datos: Datos, plan: Plan, trab: str, f: date) -> bool:
    """C5 — como mucho `dias_max_semana` días trabajados en la semana ISO en que cae `f`."""
    lunes = f - timedelta(days=f.weekday())
    trabajados = sum(1 for i in range(7) if (trab, lunes + timedelta(days=i)) in plan)
    return trabajados + 1 <= datos.config.dias_max_semana


def horas_7dias_ok(datos: Datos, plan: Plan, trab: str, f: date, turno: str) -> bool:
    """C6 — ninguna ventana de 7 días consecutivos que contenga a `f` puede superar
    `horas_max_semana` horas. Ventana deslizante, no semana ISO."""
    horas = {i: datos.turnos[plan[(trab, f + timedelta(days=i))]].horas
             for i in range(-6, 7) if (trab, f + timedelta(days=i)) in plan}
    nuevas = datos.turnos[turno].horas
    for arranque in range(-6, 1):
        ventana = sum(h for i, h in horas.items() if arranque <= i < arranque + 7)
        if ventana + nuevas > datos.config.horas_max_semana:
            return False
    return True


def permite(datos: Datos, plan: Plan, trab: str, f: date, turno: str,
            exento_localizado: bool = False) -> bool:
    """¿Es legal añadir este turno a este trabajador este día, sobre el plan tal y como está?"""
    if (trab, f) in plan:                                       # C2
        return False
    return (descanso_ok(datos, plan, trab, f, turno, exento_localizado)
            and dias_semana_ok(datos, plan, trab, f)
            and horas_7dias_ok(datos, plan, trab, f, turno))


def infracciones(datos: Datos, plan: Plan) -> list[str]:
    """Lo que incumple un plan ya terminado. No decide nada: es la comprobación que se imprime al
    cerrar cada paso, para no dar por bueno un cuadrante ilegal porque la cobertura salga bien."""
    fallos: list[str] = []
    por_trab: dict[str, list[date]] = {}
    for (w, f) in plan:
        por_trab.setdefault(w, []).append(f)

    minimo = timedelta(hours=datos.config.descanso_minimo)
    for w, fechas in sorted(por_trab.items()):
        fechas.sort()
        horas = {f: datos.turnos[plan[(w, f)]].horas for f in fechas}

        for f in fechas:                                        # C4
            sig = f + timedelta(days=1)
            if (w, sig) in plan:
                hueco = datos.intervalo(plan[(w, sig)], sig)[0] - datos.intervalo(plan[(w, f)], f)[1]
                if hueco < minimo:
                    # Se etiqueta aparte cuando hay una guardia de LOCALIZACIÓN de por medio: no es
                    # presencia sino disponibilidad, así que ni exige descanso después ni lo
                    # consume antes. Sigue apareciendo en el informe —el descanso debe respetarse
                    # salvo que sea la única forma de cubrir— pero no es el mismo incumplimiento.
                    loc = (datos.localizado(plan[(w, f)]) or datos.localizado(plan[(w, sig)]))
                    fallos.append(f"{'C4-loc' if loc else 'C4'} {w} {f:%d/%m}->{sig:%d/%m}: "
                                  f"{hueco.total_seconds() / 3600:.1f} h de descanso")

        semanas: dict[date, int] = {}                           # C5
        for f in fechas:
            lunes = f - timedelta(days=f.weekday())
            semanas[lunes] = semanas.get(lunes, 0) + 1
        for lunes, n in sorted(semanas.items()):
            if n > datos.config.dias_max_semana:
                fallos.append(f"C5 {w} semana del {lunes:%d/%m}: {n} días trabajados")

        for f in fechas:                                        # C6
            ventana = sum(h for g, h in horas.items() if 0 <= (g - f).days < 7)
            if ventana > datos.config.horas_max_semana:
                fallos.append(f"C6 {w} 7 días desde {f:%d/%m}: {ventana:.0f} h")
    return fallos

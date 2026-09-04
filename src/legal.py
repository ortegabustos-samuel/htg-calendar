"""
Las restricciones básicas del convenio, definidas UNA sola vez.

Las consumen los dos lados del pipeline: las reglas, como test ("¿puedo darle este turno a esta
persona este día?"), y el CP-SAT del paso D, como restricción del modelo. De momento solo existe
la primera forma — la segunda se añadirá sobre estas mismas definiciones, no sobre una copia.

No es el catálogo completo y eso es deliberado: solo las tres que se decidieron
básicas, más las dos estructurales que salen gratis.

  C4  descanso mínimo entre turnos de días consecutivos    (config.descanso_minimo, 12 h)
  C5  máx. días trabajados por SEMANA ISO                  (config.dias_max_semana, 6)
  C6  máx. horas trabajadas por SEMANA ISO                 (config.horas_max_semana, 48)

También vive aquí `domingo_ok` — nunca domingo suelto sin el sábado de ese fin de semana. No es
del convenio (no lleva número de artículo, es una regla de reparto), pero la consumen los mismos
sitios que C4/C5/C6, así que se define en el mismo lugar en vez de en uno propio.

También vive aquí `descanso_finde_ok` — si sábado y domingo se trabajan la misma semana, exige un
par de días consecutivos libres entre semana. Tampoco es del convenio, y a diferencia de
domingo_ok no se pliega en permite(): depende de la semana completa, no de un día contra el
anterior, así que se evalúa una vez decidida la semana (ver equidad.py, libranzas.py).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from cargar_datos import Datos

Plan = dict[tuple[str, date], str]

def descanso_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str,
                exento_localizado: bool = False) -> bool:
    """C4 — entre el fin de un turno y el inicio del siguiente median al menos
    `descanso_minimo` horas.

    `exento_localizado` levanta la regla cuando uno de los dos es una guardia de localización: no
    es presencia, así que ni exige descanso después ni lo consume antes. Va en FALSO por defecto a
    propósito — el descanso debe respetarse salvo que sea la única forma de cubrir una demanda que
    de otro modo quedaría vacía, y esa excepción se paga en el objetivo del paso D, no aquí.
    """
    minimo = timedelta(hours=datos.config.descanso_minimo)
    exento = exento_localizado and datos.localizado(turno_id)
    inicio, fin = datos.intervalo(turno_id, fecha)

    ayer = fecha - timedelta(days=1)
    previo = plan.get((trabajador_id, ayer))
    if (previo is not None and not (exento or (exento_localizado and datos.localizado(previo)))
            and inicio - datos.intervalo(previo, ayer)[1] < minimo):
        return False

    manana = fecha + timedelta(days=1)
    posterior = plan.get((trabajador_id, manana))
    if (posterior is not None and not (exento or (exento_localizado and datos.localizado(posterior)))
            and datos.intervalo(posterior, manana)[0] - fin < minimo):
        return False
    return True


def dias_semana_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date) -> bool:
    """C5 — como mucho `dias_max_semana` días trabajados en la semana ISO en que cae `fecha`."""
    lunes = fecha - timedelta(days=fecha.weekday())
    trabajados = sum(1 for i in range(7) if (trabajador_id, lunes + timedelta(days=i)) in plan)
    return trabajados + 1 <= datos.config.dias_max_semana


def horas_semana_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno: str) -> bool:
    """C6 — como mucho `horas_max_semana` horas trabajadas en la semana ISO en que cae `fecha`."""
    lunes = fecha - timedelta(days=fecha.weekday())
    horas = sum(datos.turnos[plan[(trabajador_id, lunes + timedelta(days=i))]].horas
                for i in range(7) if (trabajador_id, lunes + timedelta(days=i)) in plan)
    return horas + datos.turnos[turno].horas <= datos.config.horas_max_semana


def domingo_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """El domingo solo se trabaja si también se trabaja el sábado de ese mismo fin de semana.
    No es del convenio"""
    if datos.tipo_dia(fecha, datos.turnos[turno_id].municipio) != "DOM":
        return True
    return (trabajador_id, fecha - timedelta(days=1)) in plan


def descanso_finde_ok(datos: Datos, plan: Plan, trabajador_id: str, lunes: date) -> bool:
    """Si esa semana ISO (`lunes`..`lunes+6`) se trabajan sábado Y domingo, exige un par de días
    consecutivos libres entre semana. No es del convenio: es una regla de reparto, como
    domingo_ok. A diferencia de domingo_ok, no hace falta excepción de festivos: un festivo
    trabajado entre semana ocupa el día igual que un laborable."""
    dias = [lunes + timedelta(days=i) for i in range(7)]
    if (trabajador_id, dias[5]) not in plan or (trabajador_id, dias[6]) not in plan:
        return True
    libres = {d for d in dias[:5] if (trabajador_id, d) not in plan}
    return any(dias[i] in libres and dias[i + 1] in libres for i in range(4))


def permite(datos: Datos, plan: Plan, trabjador_id: str, fecha: date, turno_id: str,
            exento_localizado: bool = False) -> bool:
    """¿Es legal añadir este turno a este trabajador este día, sobre el plan tal y como está?"""
    if (trabjador_id, fecha) in plan:                                       # C2
        return False
    return (descanso_ok(datos, plan, trabjador_id, fecha, turno_id, exento_localizado)
            and dias_semana_ok(datos, plan, trabjador_id, fecha)
            and horas_semana_ok(datos, plan, trabjador_id, fecha, turno_id)
            and domingo_ok(datos, plan, trabjador_id, fecha, turno_id))



def formas(datos: Datos, plan: Plan, trabajador_id: str, desde: date, hasta: date) -> Counter:
    """(tipo, forma) -> nº de casos de ese trabajador en el tramo. La FORMA es la secuencia de
    turnos que provoca el incumplimiento, no la persona ni la fecha: es lo que permite decir si
    algo ya lo produce el patrón o se lo ha inventado el pipeline."""
    dias = {}
    f = desde - timedelta(days=8)
    fin = hasta + timedelta(days=8)
    while f <= fin:
        s = plan.get((trabajador_id, f))
        if s is not None:
            dias[f] = s
        f += timedelta(days=1)

    minimo = timedelta(hours=datos.config.descanso_minimo)
    salida: Counter = Counter()
    for f, s in dias.items():
        g = f + timedelta(days=1)
        if g in dias and datos.intervalo(dias[g], g)[0] - datos.intervalo(s, f)[1] < minimo:
            # Igual que en `infracciones`: se separa cuando hay una guardia de LOCALIZACIÓN de por
            # medio, que es disponibilidad y no presencia. Sigue contándose, pero no es lo mismo.
            loc = datos.localizado(s) or datos.localizado(dias[g])
            salida[("C4-loc" if loc else "C4", (s, dias[g]))] += 1

    semanas: dict[date, list[str]] = defaultdict(list)
    for f, s in dias.items():
        semanas[f - timedelta(days=f.weekday())].append(s)
    for turnos in semanas.values():
        if len(turnos) > datos.config.dias_max_semana:
            salida[("C5", len(turnos))] += 1
        if sum(datos.turnos[x].horas for x in turnos) > datos.config.horas_max_semana:
            salida[("C6", tuple(sorted(turnos)))] += 1
    return salida


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

        semanas: dict[date, list[date]] = {}                    # C5 + C6, misma semana ISO
        for f in fechas:
            lunes = f - timedelta(days=f.weekday())
            semanas.setdefault(lunes, []).append(f)
        for lunes, dias_semana in sorted(semanas.items()):
            if len(dias_semana) > datos.config.dias_max_semana:
                fallos.append(f"C5 {w} semana del {lunes:%d/%m}: {len(dias_semana)} días trabajados")
            total = sum(horas[f] for f in dias_semana)
            if total > datos.config.horas_max_semana:
                fallos.append(f"C6 {w} semana del {lunes:%d/%m}: {total:.0f} h")
    return fallos

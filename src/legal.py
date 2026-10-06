"""
legal.py — Las reglas del convenio, definidas UNA sola vez.

Son comunes a todas las zonas: aquí está la forma de cada regla y las cifras salen del convenio
de la zona (`datos.zona.convenio`). Las reglas de reparto propias de cada zona van en su fichero
(valladolid.py).

  C2  un turno al día
  C4  descanso mínimo entre turnos de días consecutivos
  C5  máx. días trabajados por SEMANA ISO
  C6  máx. horas trabajadas por SEMANA ISO

C5 y C6 se cuentan por semana ISO (lunes a domingo) en todas las zonas del convenio de CyL, y
permiten rachas por encima del tope a caballo de dos semanas, a sabiendas.

Se aplican donde el pipeline INVENTA una secuencia, nunca sobre un ciclo de patrón heredado.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from dominio import Datos, Franja, Plan


def descanso_entre(datos: Datos, hoy: str, manana: str) -> bool:
    """C4 — entre el fin de la línea `hoy` y el inicio de la línea `manana`, el día siguiente,
    median al menos `descanso_minimo` horas. Solo depende del reloj de las dos líneas.

    Las guardias LOCALIZADAS no entran: son disponibilidad, no presencia, así que ni exigen
    descanso después ni lo consumen antes.
    """
    if Franja.LOCALIZADO in (datos.turnos[hoy].franja, datos.turnos[manana].franja):
        return True
    ancla = date(2001, 1, 1)
    hueco = datos.intervalo(manana, ancla + timedelta(days=1))[0] - datos.intervalo(hoy, ancla)[1]
    return hueco >= timedelta(hours=datos.zona.convenio.descanso_minimo)


def descanso_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """C4 de ese turno ese día contra lo que ya tiene en el plan la víspera y el día después."""
    previo = plan.turno_de(trabajador_id, fecha - timedelta(days=1))
    posterior = plan.turno_de(trabajador_id, fecha + timedelta(days=1))
    return ((previo is None or descanso_entre(datos, previo, turno_id))
            and (posterior is None or descanso_entre(datos, turno_id, posterior)))


def dias_semana_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date) -> bool:
    """C5 — como mucho `dias_max_semana` días trabajados en la semana ISO en que cae `fecha`."""
    lunes = fecha - timedelta(days=fecha.weekday())
    trabajados = sum(1 for i in range(7)
                     if plan.turno_de(trabajador_id, lunes + timedelta(days=i)) is not None)
    return trabajados + 1 <= datos.zona.convenio.dias_max_semana


def horas_semana_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """C6 — como mucho `horas_max_semana` horas trabajadas en la semana ISO en que cae `fecha`.
    Se cuenta en minutos: el convenio da horas enteras y los turnos computan minutos."""
    lunes = fecha - timedelta(days=fecha.weekday())
    turnos_semana = (plan.turno_de(trabajador_id, lunes + timedelta(days=i)) for i in range(7))
    minutos = sum(datos.turnos[s].minutos_computo for s in turnos_semana if s is not None)
    return minutos + datos.turnos[turno_id].minutos_computo <= datos.zona.convenio.horas_max_semana * 60


def permite(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """¿Es legal añadir este turno a este trabajador este día, sobre el plan tal y como está?"""
    if not plan.hueco(trabajador_id, fecha):           # C2: ni encima de un turno ni de un descanso
        return False
    return (descanso_ok(datos, plan, trabajador_id, fecha, turno_id)
            and dias_semana_ok(datos, plan, trabajador_id, fecha)
            and horas_semana_ok(datos, plan, trabajador_id, fecha, turno_id))


def infracciones(datos: Datos, plan: Plan) -> list[str]:
    """Lo que incumple un plan ya terminado. No decide nada: es la comprobación que se imprime al
    cerrar cada paso, para no dar por bueno un cuadrante ilegal porque la cobertura salga bien."""
    convenio = datos.zona.convenio
    minimo = timedelta(hours=convenio.descanso_minimo)
    por_trabajador: dict[str, list[date]] = defaultdict(list)
    for (trabajador_id, fecha), _ in plan.items():
        if plan.turno_de(trabajador_id, fecha) is not None:
            por_trabajador[trabajador_id].append(fecha)

    fallos: list[str] = []
    for trabajador_id, fechas in sorted(por_trabajador.items()):
        fechas.sort()
        for fecha in fechas:                                                    # C4
            siguiente = fecha + timedelta(days=1)
            if plan.turno_de(trabajador_id, siguiente) is None:
                continue
            hoy, manana = plan.get(trabajador_id, fecha), plan.get(trabajador_id, siguiente)
            if Franja.LOCALIZADO in (datos.turnos[hoy].franja, datos.turnos[manana].franja):
                continue                                # las localizadas no entran en C4
            descanso = datos.intervalo(manana, siguiente)[0] - datos.intervalo(hoy, fecha)[1]
            if descanso < minimo:
                fallos.append(f"C4 {trabajador_id} {fecha:%d/%m}->{siguiente:%d/%m}: "
                              f"{descanso.total_seconds() / 3600:.1f} h de descanso")

        semanas: dict[date, list[date]] = defaultdict(list)                     # C5 y C6
        for fecha in fechas:
            semanas[fecha - timedelta(days=fecha.weekday())].append(fecha)
        for lunes, dias in sorted(semanas.items()):
            if len(dias) > convenio.dias_max_semana:
                fallos.append(f"C5 {trabajador_id} semana del {lunes:%d/%m}: {len(dias)} días trabajados")
            minutos = sum(datos.turnos[plan.get(trabajador_id, f)].minutos_computo for f in dias)
            if minutos > convenio.horas_max_semana * 60:
                fallos.append(f"C6 {trabajador_id} semana del {lunes:%d/%m}: {minutos / 60:.1f} h")
    return fallos

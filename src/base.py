"""
base.py — Paso A del pipeline: el cuadrante base, sin ninguna decisión libre.

Pinta lo que los datos YA prescriben, y nada más:

  * PATRÓN     — solo los reales (noches y UVI): la fila de `patrones.csv` que le toca esa semana.
                 La rotación avanza una fila por semana desde el LUNES ANCLA (el lunes de la
                 semana en que cae el 1 de enero) y cada trabajador arranca en la fila que declara
                 `fila_inicial` en trabajadores.csv — eso es lo que da continuidad con el
                 cuadrante del año anterior (ver `cargar_datos.offsets_patron`).
  * FIJO       — nada: los coloca el modelo de titulares (titulares.py).
  * CORRETURNO — nada. Cubre el residuo en el paso D.

Importante mencionar que dias como vacaciones o festivos ni siquiera se contemplan como disponibles  

Salida: el `plan` — dict {(id_trab, fecha) -> id_turno} — que es el objeto que se pasan entre sí
todas las etapas y el que consume la vista de Excel.
"""
from __future__ import annotations

from datetime import date
from cargar_datos import DESCANSOS, DIAS, Datos

def turno_patron(datos: Datos, trabajador_id: str, fecha: date) -> str:
    """Celda que la rotación prescribe al trabajador el día fecha: un id de turno, `LIBRE` o `DO`.
    Ojo: prescribir un turno no significa que se trabaje la línea
    puede no operar ese día y el trabajador puede estar de vacaciones."""

    trabajador = datos.trabajadores[trabajador_id]
    filas = datos.patrones.get(trabajador.patron)
    offset = datos.offsets.get(trabajador_id, 0)
    semanas = (fecha - datos.primer_lunes).days // 7
    fila = filas[(offset + semanas) % len(filas)]
    return fila[DIAS[fecha.weekday()]]


def descanso_prescrito(datos: Datos, trabajador_id: str, fecha: date) -> str | None:
    """`DO` si la rotación marca ese día como descanso CON ETIQUETA, None en cualquier otro caso
    (incluido el `LIBRE` de toda la vida, que no lleva etiqueta, y quien no tiene patrón).

    Se consulta desde dos sitios: la salida, para el descanso propio de cada trabajador, y
    `libranzas`, para que el cubridor que asume un bloque herede también su descanso etiquetado.
    """
    trabajador = datos.trabajadores[trabajador_id]
    if trabajador.tipo != "patron" or not trabajador.patron:
        return None
    celda = turno_patron(datos, trabajador_id, fecha)
    return celda if celda in DESCANSOS else None


def prescrito(datos: Datos, trabajador_id: str, fecha: date) -> str | None:
    """Plaza que le toca a este trabajador ese día, IGNORANDO si está disponible.

    Solo los patrones prescriben: es lo que le corresponde por rotación, siempre que la plaza exista
    ese día (la línea opera). Un fijo no tiene nada prescrito. Que esté de vacaciones no cambia lo que
    le tocaba, y por eso esto y `disponible` son preguntas separadas: juntas dicen qué plazas se
    quedan solas, que es lo que el paso B traspasa a un cubridor.
    """
    trabajador = datos.trabajadores[trabajador_id]
    if trabajador.tipo == "patron":
        turno_id = turno_patron(datos, trabajador_id, fecha)
        return turno_id if (turno_id in DESCANSOS or (turno_id in datos.turnos and datos.opera(turno_id, fecha))) else None
    return None


def construir(datos: Datos) -> dict[tuple[str, date], str]:
    """Plan base del año: {(id_trab, fecha) -> id_turno}. Solo pinta los patrones reales: los
    fijos los coloca el modelo de titulares (titulares.py)."""
    plan: dict[tuple[str, date], str] = {}
    for dia in datos.lista_dias_calendario:
        for trabajador_id, trabajador in datos.trabajadores.items():
            if trabajador.tipo != "patron" or not datos.disponible(trabajador_id, dia):
                continue                                  # vacaciones: ausencia, no asignación
            turno_id = prescrito(datos, trabajador_id, dia)
            if turno_id is not None:
                plan[(trabajador_id, dia)] = turno_id
    return plan

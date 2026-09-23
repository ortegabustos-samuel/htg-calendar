"""
base.py — Paso A del pipeline: el cuadrante base, sin ninguna decisión libre.

Pinta lo que los datos YA prescriben, y nada más:

  * PATRÓN     — la fila de `patrones.csv` que le toca esa semana. La rotación avanza una fila
                 por semana desde el LUNES ANCLA (el lunes de la semana en que cae el 1 de enero)
                 y cada trabajador arranca en la fila que declara `fila_inicial` en
                 trabajadores.csv — eso es lo que da continuidad con el cuadrante del año anterior
                 (ver `cargar_datos.offsets_patron`).
  * FIJO       — su `linea` titular, solo de lunes a viernes. Si además tiene capacidad de finde
                 declarada en capacidades.csv (el caso de los antiguos "mixtos"), ese fin de semana
                 no lo decide este paso: lo decide el paso D junto a correturno y patrón grande
                 (ver `residuo.py`), así que aquí ni se mira.
  * CORRETURNO — nada. Es el único pool rodante de verdad, y su semana la decide el paso C.

Importante mencionar que dias como vacaciones o festivos ni siquiera se contemplan como disponibles  

Salida: el `plan` — dict {(id_trab, fecha) -> id_turno} — que es el objeto que se pasan entre sí
todas las etapas y el que consume la vista de Excel.
"""
from __future__ import annotations

from datetime import date
from cargar_datos import DESCANSOS, DIAS, DO, LIBRE, Datos

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
    return celda if celda == DO else None


def prescrito(datos: Datos, trabajador_id: str, fecha: date) -> str | None:
    """Plaza que le toca a este trabajador ese día, IGNORANDO si está disponible.

    Es lo que le corresponde por rotación o por su línea fija, siempre que la plaza exista ese día
    (la línea opera y su capacidad cubre el tipo de día). Que esté de vacaciones no cambia lo que
    le tocaba, y por eso esto y `disponible` son preguntas separadas: juntas dicen qué plazas se
    quedan solas, que es lo que el paso B traspasa a un cubridor.
    """
    trabajador = datos.trabajadores[trabajador_id]
    if trabajador.tipo == "patron":
        turno_id = turno_patron(datos, trabajador_id, fecha)
        # DO se prescribe como tal: no es trabajo, pero OCUPA el día y tiene que llegar al final
        # sin que nadie lo edite. Devolverlo aquí es lo que hace que entre en el plan y que un
        # cubridor lo herede junto con los turnos del bloque, sin código especial en el traspaso.
        if turno_id == DO:
            return DO
        return turno_id if (turno_id != LIBRE and turno_id in datos.turnos and datos.opera(turno_id, fecha)) else None
    if trabajador.tipo == "fijo":
        if not datos.opera(trabajador.linea, fecha):
            return None
        dia = datos.tipo_dia(fecha, datos.turnos[trabajador.linea].municipio)
        if dia != "LV":
            return None
        cap = datos.capacidades.get((trabajador_id, trabajador.linea))
        return trabajador.linea if cap and cap.lv == 1 else None
    return None


def construir(datos: Datos) -> dict[tuple[str, date], str]:
    """Plan base del año: {(id_trab, fecha) -> id_turno}."""
    plan: dict[tuple[str, date], str] = {}
    for dia in datos.lista_dias_calendario:
        for trabajador_id in datos.trabajadores:
            if not datos.disponible(trabajador_id, dia):
                continue                                  # vacaciones: ausencia, no asignación
            turno_id = prescrito(datos, trabajador_id, dia)
            if turno_id is not None:
                plan[(trabajador_id, dia)] = turno_id
    return plan

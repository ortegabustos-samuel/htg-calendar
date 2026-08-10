"""Utilidades de fecha compartidas por todo el proyecto.

Vivían en `modelo.py` por acumulación histórica, no por diseño: no tienen nada que ver con el
solver y las consultan `salida`, `validar_datos` y los cinco pasos del pipeline."""
from __future__ import annotations

from datetime import date, timedelta

from cargar_datos import DIAS, LIBRE, Datos


def rango_fechas(inicio: date, fin: date) -> list[date]:
    """Lista de fechas [inicio, fin] inclusive."""
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


def semana(f: date) -> tuple[int, int]:
    """Clave (año, nº) de la semana ISO (lunes-domingo) a la que pertenece la fecha."""
    a, n, _ = f.isocalendar()
    return (a, n)


def fila_patron(datos: Datos, w: str, f: date) -> int | None:
    """Fila de `patrones.csv` que le toca a `w` el día `f`, o None si no es de patrón.

    El trabajador avanza una fila por semana desde el lunes del arranque; `datos.offsets` dice en
    cuál empieza (es lo que da continuidad entre años, ver `cargar_datos.offsets_patron`).

    OJO al ancla: es el lunes de la semana del 1 de enero, NO el 1 de enero. El año no empieza en
    lunes —2026 empieza en jueves—, así que anclar en `datos.inicio` haría que la rotación
    cambiase de fila los jueves y desplazaría el patrón entero a partir del 5 de enero. Es el
    mismo ancla que usa `modelo.py:702` y el que documenta CLAUDE.md."""
    trab = datos.trabajadores[w]
    if trab.tipo != "patron" or not trab.patron:
        return None
    filas = datos.patrones.get(trab.patron)
    if not filas:
        return None
    ancla = datos.inicio - timedelta(days=datos.inicio.weekday())   # lunes de la semana ancla
    return (datos.offsets[w] + (f - ancla).days // 7) % len(filas)


def turno_prescrito(datos: Datos, w: str, f: date) -> str | None:
    """Turno que la ROTACIÓN (patrón) o la LÍNEA (fijo) prescribe a `w` el día `f`.

    None si ese día su patrón dice LIBRE, si el turno no opera esa fecha, o si el trabajador no es
    ni de patrón ni fijo (los mixtos y correturnos no tienen nada prescrito: son el pool)."""
    trab = datos.trabajadores[w]
    if trab.tipo == "fijo":
        if not trab.linea or not datos.opera(trab.linea, f):
            return None
        return trab.linea
    fila = fila_patron(datos, w, f)
    if fila is None:
        return None
    s = datos.patrones[trab.patron][fila].get(DIAS[f.weekday()])
    if not s or s == LIBRE or s not in datos.turnos or not datos.opera(s, f):
        return None
    return s

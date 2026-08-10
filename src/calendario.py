"""Utilidades de fecha compartidas por todo el proyecto.

Vivían en `modelo.py` por acumulación histórica, no por diseño: no tienen nada que ver con el
solver y las consultan `salida`, `validar_datos` y los cinco pasos del pipeline."""
from __future__ import annotations

from datetime import date, timedelta


def rango_fechas(inicio: date, fin: date) -> list[date]:
    """Lista de fechas [inicio, fin] inclusive."""
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


def semana(f: date) -> tuple[int, int]:
    """Clave (año, nº) de la semana ISO (lunes-domingo) a la que pertenece la fecha."""
    a, n, _ = f.isocalendar()
    return (a, n)

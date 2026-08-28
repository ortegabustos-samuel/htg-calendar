"""
ritmo.py — Agrupación de trabajadores y bloques de trabajo consecutivos.

`grupo_de()` decide bajo qué grupo se declara cada trabajador (el patrón, o el tipo si no tiene
patrón). `es_rigido()` consulta si ese grupo está en `config.grupos_rigidos`: la plaza no se
fracciona al ceder, se cede el ciclo entero — es el caso del binomio de noche y UVI. Es una
declaración manual (convenio / acuerdo con la empresa, confirmada patrón a patrón), no algo que se
infiera del cuadrante: el descanso que arrastra un ciclo rígido se lee directo de la rotación del
patrón (`base.turno_patron`), no se mide aquí.

`bloques()` trocea el plan de un trabajador en rachas de días de trabajo consecutivos — lo que
sigue vivo AHORA MISMO en el plan (ya con vacaciones y cesiones anteriores descontadas), que es
distinto de lo que dice el patrón en crudo.
"""
from __future__ import annotations

from datetime import date

from cargar_datos import Datos

Plan = dict[tuple[str, date], str]


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


def es_rigido(datos: Datos, trab: str) -> bool:
    """La plaza de `trab` no se fracciona al ceder: se cede el ciclo entero, nunca un día suelto."""
    return grupo_de(datos, trab) in datos.config.grupos_rigidos

"""
horas.py — El libro de horas del generador.

Contador ÚNICO de horas trabajadas por persona, que consultan y actualizan todas las etapas: las
reglas antes de asignar, y el CP-SAT como cota superior.

El techo anual (`horas_objetivo` de config.toml, escalado por el `factor_jornada` de quien tenga
reducción de jornada) es un VETO ABSOLUTO: ninguna etapa lo pasa, en ningún caso.

En el paso A este libro solo CUENTA: se construye a partir del base y dice cuánto le prescribe
su patrón a cada uno. Ese número el exceso sobre el objetivo es exactamente lo que el paso B
tendrá que ceder en forma de libranzas.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

from cargar_datos import Datos

EPS = 1e-9          # las horas son floats leídos del CSV: comparar con holgura, no con ==


class LibroHoras:
    """Horas acumuladas por trabajador. `apunta`/`borra` lo mueven, `cabe` lo defiende."""

    def __init__(self, datos: Datos):
        self.datos = datos
        self._horas: dict[str, float] = {trabajador_id: 0.0 for trabajador_id in datos.trabajadores}

    @classmethod
    def desde_plan(cls, datos: Datos, plan: dict[tuple[str, date], str]) -> "LibroHoras":
        libro = cls(datos)
        for (trabajador_id, fecha), turno_id in plan.items():
            libro.apunta(trabajador_id, turno_id)
        return libro

    # -- consultas ---------------------------------------------------------- #
    def objetivo(self, trabajador_id: str) -> float:
        """Jornada anual de este trabajador: el objetivo del convenio por su factor de jornada."""
        return self.datos.config.horas_objetivo * self.datos.trabajadores[trabajador_id].factor_jornada

    def horas(self, trabajador_id: str) -> float:
        return self._horas[trabajador_id]

    def exceso(self, trabajador_id: str) -> float:
        """Horas por encima del objetivo. Positivo = tiene que ceder; negativo = le sobra sitio."""
        return self._horas[trabajador_id] - self.objetivo(trabajador_id)

    def cabe(self, trabajador_id: str, turno: str) -> bool:
        """¿Puede este trabajador asumir este turno sin pasarse del techo anual?"""
        return self._horas[trabajador_id] + self.datos.turnos[turno].horas <= self.objetivo(trabajador_id) + EPS

    # -- movimientos -------------------------------------------------------- #
    def apunta(self, trabajador_id: str, turno_id: str) -> None:
        self._horas[trabajador_id] += self.datos.turnos[turno_id].horas

    def borra(self, trabajador_id: str, turno_id: str) -> None:
        self._horas[trabajador_id] -= self.datos.turnos[turno_id].horas



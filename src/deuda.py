"""La equidad, llevada como una COLA: en cada asignación va el que menos lleva.

Sustituye al término P2 del optimizador anterior. Medido sobre el dataset real, la rotación de un
patrón ya reparte sola (8 h y 2 sábados de rango entre los 38 de PAT_GRANDE_VALL, con solo 1,37
ciclos al año), así que el 74 % de la plantilla llega equitativo gratis. Lo que hay que repartir
son las PERTURBACIONES —coberturas, adopciones, el pool entero—, y para eso basta con preguntar
siempre quién va más corto. Es lo que hace un jefe de tráfico con una tabla en la pared, converge
solo y se verifica a ojo.

La moneda es `Turno.horas` (legales). `horas_consumo` no interviene: el objetivo de 1776 es una
cifra de convenio y un convenio cuenta horas legales."""
from __future__ import annotations

from datetime import date

from cargar_datos import Datos
from metricas import METRICAS      # ("sabado", "domingo", "festivo") — una sola definicion
from plan import Plan


class Deuda:
    def __init__(self, datos: Datos) -> None:
        self.datos = datos

    def objetivo(self, w: str) -> float:
        """Horas anuales que le tocan, escaladas por su reducción de jornada."""
        return self.datos.config.horas_objetivo * self.datos.trabajadores[w].factor_jornada

    def horas(self, plan: Plan, w: str) -> float:
        return sum(self.datos.turnos[plan.turno_de(w, d)].horas for d in plan.dias_de(w))

    def _metrica_de(self, f: date, turno: str) -> str | None:
        muni = self.datos.turnos[turno].municipio
        td = self.datos.tipo_dia(f, muni)
        return {"SAB": "sabado", "DOM": "domingo", "FEST": "festivo"}.get(td)

    def cuenta(self, plan: Plan, w: str, metrica: str) -> int:
        return sum(1 for d in plan.dias_de(w)
                   if self._metrica_de(d, plan.turno_de(w, d)) == metrica)

    def orden(self, plan: Plan, candidatos: list[str], f: date, turno: str) -> list[str]:
        """Candidatos de MÁS deuda a menos. Desempate TOTAL para que el reparto sea determinista.

        Clave, en este orden: la métrica del día (si el turno cae en sábado/domingo/festivo, primero
        quien menos lleve de ESO), luego las horas que le faltan para su objetivo, y por último el
        id ascendente — que es lo que garantiza que dos ejecuciones den lo mismo."""
        metrica = self._metrica_de(f, turno)

        def clave(w: str) -> tuple:
            falta = self.objetivo(w) - self.horas(plan, w)
            de_metrica = self.cuenta(plan, w, metrica) if metrica else 0
            return (de_metrica, -falta, w)

        return sorted(candidatos, key=clave)

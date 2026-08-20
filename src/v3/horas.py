"""
horas.py — El libro de horas del pipeline v3.

Contador ÚNICO de horas trabajadas por persona, que consultan y actualizan todas las etapas: las
reglas antes de asignar, y el CP-SAT como cota superior. Nace de una lección concreta del intento
anterior (`solver-v2`): allí las etapas de cobertura asignaban turnos sin mirar ningún techo anual,
y la cifra de cobertura salía alta porque media plantilla acababa muy por encima de la jornada.
Cobertura sostenida sobrecargando gente, no repartiendo mejor. Por eso el techo vive en un único
sitio y no en cada regla.

El techo anual (`horas_objetivo` de config.toml, escalado por el `factor_jornada` de quien tenga
reducción de jornada) es un VETO ABSOLUTO: ninguna etapa lo pasa, en ningún caso.

En el paso A este libro solo CUENTA: se construye a partir del esqueleto y dice cuánto le prescribe
su patrón a cada uno. Ese número —el exceso sobre el objetivo— es exactamente lo que el paso B
tendrá que ceder en forma de libranzas.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

from v3.cargar_datos import Datos

EPS = 1e-9          # las horas son floats leídos del CSV: comparar con holgura, no con ==


class LibroHoras:
    """Horas acumuladas por trabajador. `apunta`/`borra` lo mueven, `cabe` lo defiende."""

    def __init__(self, datos: Datos):
        self.datos = datos
        self._horas: dict[str, float] = {w: 0.0 for w in datos.trabajadores}

    @classmethod
    def desde_plan(cls, datos: Datos, plan: dict[tuple[str, date], str]) -> "LibroHoras":
        libro = cls(datos)
        for (w, _), s in plan.items():
            libro.apunta(w, s)
        return libro

    # -- consultas ---------------------------------------------------------- #
    def objetivo(self, trab: str) -> float:
        """Jornada anual de este trabajador: el objetivo del convenio por su factor de jornada."""
        return self.datos.config.horas_objetivo * self.datos.trabajadores[trab].factor_jornada

    def horas(self, trab: str) -> float:
        return self._horas[trab]

    def exceso(self, trab: str) -> float:
        """Horas por encima del objetivo. Positivo = tiene que ceder; negativo = le sobra sitio."""
        return self._horas[trab] - self.objetivo(trab)

    def cabe(self, trab: str, turno: str) -> bool:
        """¿Puede este trabajador asumir este turno sin pasarse del techo anual?"""
        return self._horas[trab] + self.datos.turnos[turno].horas <= self.objetivo(trab) + EPS

    # -- movimientos -------------------------------------------------------- #
    def apunta(self, trab: str, turno: str) -> None:
        self._horas[trab] += self.datos.turnos[turno].horas

    def borra(self, trab: str, turno: str) -> None:
        self._horas[trab] -= self.datos.turnos[turno].horas


# --------------------------------------------------------------------------- #
#  Informe: lo que se mira para dar por bueno un paso
# --------------------------------------------------------------------------- #
def _grupo(datos: Datos, trab: str) -> str:
    """Etiqueta con la que se agrupa en el resumen: el patrón si lo tiene, si no el tipo."""
    t = datos.trabajadores[trab]
    return t.patron if t.tipo == "patron" and t.patron else t.tipo


def escribir_csv(datos: Datos, plan: dict[tuple[str, date], str], libro: LibroHoras,
                 ruta: Path) -> None:
    """Una fila por trabajador: días trabajados, horas, objetivo y exceso."""
    dias: dict[str, int] = defaultdict(int)
    for (w, _) in plan:
        dias[w] += 1

    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as fichero:
        escritor = csv.writer(fichero)
        escritor.writerow(["id_trab", "tipo", "grupo", "dias", "horas", "objetivo", "exceso"])
        for w in sorted(datos.trabajadores,
                        key=lambda w: (_grupo(datos, w), w)):
            escritor.writerow([w, datos.trabajadores[w].tipo, _grupo(datos, w), dias[w],
                               f"{libro.horas(w):.1f}", f"{libro.objetivo(w):.0f}",
                               f"{libro.exceso(w):+.1f}"])


def resumen(datos: Datos, libro: LibroHoras, titulo: str) -> None:
    """Horas por grupo (patrón, o tipo si no lo tiene) frente al objetivo. Es la comprobación de
    un vistazo: un patrón que prescribe +80 h/año es un patrón que tendrá que ceder dos semanas."""
    grupos: dict[str, list[str]] = defaultdict(list)
    for w in datos.trabajadores:
        grupos[_grupo(datos, w)].append(w)

    print(f"\n{titulo}")
    print(f"{'grupo':<18} {'n':>3} {'h media':>8} {'h min':>7} {'h max':>7} {'Δ obj medio':>12}")
    print("-" * 60)
    for g, gente in sorted(grupos.items()):
        hs = [libro.horas(w) for w in gente]
        dl = sum(libro.exceso(w) for w in gente) / len(gente)
        print(f"{g:<18} {len(gente):>3} {sum(hs)/len(hs):>8.0f} {min(hs):>7.0f} "
              f"{max(hs):>7.0f} {dl:>+12.0f}")
    print("-" * 60)
    pasados = [w for w in datos.trabajadores if libro.exceso(w) > EPS]
    print(f"  por encima del objetivo: {len(pasados)} de {len(datos.trabajadores)}")

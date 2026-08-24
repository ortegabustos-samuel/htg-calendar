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


# --------------------------------------------------------------------------- #
#  Informe: lo que se mira para dar por bueno un paso
# --------------------------------------------------------------------------- #
def _grupo(datos: Datos, trabajador_id: str) -> str:
    """Etiqueta con la que se agrupa en el resumen: el patrón si lo tiene, si no el tipo."""
    trabajador = datos.trabajadores[trabajador_id]
    return trabajador.patron if trabajador.tipo == "patron" and trabajador.patron else trabajador.tipo


def escribir_csv(datos: Datos, plan: dict[tuple[str, date], str], libro: LibroHoras,
                 ruta: Path) -> None:
    """Una fila por trabajador: días trabajados, horas, objetivo y exceso."""
    dias: dict[str, int] = defaultdict(int)
    for (trabajador_id, _) in plan:
        dias[trabajador_id] += 1

    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as fichero:
        escritor = csv.writer(fichero)
        escritor.writerow(["id_trab", "tipo", "grupo", "dias", "horas", "objetivo", "exceso"])
        for trabajador_id in sorted(datos.trabajadores,
                        key=lambda trabajador_id: (_grupo(datos, trabajador_id), trabajador_id)):
            escritor.writerow([trabajador_id, datos.trabajadores[trabajador_id].tipo, _grupo(datos, trabajador_id), dias[trabajador_id],
                               f"{libro.horas(trabajador_id):.1f}", f"{libro.objetivo(trabajador_id):.0f}",
                               f"{libro.exceso(trabajador_id):+.1f}"])


def balance(datos: Datos) -> None:
    """¿Cuadra la aritmética gruesa antes de resolver nada?

    Horas que EXIGE la demanda del año frente a las que APORTA la plantilla a su jornada objetivo.
    Si el balance sale negativo, el cuadrante es imposible sin huecos o sin pasarse del objetivo: es
    un problema de PLANTILLA, no de solver, y conviene saberlo antes de gastar cuatro minutos de
    cómputo. Las líneas de demanda 0 (los refuerzos) no cuentan: no son demanda, son relleno.
    """
    objetivo = datos.config.horas_objetivo
    exige = sum(sum(1 for f in datos.fechas if datos.opera(s, f)) * t.dem * t.horas
                for s, t in datos.turnos.items() if t.dem > 0)
    capacidad = sum(objetivo * w.factor_jornada for w in datos.trabajadores.values())
    n = len(datos.trabajadores)
    print(f"\nBALANCE ANUAL — demanda {exige:,.0f} h frente a {capacidad:,.0f} h de plantilla "
          f"({n} trabajadores a {objetivo} h)")
    print(f"  holgura {capacidad - exige:+,.0f} h ({(capacidad - exige) / objetivo:+.1f} FTE) · "
          f"media exigida {exige / n:,.0f} h por trabajador")
    if capacidad < exige:
        print("  *** DÉFICIT: no hay horas para la demanda. Habrá huecos o exceso de jornada ***")


def resumen(datos: Datos, libro: LibroHoras, titulo: str) -> None:
    """Horas por grupo (patrón, o tipo si no lo tiene) frente al objetivo. Es la comprobación de
    un vistazo: un patrón que prescribe +80 h/año es un patrón que tendrá que ceder dos semanas."""
    grupos: dict[str, list[str]] = defaultdict(list)
    for trabajador_id in datos.trabajadores:
        grupos[_grupo(datos, trabajador_id)].append(trabajador_id)

    print(f"\n{titulo}")
    print(f"{'grupo':<18} {'n':>3} {'h media':>8} {'h min':>7} {'h max':>7} {'Δ obj medio':>12}")
    print("-" * 60)
    for grupo, list_trabajadores in sorted(grupos.items()):
        hs = [libro.horas(trabajador_id) for trabajador_id in list_trabajadores]
        dl = sum(libro.exceso(trabajador_id) for trabajador_id in list_trabajadores) / len(list_trabajadores)
        print(f"{grupo:<18} {len(list_trabajadores):>3} {sum(hs)/len(hs):>8.0f} {min(hs):>7.0f} "
              f"{max(hs):>7.0f} {dl:>+12.0f}")
    print("-" * 60)
    pasados = [trabajador_id for trabajador_id in datos.trabajadores if libro.exceso(trabajador_id) > EPS]
    print(f"  por encima del objetivo: {len(pasados)} de {len(datos.trabajadores)}")

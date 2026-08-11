#!/usr/bin/env python3
"""Orquesta el pipeline determinista de cinco pasos y escribe la salida.

El plan se construye por acumulación: los pasos 1 a 4 solo AÑADEN asignaciones, y el 5 es el único
autorizado a deshacer. Tras cada paso se verifica el convenio entero, para que una infracción se
detecte donde se causó y no ocho meses después."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import criticos
import decisiones
import libranzas
import reparacion
import reparto
import rotacion
import salida
import validar_datos
from cargar_datos import cargar, Datos
from deuda import Deuda
from legal import Legal
from plan import Plan


def _paso(nombre: str, plan: Plan, ley: Legal) -> None:
    fallos = ley.verificar(plan)
    if fallos:
        raise SystemExit(f"BUG en el paso «{nombre}»: {len(fallos)} infracciones de convenio\n"
                         + "\n".join(f"  · {x}" for x in fallos[:10]))
    print(f"  {nombre:14s} · {len(plan.libro):6d} decisiones · {len(plan.huecos):4d} huecos")


def construir(datos: Datos, con_reparacion: bool = True) -> Plan:
    ley, dd = Legal(datos), Deuda(datos)
    plan = Plan()

    libranzas.repartir(datos, plan);        _paso("1 libranzas", plan, ley)
    criticos.cubrir(datos, plan, ley);      _paso("2 criticos", plan, ley)
    rotacion.estampar(datos, plan, ley)
    rotacion.adoptar(datos, plan, ley, dd); _paso("3 rotacion", plan, ley)
    reparto.repartir(datos, plan, ley, dd)
    reparto.rellenar_refuerzos(datos, plan, ley, dd); _paso("4 reparto", plan, ley)
    if con_reparacion:
        reparacion.reparar(datos, plan, ley, dd)
        _paso("5 reparacion", plan, ley)
    return plan


def cobertura(datos: Datos, plan: Plan) -> tuple[int, int, float]:
    """(cubiertos, demandados, %) sobre los turnos de prioridad >= 1."""
    dem = cub = 0
    for f in datos.fechas:
        for s, t in datos.turnos.items():
            if t.prioridad < 1 or not datos.opera(s, f):
                continue
            dem += t.dem
            cub += min(t.dem, plan.cubierto(f, s))
    return cub, dem, 100.0 * cub / dem if dem else 0.0


def main() -> int:
    if validar_datos.main() != 0:
        return 1
    datos = cargar()
    print(f"Resolviendo {datos.config.anio} — pipeline determinista")
    plan = construir(datos)

    cub, dem, pct = cobertura(datos, plan)
    print(f"\nCOBERTURA  {cub}/{dem} = {pct:.2f} %   ({dem - cub} turnos sin cubrir)")

    salida.generar_anual(datos, plan.asignaciones())
    ruta = decisiones.escribir(plan)
    print(f"Libro de decisiones -> {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

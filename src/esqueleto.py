"""
esqueleto.py — Paso A: el plan inicial, sin ninguna decisión libre.

Pinta lo que los patrones ya prescriben (noches y UVI): la fila de la rotación que le toca a cada
uno esa semana, turnos y descansos. Lo que toca cada día lo dice `Datos.prescrito`.

Las vacaciones no se pintan: son ausencia, y ese día no aparece en el plan.
"""
from __future__ import annotations

from dominio import Datos, Plan


def construir(datos: Datos) -> Plan:
    """El plan inicial: lo que prescriben los patrones, salvo en vacaciones."""
    plan = Plan(datos)
    for trabajador_id in datos.trabajadores:
        for fecha in datos.lista_dias_calendario:
            if datos.disponible(trabajador_id, fecha):
                valor = datos.prescrito(trabajador_id, fecha)
                if valor is not None:
                    plan.poner(trabajador_id, fecha, valor)
    return plan

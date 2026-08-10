#!/usr/bin/env python3
"""Deuda: quien menos lleva va primero, y el desempate es total (determinismo)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from deuda import Deuda
from plan import Plan


def main() -> int:
    datos = cargar()
    dd = Deuda(datos)
    pool = sorted(x.id for x in datos.trabajadores.values() if x.tipo == "correturno")
    a, b, c = pool[0], pool[1], pool[2]

    # Objetivo: 1776 salvo reduccion de jornada
    assert dd.objetivo(a) == datos.config.horas_objetivo * datos.trabajadores[a].factor_jornada

    p = Plan()
    lunes = date(2026, 3, 16)
    # `a` acumula 3 turnos de 8 h; `b` uno; `c` ninguno.
    for i in range(3):
        p.asignar(a, lunes + timedelta(days=i), "VADN001", "test", "montaje")
    p.asignar(b, lunes, "VADN002", "test", "montaje")

    assert dd.horas(p, a) == 24.0, dd.horas(p, a)
    assert dd.horas(p, b) == 8.0
    assert dd.horas(p, c) == 0.0

    # Mas deuda = menos lleva -> c primero, luego b, luego a
    orden = dd.orden(p, [a, b, c], lunes + timedelta(days=10), "VADN001")
    assert orden == [c, b, a], orden

    # Empate total -> desempata id ascendente, siempre igual
    p2 = Plan()
    orden1 = dd.orden(p2, [c, b, a], lunes, "VADN001")
    orden2 = dd.orden(p2, [a, c, b], lunes, "VADN001")
    assert orden1 == orden2 == sorted([a, b, c]), (orden1, orden2)

    # Un sabado cuenta en la metrica sabado
    sab = date(2026, 3, 21)
    assert sab.weekday() == 5
    p3 = Plan()
    p3.asignar(a, sab, "VADN001", "test", "montaje")
    assert dd.cuenta(p3, a, "sabado") == 1
    assert dd.cuenta(p3, b, "sabado") == 0

    print("OK  deuda · orden por deuda con desempate total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

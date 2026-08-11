#!/usr/bin/env python3
"""Paso 4: semana a semana, lo escaso primero, y al que menos lleva."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana
from cargar_datos import cargar
from criticos import cubrir
from deuda import Deuda
from legal import Legal
from libranzas import repartir as repartir_libranzas
from plan import Plan
from reparto import candidatos, pendientes_semana, repartir
from rotacion import adoptar, estampar


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)

    p = Plan()
    repartir_libranzas(datos, p)
    cubrir(datos, p, ley)
    estampar(datos, p, ley)
    adoptar(datos, p, ley, dd)

    # Dentro de una semana, lo escaso va primero
    sem = (2026, 12)
    pend = pendientes_semana(datos, p, sem)
    n = [len(candidatos(datos, p, ley, f, s)) for f, s in pend]
    assert n == sorted(n), f"no esta ordenado por escasez: {n}"

    antes = len(p.libro)
    repartir(datos, p, ley, dd)
    assert len(p.libro) > antes, "el paso 4 no ha asignado nada"

    # Solo mueve al pool
    for d in p.libro[antes:]:
        if d.turno:
            assert datos.trabajadores[d.trabajador].tipo in ("mixto", "correturno"), \
                f"{d.trabajador} no es del pool"

    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    # Determinismo
    p2 = Plan()
    repartir_libranzas(datos, p2)
    cubrir(datos, p2, ley)
    estampar(datos, p2, ley)
    adoptar(datos, p2, ley, dd)
    repartir(datos, p2, ley, dd)
    assert p.asignaciones() == p2.asignaciones(), "el paso 4 no es determinista"

    print(f"OK  reparto · {len(p.libro) - antes} asignaciones al pool")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

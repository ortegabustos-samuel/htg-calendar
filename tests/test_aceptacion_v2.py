#!/usr/bin/env python3
"""Aceptacion: el pipeline entero corre, es legal, es determinista y cubre lo pactado."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from generar_anual import cobertura, construir
from legal import Legal

UMBRAL = 99.0     # liston pactado en la especificacion, alcanzado tras corregir libranzas.py


def main() -> int:
    datos = cargar()
    ley = Legal(datos)

    plan = construir(datos)
    assert ley.verificar(plan) == [], ley.verificar(plan)[:10]

    cub, dem, pct = cobertura(datos, plan)
    assert pct >= UMBRAL, f"cobertura {pct:.2f} % < {UMBRAL} %"

    plan2 = construir(datos)
    assert plan.asignaciones() == plan2.asignaciones(), "el pipeline NO es determinista"

    print(f"OK  aceptacion · cobertura {pct:.2f} % ({cub}/{dem}) · "
          f"{len(plan.huecos)} huecos · {len(plan.libro)} decisiones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

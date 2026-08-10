#!/usr/bin/env python3
"""Paso 3: estampa la rotacion donde nadie decidio otra cosa y adopta las filas huerfanas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import turno_prescrito
from cargar_datos import cargar
from criticos import cubrir
from deuda import Deuda
from legal import Legal
from libranzas import repartir
from plan import Plan
from rotacion import adoptar, estampar


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)

    p = Plan()
    repartir(datos, p)
    cubrir(datos, p, ley)
    antes = len(p.libro)
    estampar(datos, p, ley)
    adoptar(datos, p, ley, dd)
    assert len(p.libro) > antes, "el paso 3 no ha estampado nada"

    # Un dia cedido en el paso 1 NO puede acabar con turno
    for d in p.libro:
        if d.paso == "libranzas" and d.turno is None:
            assert p.turno_de(d.trabajador, d.fecha) is None, \
                f"{d.trabajador} cedio el {d.fecha} y acabo trabajando"

    # Lo asignado por el paso 2 sigue intacto
    for d in p.libro:
        if d.paso == "criticos" and d.turno:
            assert p.turno_de(d.trabajador, d.fecha) == d.turno, \
                f"el paso 3 piso la cobertura critica de {d.trabajador} el {d.fecha}"

    # Un fijo disponible hace su linea salvo que cediera o cubriera algo critico
    fijo = next(x for x in datos.trabajadores.values() if x.tipo == "fijo" and x.linea)
    dias_suyos = [f for f in datos.fechas
                  if datos.disponible(fijo.id, f) and turno_prescrito(datos, fijo.id, f)]
    hechos = sum(1 for f in dias_suyos if p.turno_de(fijo.id, f) is not None)
    assert hechos > 0.8 * len(dias_suyos), f"{fijo.id}: solo {hechos}/{len(dias_suyos)}"

    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    print(f"OK  rotacion · {len(p.libro) - antes} decisiones del paso 3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

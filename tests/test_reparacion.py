#!/usr/bin/env python3
"""Paso 5: cierra huecos deshaciendo, en el orden pactado, sin romper el convenio."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from deuda import Deuda
from generar_anual import cobertura, construir
from legal import Legal
from reparacion import huecos_reales, reparar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)

    sin = construir(datos, con_reparacion=False)
    _, _, pct_sin = cobertura(datos, sin)
    huecos_antes = len(huecos_reales(datos, sin))

    cerrados = reparar(datos, sin, ley, dd)
    _, _, pct_con = cobertura(datos, sin)
    huecos_despues = len(huecos_reales(datos, sin))

    assert cerrados >= 0
    assert huecos_despues <= huecos_antes, "la reparacion ha ABIERTO huecos"
    assert pct_con >= pct_sin, f"la reparacion ha empeorado: {pct_sin} -> {pct_con}"
    assert ley.verificar(sin) == [], ley.verificar(sin)[:10]

    # Determinismo
    a = construir(datos, con_reparacion=True)
    b = construir(datos, con_reparacion=True)
    assert a.asignaciones() == b.asignaciones(), "la reparacion no es determinista"

    print(f"OK  reparacion · {huecos_antes} -> {huecos_despues} huecos "
          f"({cerrados} cerrados) · cobertura {pct_sin:.2f} -> {pct_con:.2f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

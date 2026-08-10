#!/usr/bin/env python3
"""Paso 2: las lineas criticas se cubren antes que nada, y una semana solo se adopta una vez."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana
from cargar_datos import cargar
from criticos import cubrir, lineas_criticas, principales
from legal import Legal
from libranzas import repartir
from plan import Plan


def main() -> int:
    datos = cargar()
    ley = Legal(datos)

    criticas = lineas_criticas(datos)
    assert set(criticas) >= {"VADU47127", "VADN051", "VADN052", "VADP003", "H"}, criticas
    # ordenadas de mas critica a menos: VADU47127 es prioridad 4
    assert criticas[0] == "VADU47127", criticas

    orden = principales(datos)
    assert orden["VADU47127"], "VADU47127 debe tener cubridor designado"

    p = Plan()
    repartir(datos, p)
    cubrir(datos, p, ley)

    dec = [d for d in p.libro if d.paso == "criticos"]
    assert dec, "el paso 2 no ha asignado nada"

    # Nadie adopta dos veces en la misma semana ISO
    adopciones = {}
    for d in dec:
        clave = (d.trabajador, semana(d.fecha))
        adopciones.setdefault(clave, set()).add(d.turno)
    for (w, sem), turnos in adopciones.items():
        assert len(turnos) == 1, f"{w} cubre {turnos} en la semana {sem}"

    # Todo lo asignado es legal
    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    # Determinismo
    p2 = Plan()
    repartir(datos, p2)
    cubrir(datos, p2, ley)
    a = [(d.trabajador, d.fecha, d.turno) for d in p.libro if d.paso == "criticos"]
    b = [(d.trabajador, d.fecha, d.turno) for d in p2.libro if d.paso == "criticos"]
    assert a == b, "el paso 2 no es determinista"

    print(f"OK  criticos · {len(dec)} coberturas · {len(criticas)} lineas criticas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

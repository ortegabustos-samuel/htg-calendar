#!/usr/bin/env python3
"""El orden v de cubridores: quien es principal de cada linea critica."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from modelo import principales


def main() -> int:
    datos = cargar()
    p = principales(datos)

    # Los dos cubridores estan CRUZADOS a proposito: cada uno es principal de una linea
    # y suplente de la otra. Es exactamente lo que el plan actual no respeta.
    assert p["VADP003"][0] == "01860358A", p["VADP003"]
    assert p["VADU47127"][0] == "18029935M", p["VADU47127"]

    # El orden es completo y creciente en v.
    for linea, orden in p.items():
        assert orden, f"{linea} sin cubridores designados"
        vs = [datos.capacidades[(w, linea)].v for w in orden]
        assert vs == sorted(vs), f"{linea}: orden no creciente {vs}"

    print(f"OK  orden de cubridores para {len(p)} lineas: "
          + " · ".join(f"{k}->{v[0]}" for k, v in sorted(p.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

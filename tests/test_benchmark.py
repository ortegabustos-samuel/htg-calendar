#!/usr/bin/env python3
"""Benchmark contra la rama base (tag base-cesiones-noche, 99,5 % de cobertura).

No ejecuta CP-SAT: compara contra las cifras anotadas de aquella corrida. El optimizador no esta en
el producto, esta en la vara de medir."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from deuda import Deuda
from generar_anual import cobertura, construir
from legal import Legal

# Cifras de la rama base (tag base-cesiones-noche) sobre el mismo dataset 2026.
BASE_COBERTURA = 99.5


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)
    plan = construir(datos)

    cub, dem, pct = cobertura(datos, plan)
    assert ley.verificar(plan) == [], ley.verificar(plan)[:10]

    pool = sorted(x.id for x in datos.trabajadores.values()
                  if x.tipo in ("mixto", "correturno"))
    sab = [dd.cuenta(plan, w, "sabado") for w in pool]
    dom = [dd.cuenta(plan, w, "domingo") for w in pool]
    horas = [dd.horas(plan, w) for w in pool]

    print(f"  cobertura      {pct:6.2f} %   (base {BASE_COBERTURA} %, "
          f"delta {pct - BASE_COBERTURA:+.2f})")
    print(f"  huecos         {dem - cub:6d}")
    print(f"  pool sabados   min {min(sab):3d}  max {max(sab):3d}  rango {max(sab)-min(sab):3d}")
    print(f"  pool domingos  min {min(dom):3d}  max {max(dom):3d}  rango {max(dom)-min(dom):3d}")
    print(f"  pool horas     min {min(horas):7.0f}  max {max(horas):7.0f}  "
          f"rango {max(horas)-min(horas):5.0f}")

    assert pct >= 99.0, f"cobertura {pct:.2f} % por debajo del liston pactado"
    print("OK  benchmark")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

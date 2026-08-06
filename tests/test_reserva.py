#!/usr/bin/env python3
"""La reserva de Nivel 0 no puede cambiar al refactorizar reserva_cubridores."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from modelo import calendario_cesiones, reserva_cubridores

# Valores con el calendario de cesiones DETERMINISTA (semilla fija, un hilo). Antes de fijarlo,
# Y0945237C daba 495 en unas ejecuciones y 506 en otras: era el sorteo del solver, no un cálculo.
# Que los dos cubridores de noche salgan simétricos (506 y 506) es lo esperable, porque su carga
# es simétrica por construcción. Comprobado que viejo y nuevo reserva_cubridores coinciden al
# pasarles las MISMAS cesiones, así que el refactor no cambió comportamiento.
ESPERADO = {"Y0945237C": 506.0, "71174480Z": 506.0, "01860358A": 194.3,
            "18029935M": 194.3, "12427762B": 137.1, "72918050T": 182.9}


def main() -> int:
    datos = cargar()
    res = reserva_cubridores(datos, calendario_cesiones(datos))
    obtenido = {w: round(max(c.values()), 1) for w, c in res.items()}
    assert obtenido == ESPERADO, f"\n esperado {ESPERADO}\n obtenido {obtenido}"
    print(f"OK  reserva sin cambios para {len(obtenido)} cubridores")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

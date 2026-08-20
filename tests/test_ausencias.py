#!/usr/bin/env python3
"""Comprueba ausencias_criticas contra los casos reales de 2026 que motivaron el cambio."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from v3.cargar_datos import cargar
from modelo import ausencias_criticas, calendario_cesiones


def main() -> int:
    datos = cargar()
    aus = ausencias_criticas(datos, calendario_cesiones(datos))

    # Semana del 3 al 9 de agosto: 12427762B esta de vacaciones del 1 al 15 y le toca
    # la fila 0 de UVI_PRIV, que prescribe SOLO miercoles y jueves. Faltan los dos ->
    # es la fila entera -> ADOPCION.
    a = next(x for x in aus if x.linea == "VADP003" and x.titular == "12427762B"
             and date(2026, 8, 6) in x.faltan)
    assert a.prescritos == frozenset({date(2026, 8, 5), date(2026, 8, 6)}), a.prescritos
    assert a.faltan == a.prescritos, a.faltan
    assert a.entera is True
    assert date(2026, 8, 3) in a.libres and date(2026, 8, 7) in a.libres, a.libres

    # Semana del 12 al 18 de octubre: 72918050T esta de vacaciones HASTA EL 15 y le toca
    # la fila 1, que prescribe lunes, martes, viernes, sabado y domingo. Solo faltan el
    # lunes 12 y el martes 13 -> PARCIAL.
    a = next(x for x in aus if x.linea == "VADP003" and x.titular == "72918050T"
             and date(2026, 10, 12) in x.faltan)
    assert a.faltan == frozenset({date(2026, 10, 12), date(2026, 10, 13)}), a.faltan
    assert len(a.prescritos) == 5, a.prescritos
    assert a.entera is False

    # Invariantes generales
    for x in aus:
        assert x.faltan, "una ausencia sin dias que falten no deberia existir"
        assert x.faltan <= x.prescritos
        assert not (x.libres & x.prescritos), "libres y prescritos son disjuntos"
        assert datos.turnos[x.linea].prioridad >= 2

    print(f"OK  {len(aus)} ausencias criticas · "
          f"{sum(1 for x in aus if x.entera)} enteras · "
          f"{sum(1 for x in aus if not x.entera)} parciales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

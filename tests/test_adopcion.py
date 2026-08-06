#!/usr/bin/env python3
"""La adopcion se dispara por fila entera, no por tocar una linea critica."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from modelo import ausencias_criticas, calendario_cesiones, semana, semanas_adoptadas


def main() -> int:
    datos = cargar()
    aus = ausencias_criticas(datos, calendario_cesiones(datos))
    enteras = [a for a in aus if a.entera]
    parciales = [a for a in aus if not a.entera]

    # Las dos deben existir en estos datos, o el cambio no se estaria probando.
    assert enteras, "no hay ninguna adopcion: el test no prueba nada"
    assert parciales, "no hay ninguna cobertura parcial: el test no prueba nada"

    # Agosto: fila entera -> adopcion, y arrastra descansos.
    ago = next(a for a in enteras if a.linea == "VADP003" and date(2026, 8, 6) in a.faltan)
    assert ago.libres, "una adopcion sin descansos que heredar no tiene sentido"

    # Octubre: parcial -> NO arrastra nada, se cubre como turno normal.
    oct_ = next(a for a in parciales if a.linea == "VADP003" and date(2026, 10, 12) in a.faltan)
    assert not oct_.entera

    # Los dias que faltan en una parcial son estrictamente menos que los prescritos.
    for a in parciales:
        assert len(a.faltan) < len(a.prescritos)

    # semanas_adoptadas: lo que consultan el relleno de refuerzos y el pulido.
    sa = semanas_adoptadas(datos)
    assert ("VADP003", semana(date(2026, 8, 6))) in sa
    assert ("VADP003", semana(date(2026, 10, 12))) not in sa

    print(f"OK  {len(enteras)} adopciones · {len(parciales)} coberturas parciales · "
          f"{len(sa)} semanas adoptadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

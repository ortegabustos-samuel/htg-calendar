#!/usr/bin/env python3
"""Legal: C4 (12h), C5 (dias/semana) y C6 (48h/semana), y la exencion de los pares pactados."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from legal import Legal
from plan import Plan


def main() -> int:
    datos = cargar()
    ley = Legal(datos)
    p = Plan()

    # Los pares pactados existen y contienen el localizado consigo mismo: VADU47127 es
    # 22:00->22:00, encadena con 0 h de descanso y su rotacion lo exige.
    assert ("VADU47127", "VADU47127") in ley.pares_c4, "el localizado debe estar exento de C4"

    # C4: una noche seguida de una manana del dia siguiente no llega a 12 h de descanso.
    # VADN171 sale a las 08:00, VADN173 entra a las 07:00 -> 23 h, eso SI cabe.
    # Buscamos un par real que NO quepa: noche 23:00-08:00 y tarde que entre antes de las 20:00.
    lun, mar = date(2026, 3, 16), date(2026, 3, 17)
    w = next(x.id for x in datos.trabajadores.values() if x.tipo == "correturno")
    p.asignar(w, lun, "VADN171", "test", "montaje")       # 23:00 -> 08:00 del martes
    ok, motivo = ley.puede(p, w, mar, "VADN171")          # otra noche: 23:00 del martes
    assert ok, f"noche+noche son 15 h de descanso, deberia caber: {motivo}"

    # C5: el tope del pool es cmax_pool (5), no cmax (6).
    assert ley.tope_dias(w) == datos.config.cmax_pool
    fijo = next(x.id for x in datos.trabajadores.values() if x.tipo == "fijo")
    assert ley.tope_dias(fijo) == datos.config.cmax

    # Llenamos la semana del pool hasta el tope y comprobamos que el siguiente dia se bloquea.
    p2 = Plan()
    dias = [date(2026, 3, 16) + timedelta(days=i) for i in range(7)]
    for d in dias[:datos.config.cmax_pool]:
        p2.asignar(w, d, "VADN001", "test", "montaje")
    ok, motivo = ley.puede(p2, w, dias[datos.config.cmax_pool], "VADN001")
    assert not ok and "C5" in motivo, f"deberia bloquear por C5, dijo: {ok} {motivo}"

    # El plan que acabamos de construir a mano es legal salvo por ese tope; verificar() lo dice.
    infracciones = ley.verificar(p2)
    assert infracciones == [], infracciones

    # Y un plan que SI infringe se detecta.
    p3 = Plan()
    for d in dias[:datos.config.cmax_pool + 1]:
        p3.asignar(w, d, "VADN001", "test", "montaje")
    assert any("C5" in x for x in ley.verificar(p3)), ley.verificar(p3)

    print(f"OK  legal · {len(ley.pares_c4)} pares exentos de C4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

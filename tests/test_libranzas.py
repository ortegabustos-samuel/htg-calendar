#!/usr/bin/env python3
"""Paso 1: quien cede, cuanto, con que granularidad y donde cae."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from libranzas import es_bloque, exceso_h, peso_semanas, repartir
from plan import Plan


def main() -> int:
    datos = cargar()

    # Granularidad DERIVADA: los cuatro binomios son de bloque, el resto no.
    bloque = {p for p in datos.patrones if es_bloque(datos, p)}
    assert bloque == {"UVI_VAL", "UVI_PRIV", "VAL_NOCHES", "VAL_NOCHES2"}, bloque

    # UVI no cede: sus horas LEGALES estan por debajo del objetivo.
    for p in ("UVI_PRIV", "UVI_VAL"):
        w = next(x.id for x in datos.trabajadores.values() if x.patron == p)
        assert exceso_h(datos, w) == 0.0, f"{p} no deberia ceder: {exceso_h(datos, w)}"

    # PAT_MEDINA cede POCO (no 60h, que es lo que da la moneda de CONSUMO en diagnostico.py):
    # es el unico patron no-UVI que toca un localizado 24h. Pero no hay un unico numero
    # representativo por PATRON: exceso_h es por TRABAJADOR, y las 9 filas de PAT_MEDINA pesan
    # entre 32 y 45 h/semana, asi que cada trabajador cede segun en que semanas de la rotacion
    # caigan SUS vacaciones. Medido: 7 de los 9 no ceden nada y 2 si (14h y 33h) -- coger "el
    # primero del CSV" es arbitrario y ese en concreto da 0h, por eso se comprueba el grupo.
    medina = [x.id for x in datos.trabajadores.values() if x.patron == "PAT_MEDINA"]
    excesos_medina = {w: exceso_h(datos, w) for w in medina}
    assert any(e > 0 for e in excesos_medina.values()), \
        f"PAT_MEDINA no deberia quedarse entero en cero, como UVI: {excesos_medina}"
    assert max(excesos_medina.values()) < 60, \
        f"PAT_MEDINA no deberia acercarse a los 60h de la moneda de CONSUMO: {excesos_medina}"

    # PAT_GRANDE_VALL cede ~92 h
    w = next(x.id for x in datos.trabajadores.values() if x.patron == "PAT_GRANDE_VALL")
    assert 80 <= exceso_h(datos, w) <= 105, exceso_h(datos, w)

    # El peso de agosto debe ser MENOR que el de una semana normal: hay menos gente.
    pesos = peso_semanas(datos)
    agosto = [v for k, v in pesos.items() if k[1] in (32, 33, 34)]
    marzo = [v for k, v in pesos.items() if k[1] in (11, 12, 13)]
    assert sum(agosto) / len(agosto) < sum(marzo) / len(marzo), "agosto deberia pesar menos"

    # Reparto completo: nadie cede un dia en el que ya estaba de vacaciones,
    # y las cesiones de bloque cubren la fila ENTERA de su semana.
    p = Plan()
    repartir(datos, p)
    cesiones = [(d.trabajador, d.fecha) for d in p.libro if d.turno is None]
    assert cesiones, "no ha cedido nadie"
    for w, f in cesiones:
        assert datos.disponible(w, f), f"{w} cede el {f} pero estaba de vacaciones"

    # Determinismo: dos repartos identicos
    p2 = Plan()
    repartir(datos, p2)
    assert [(d.trabajador, d.fecha) for d in p2.libro if d.turno is None] == cesiones

    print(f"OK  libranzas · {len(cesiones)} dias cedidos · {len(bloque)} patrones de bloque")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

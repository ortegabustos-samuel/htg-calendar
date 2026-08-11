#!/usr/bin/env python3
"""Paso 4: semana a semana, lo escaso primero, y al que menos lleva.

Ronda de corrección 1: ancla los tres hallazgos de la revisión.
  (i)   la deuda manda sobre la estabilidad, no al reves (hallazgo 1 - clave de min() invertida).
  (ii)  un turno con dem > 1 (REF CAL) recibe varias asignaciones el mismo dia (hallazgo 2).
  (iii) rellenar_refuerzos no lleva a nadie por encima de su objetivo anual (hallazgo 3)."""
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana
from cargar_datos import cargar
from criticos import cubrir
from deuda import Deuda
from legal import Legal
from libranzas import repartir as repartir_libranzas
from plan import Plan
from reparto import _elegir, candidatos, pendientes_semana, rellenar_refuerzos, repartir
from rotacion import adoptar, estampar


def test_deuda_manda_sobre_estabilidad(datos) -> None:
    """Hallazgo 1: con la clave de `min()` invertida esto elegia a `b` (mejor estabilidad, menos
    deuda). `a` va primero en `por_deuda` (mas deuda) pero ya trabajo esta semana en una franja y
    un municipio distintos del turno objetivo (peor estabilidad): la deuda tiene que ganar igual."""
    pool = sorted(w for w, t in datos.trabajadores.items() if t.tipo in ("mixto", "correturno"))
    a, b = pool[0], pool[1]
    turnos = sorted(datos.turnos)
    s_obj = turnos[0]
    s_otro = next(s for s in turnos if datos.turnos[s].tipo != datos.turnos[s_obj].tipo
                  and datos.turnos[s].municipio != datos.turnos[s_obj].municipio)

    p = Plan()
    lunes = date(2026, 3, 16)
    assert lunes.weekday() == 0
    p.asignar(a, lunes, s_otro, "test", "monta escenario: a ya curra esta semana, franja distinta")
    jueves = lunes + timedelta(days=3)

    por_deuda = [a, b]     # a: mas deuda (posicion 0). b: sin nada esta semana, mejor estabilidad.
    elegido = _elegir(datos, p, por_deuda, jueves, s_obj)
    assert elegido == a, f"deberia ganar la deuda ({a}), no la estabilidad ({b}): eligio {elegido}"


def test_rellenar_refuerzos_para_en_seco(datos) -> None:
    """Hallazgo 3, caso positivo: sobre un plan vacio (un solo correturno, sin nada asignado en
    todo el año) hay de sobra donde meter REF CAL, y el corte `if falta <= 0` tiene que activarse
    exactamente al llegar al objetivo, nunca despues.

    Se filtra `datos.trabajadores` a un solo correturno (con `dataclasses.replace`, sin tocar
    turnos/capacidades/patrones reales) para que el barrido anual de `rellenar_refuerzos` no tenga
    que descartar a los otros 87 cada ronda: mismo comportamiento, mucho mas rapido de anclar."""
    correturno = sorted(w for w, t in datos.trabajadores.items() if t.tipo == "correturno")[0]
    datos_uno = replace(datos, trabajadores={correturno: datos.trabajadores[correturno]})
    ley_uno, dd_uno = Legal(datos_uno), Deuda(datos_uno)

    p = Plan()
    asignados = rellenar_refuerzos(datos_uno, p, ley_uno, dd_uno)
    assert asignados > 0, "no ha rellenado nada teniendo el año entero libre"

    horas_final = dd_uno.horas(p, correturno)
    objetivo = dd_uno.objetivo(correturno)
    assert horas_final <= objetivo + 1e-9, \
        f"{correturno} quedo por encima de su objetivo: {horas_final} h > {objetivo} h"
    # Con REF CAL de 8 h y objetivo=1776 (multiplo exacto), tiene que parar EXACTO, no antes de
    # sobra ni pasado: si el corte fuese `<` en vez de `<=`, o mirase horas ANTES de sumar el
    # turno en vez de la proyeccion, esto fallaria por soltar un turno de mas o de menos.
    assert horas_final == objetivo, \
        f"{correturno}: se detuvo en {horas_final} h en vez de exactamente en {objetivo} h"
    assert ley_uno.verificar(p) == [], ley_uno.verificar(p)[:5]


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)

    test_deuda_manda_sobre_estabilidad(datos)
    test_rellenar_refuerzos_para_en_seco(datos)

    p = Plan()
    repartir_libranzas(datos, p)
    cubrir(datos, p, ley)
    estampar(datos, p, ley)
    adoptar(datos, p, ley, dd)

    # Dentro de una semana, lo escaso va primero
    sem = (2026, 12)
    pend = pendientes_semana(datos, p, sem)
    n = [len(candidatos(datos, p, ley, f, s)) for f, s in pend]
    assert n == sorted(n), f"no esta ordenado por escasez: {n}"

    antes = len(p.libro)
    repartir(datos, p, ley, dd)
    assert len(p.libro) > antes, "el paso 4 no ha asignado nada"

    # Solo mueve al pool
    for d in p.libro[antes:]:
        if d.turno:
            assert datos.trabajadores[d.trabajador].tipo in ("mixto", "correturno"), \
                f"{d.trabajador} no es del pool"

    # Hallazgo 2: REF CAL (dem=10) tiene que poder recibir mas de una persona el mismo dia.
    multi = [f for f in datos.fechas
             if datos.opera("REF CAL M", f) and p.cubierto(f, "REF CAL M") > 1]
    assert multi, "REF CAL M nunca junta a mas de 1 persona el mismo dia: el bucle dem>1 no actua"

    # Efecto colateral del hallazgo 2, encontrado al medir (no pedido en la ronda, pero hay que
    # anclarlo o vuelve a colar-se): sin separar la cobertura REAL (prioridad>=1) del comodin REF
    # CAL dentro de cada semana, el bucle dem>1 de REF CAL agota el cupo semanal del pool ANTES de
    # llegar a la cobertura real de esa semana. Medido: sin esa prelacion los pendientes de
    # cobertura real suben de ~250 a 753; con ella, se quedan por debajo de la cifra original.
    pend_reales = sum(1 for sem in sorted({semana(f) for f in datos.fechas})
                      for f, s in pendientes_semana(datos, p, sem)
                      if datos.turnos[s].prioridad >= 1)
    assert pend_reales < 400, (
        f"demasiada cobertura real sin cubrir tras el paso 4 ({pend_reales} pares): huele a que "
        "REF CAL le esta robando cupo semanal a la cobertura real (ver docstring de repartir())"
    )

    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    # Hallazgo 3, sobre el año real: el relleno de refuerzos no empuja a nadie por encima de su
    # objetivo anual. `test_rellenar_refuerzos_para_en_seco` ya ancla el caso POSITIVO (que rellena
    # de verdad y para exacto); aqui, sobre el plan completo, `repartir()` ya se ha comido casi
    # todo REF CAL disponible el mismo (con el freno de objetivo que lleva desde el hallazgo 2), asi
    # que no se exige que sobre nada — solo que si sobra algo, nadie termine por encima.
    #
    # OJO: la cobertura REAL (prioridad >= 1) SI puede dejar a un correturno por encima de 1776 h
    # — la cobertura manda sobre la equidad en la ordenacion lexicografica del proyecto — y eso no
    # es cosa de este hallazgo. Lo que hallazgo 3 prohibe es que sea `rellenar_refuerzos` quien lo
    # empuje: solo se comprueba a quien empezaba POR DEBAJO de su objetivo antes del relleno.
    correturnos_horas_antes = {w: dd.horas(p, w) for w, t in datos.trabajadores.items()
                               if t.tipo == "correturno"}
    antes_ref = len(p.libro)
    asignados_ref = rellenar_refuerzos(datos, p, ley, dd)
    for d in p.libro[antes_ref:]:
        assert datos.trabajadores[d.trabajador].tipo == "correturno", \
            f"{d.trabajador} no es correturno (REF CAL solo es de ellos)"
    for w, horas_antes in correturnos_horas_antes.items():
        if horas_antes > dd.objetivo(w) + 1e-9:
            continue                       # ya iba por encima por cobertura real: no es lo que se mide
        horas_despues = dd.horas(p, w)
        assert horas_despues <= dd.objetivo(w) + 1e-9, \
            (f"{w} iba por debajo de objetivo ({horas_antes} h) y rellenar_refuerzos lo paso "
             f"a {horas_despues} h (objetivo {dd.objetivo(w)} h)")
    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    # Determinismo (repartir + rellenar_refuerzos)
    p2 = Plan()
    repartir_libranzas(datos, p2)
    cubrir(datos, p2, ley)
    estampar(datos, p2, ley)
    adoptar(datos, p2, ley, dd)
    repartir(datos, p2, ley, dd)
    rellenar_refuerzos(datos, p2, ley, dd)
    assert p.asignaciones() == p2.asignaciones(), "el paso 4 no es determinista"

    print(f"OK  reparto · {len(p.libro) - antes} asignaciones al pool "
          f"({asignados_ref} de refuerzo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

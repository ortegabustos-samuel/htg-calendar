#!/usr/bin/env python3
"""Paso 1: quien cede, cuanto, con que granularidad y donde cae."""
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana, turno_prescrito
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

    # Regresion (bug del sesgo de lunes): las cesiones SUELTAS -- no las de fila entera, que
    # arrastran toda la semana del binomio y no eligen dia -- no deben concentrarse en un dia
    # de la semana. El bug original converge decenas de trabajadores en el lunes de las mismas
    # semanas de mas peso (46% en lunes, 277 cesiones contra 85 en martes); el reparto corregido
    # que reparte por dia menos usado hasta ahora deberia rondar 1/7 ~= 14% por dia.
    sueltas = [(d.trabajador, d.fecha) for d in p.libro
               if d.turno is None and "cesion de fila entera" not in d.regla]
    assert sueltas, "no hay cesiones sueltas que comprobar"
    por_dow = Counter(f.weekday() for _, f in sueltas)
    peor_dow, peor_n = por_dow.most_common(1)[0]
    peor_frac = peor_n / len(sueltas)
    assert peor_frac <= 0.25, (
        f"el dia de la semana {peor_dow} concentra {peor_n}/{len(sueltas)} "
        f"({peor_frac:.0%}) de las cesiones sueltas -- el bug original daba 46% en lunes: "
        f"{dict(por_dow)}")

    # Regresion: el 5-ene-2026 (lunes de la semana de mas peso del ranking) ya no se queda sin
    # nadie disponible. Antes de la correccion, 39-45 de 47 trabajadores elegibles de patron
    # cedian justo ese mismo dia porque `dias[0]` era casi siempre el lunes para todos ellos.
    lunes_critico = date(2026, 1, 5)
    assert lunes_critico.weekday() == 0, "el 5-ene-2026 deberia ser lunes"
    elegibles = [w for w, t in datos.trabajadores.items()
                 if t.tipo == "patron"
                 and datos.disponible(w, lunes_critico)
                 and turno_prescrito(datos, w, lunes_critico)]
    cedidos_ese_dia = {w for w, f in cesiones if f == lunes_critico}
    no_cedidos = [w for w in elegibles if w not in cedidos_ese_dia]
    assert elegibles, "no hay trabajadores de patron elegibles el 5-ene-2026 que comprobar"
    assert no_cedidos, (
        f"los {len(elegibles)} elegibles del 5-ene-2026 ceden todos -- sigue el sesgo de lunes")

    # Invariante (bug 1 + bug 2 juntos): ya no forzamos una cifra fija de "dias cedidos" -- con
    # los FIJOS incluidos sube respecto a la version solo-patrones. Lo que importa es que el total
    # de horas realmente cedidas sea del mismo orden que lo que exceso_h() dice que hay que ceder,
    # sumado sobre TODOS los patron+fijo (antes exceso_h ignoraba a los fijos por completo).
    horas_cedidas_por_trab: dict[str, float] = defaultdict(float)
    for d in p.libro:
        if d.turno is None:
            s = turno_prescrito(datos, d.trabajador, d.fecha)
            if s:
                horas_cedidas_por_trab[d.trabajador] += datos.turnos[s].horas
    suma_cedidas = sum(horas_cedidas_por_trab.values())
    suma_exceso = sum(exceso_h(datos, w) for w, t in datos.trabajadores.items()
                       if t.tipo in ("patron", "fijo"))
    assert suma_exceso > 0, "no deberia haber cero exceso total a repartir"
    ratio = suma_cedidas / suma_exceso
    assert 0.7 <= ratio <= 1.05, (
        f"horas cedidas ({suma_cedidas:.0f}) deberian rondar el exceso pedido "
        f"({suma_exceso:.0f}), ratio={ratio:.2f} -- la granularidad de bloque puede hacer que "
        f"algun trabajador se quede corto, pero no tan lejos")

    # Regresion (bug 2, foto fija de semana): ninguna semana ISO debe concentrar mas de ~15% del
    # total de cesiones del ano. El bug original metia el 94,5% de las 473 cesiones en solo 10 de
    # las 53 semanas (63 solitas en la semana del 30 de marzo, 63/473 = 13,3%).
    por_semana = Counter(semana(f) for _, f in cesiones)
    total_cesiones = len(cesiones)
    peor_sem, peor_n_sem = por_semana.most_common(1)[0]
    peor_frac_sem = peor_n_sem / total_cesiones
    assert peor_frac_sem <= 0.15, (
        f"la semana {peor_sem} concentra {peor_n_sem}/{total_cesiones} ({peor_frac_sem:.0%}) "
        f"de las cesiones del ano -- el bug original ya llegaba a un 13-15% en la peor semana")

    # Y el reparto entre semanas, agregado, debe ser claramente mas plano que el de la foto fija:
    # las 10 semanas con mas cesiones no deberian concentrar ya el 94,5% del total documentado
    # para el bug (`repartir.__doc__` cita la cifra exacta).
    top10_frac = sum(n for _, n in por_semana.most_common(10)) / total_cesiones
    assert top10_frac < 0.90, (
        f"las 10 semanas con mas cesiones concentran el {top10_frac:.0%} del total -- "
        f"el bug original (foto fija) llegaba al 94,5%, esto deberia bajar claramente de eso")

    # Y la desviacion estandar de cesiones-por-semana (sobre las 53 semanas del horizonte,
    # contando las que se quedan a cero) no deberia disparase: con 53 semanas y un reparto sano el
    # grueso de semanas deberia recibir un numero moderado de cesiones, no unas pocas acaparando
    # el año entero como en el bug.
    todas_semanas = sorted({semana(f) for f in datos.fechas})
    conteos = [por_semana.get(s, 0) for s in todas_semanas]
    media_sem = statistics.mean(conteos)
    desv_sem = statistics.pstdev(conteos)
    assert desv_sem < total_cesiones * 0.10, (
        f"desviacion estandar de cesiones/semana ({desv_sem:.1f}) demasiado alta frente al total "
        f"({total_cesiones}) -- sugiere que unas pocas semanas siguen acaparando el reparto")

    # Regresion (bug 1, los fijos nunca cedian): al menos uno de los 6 fijos tiene una cesion en
    # el libro con paso=="libranzas", y el caso conocido -- el fijo de VADN022, linea que opera
    # los 7 dias de la semana -- tiene exceso_h > 0 (llegaba a 2.312h/ano, 536h de mas, sin ceder
    # ni un dia porque exceso_h() y repartir() solo miraban tipo=="patron").
    fijo_vadn022 = next(w for w, t in datos.trabajadores.items()
                         if t.tipo == "fijo" and t.linea == "VADN022")
    assert exceso_h(datos, fijo_vadn022) > 0, (
        f"el fijo de VADN022 deberia tener exceso de horas: {exceso_h(datos, fijo_vadn022)}")
    cesiones_fijos = [d for d in p.libro
                       if d.turno is None and d.paso == "libranzas"
                       and datos.trabajadores[d.trabajador].tipo == "fijo"]
    assert cesiones_fijos, "ningun fijo cede -- vuelve a fallar el bug 1 (fijos nunca ceden)"

    print(f"OK  libranzas · {len(cesiones)} dias cedidos · {len(bloque)} patrones de bloque · "
          f"{len(sueltas)} sueltas (peor dia {peor_frac:.0%}) · "
          f"peor semana {peor_frac_sem:.0%} · top10 semanas {top10_frac:.0%} · "
          f"{len(cesiones_fijos)} cesiones de fijos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
equidad.py — Paso E: igualar sábados, domingos y festivos dentro de cada grupo.

Se llega aquí con la cobertura ya cerrada, y eso es lo que permite tratarla como INVARIANTE en vez
de como un precio: el paso E intercambia semanas ISO completas entre dos compañeros del mismo
grupo, así que los mismos turnos se siguen haciendo los mismos días — solo cambia quién. La
cobertura no puede empeorar porque no se toca.

Qué hay que igualar, medido sobre Valladolid 2026:

  * Lo que rompe el propio pipeline. Los domingos de PAT_GRANDE_VALL pasan de un rango de 3 en el
    esqueleto a 11 al final: los tres cubridores designados heredan ciclos de UVI y de noche, que
    trabajan todos los domingos, mientras a otros las libranzas se los quitan.
  * Lo que ya viene torcido del esqueleto. El patrón NO reparte por igual: PAT_GRANDE_VALL llega
    con un rango de 5 sábados y 6 festivos antes de que nadie lo toque, porque el ciclo tiene 38
    filas y el año 52 semanas, y los festivos caen donde caen.

Se atacan los dos: la equidad por construcción del patrón es solo aproximada, y aquí se afina.

Dos mecanismos, porque uno solo no llega:

  * INTERCAMBIO DE SEMANA — dos compañeros se cambian una semana ISO entera. Potente en grupos
    grandes, pero mueve sábados, domingos y festivos A LA VEZ: arreglar los domingos suele
    estropear los sábados, y entonces el cambio se descarta.
  * INTERCAMBIO DE DÍA — se cambian dos días SUELTOS de la misma semana ISO (el domingo de uno por
    el miércoles del otro). Quirúrgico: toca una sola clase. Se exige que los dos días caigan en la
    misma semana para que ninguno cambie su número de días trabajados esa semana, con lo que el
    tope de días semanales se cumple solo.

Las tres validaciones de un intercambio, y ninguna es opcional:
  * CAPACIDAD — cada uno tiene que poder hacer lo que el otro tenía, y estar disponible esos días.
  * JORNADA   — las dos semanas no valen lo mismo en horas; ninguno puede acabar sobre su objetivo.
  * LEGALIDAD — la semana en sí era legal para el otro, pero las FRONTERAS cambian: lo que enlaza
    con el domingo anterior y con el lunes siguiente es nuevo, y eso no lo ha pactado nadie.

La legalidad no se mide contando: se mide por FORMAS. Los patrones incumplen el convenio por
acuerdo, así que el cuadrante llega aquí con ~1.200 incumplimientos que hay que respetar. Un
criterio de "que no aumenten" deja pasar un intercambio que quita uno pactado y mete uno inventado
—el total no sube pero la composición empeora—, y así se colaban 26. Lo que se exige es que no
aparezca ninguna forma (un par de turnos consecutivos, una ventana de siete días) que el ESQUELETO
no produzca ya por su cuenta.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

import esqueleto, legal
from cargar_datos import Datos
from horas import EPS, LibroHoras
from ritmo import grupo_de

Plan = dict[tuple[str, date], str]
CLASES = ("SAB", "DOM", "FEST")


def _lunes(f: date) -> date:
    return f - timedelta(days=f.weekday())


def _semanas(datos: Datos, plan: Plan) -> dict[tuple[str, date], list[tuple[date, str]]]:
    """(trabajador, lunes) -> [(día, turno)] de esa semana."""
    salida: dict[tuple[str, date], list[tuple[date, str]]] = defaultdict(list)
    for (w, f), s in plan.items():
        salida[(w, _lunes(f))].append((f, s))
    return salida


def _cuentas(datos: Datos, plan: Plan) -> dict[str, Counter]:
    """Sábados, domingos y festivos de cada trabajador."""
    c: dict[str, Counter] = defaultdict(Counter)
    for (w, f), s in plan.items():
        dia = datos.tipo_dia(f, datos.turnos[s].municipio)
        if dia in CLASES:
            c[w][dia] += 1
    return c


def _desviacion(cuentas: dict[str, Counter], gente: list[str]) -> float:
    """Suma de distancias a la media del grupo, sumando las tres clases. Es lo que se minimiza."""
    total = 0.0
    for clase in CLASES:
        valores = [cuentas[w][clase] for w in gente]
        media = sum(valores) / len(valores)
        total += sum(abs(v - media) for v in valores)
    return total


def _reparto_semana(datos: Datos, dias: list[tuple[date, str]]) -> Counter:
    c: Counter = Counter()
    for f, s in dias:
        dia = datos.tipo_dia(f, datos.turnos[s].municipio)
        if dia in CLASES:
            c[dia] += 1
    return c


# --------------------------------------------------------------------------- #
#  Validación de un intercambio
# --------------------------------------------------------------------------- #
def _puede_asumir(datos: Datos, trab: str, dias: list[tuple[date, str]]) -> bool:
    return all(datos.disponible(trab, f) and datos.elegible(trab, s, f)[0] for f, s in dias)


def _horas_semana(datos: Datos, dias: list[tuple[date, str]]) -> float:
    return sum(datos.turnos[s].horas for _, s in dias)


def _infracciones_de(datos: Datos, plan: Plan, trab: str, desde: date, hasta: date) -> int:
    """Infracciones de ESE trabajador en ese tramo. Se cuentan antes y después del intercambio en
    vez de mirar el valor absoluto, porque los patrones ya llegan con incumplimientos pactados.

    El tramo se construye recorriendo los DÍAS de la ventana, no el plan entero. Esta función se
    llama cuatro veces por candidato y el plan tiene 19.000 entradas: recorrerlo entero es lo que
    hacía impracticable el pulido en cuanto había mucho que igualar.
    """
    local: Plan = {}
    f = desde - timedelta(days=8)
    fin = hasta + timedelta(days=8)
    while f <= fin:
        s = plan.get((trab, f))
        if s is not None:
            local[(trab, f)] = s
        f += timedelta(days=1)
    return len(legal.infracciones(datos, local))


def _intercambiar(plan: Plan, uno: list[tuple[date, str]], otro: list[tuple[date, str]],
                  a: str, b: str) -> None:
    for f, _ in uno:
        plan.pop((a, f), None)
    for f, _ in otro:
        plan.pop((b, f), None)
    for f, s in uno:
        plan[(b, f)] = s
    for f, s in otro:
        plan[(a, f)] = s


def _empeora(datos: Datos, plan: Plan, a: str, b: str, desde: date, hasta: date,
             antes: Counter, pactadas: set) -> bool:
    """¿El intercambio ya aplicado sube el número de incumplimientos o mete una forma inventada?"""
    despues = legal.formas(datos, plan, a, desde, hasta) + legal.formas(datos, plan, b, desde, hasta)
    if sum(despues.values()) > sum(antes.values()):
        return True
    return any(clave not in pactadas for clave in (despues - antes))


def _valido(datos: Datos, plan: Plan, libro: LibroHoras, a: str, b: str, lunes: date,
            suyas_a: list[tuple[date, str]], suyas_b: list[tuple[date, str]],
            pactadas: set) -> bool:
    if not _puede_asumir(datos, b, suyas_a) or not _puede_asumir(datos, a, suyas_b):
        return False
    ha, hb = _horas_semana(datos, suyas_a), _horas_semana(datos, suyas_b)
    if libro.horas(a) - ha + hb > libro.objetivo(a) + EPS:
        return False
    if libro.horas(b) - hb + ha > libro.objetivo(b) + EPS:
        return False

    domingo = lunes + timedelta(days=6)
    antes = legal.formas(datos, plan, a, lunes, domingo) + legal.formas(datos, plan, b, lunes, domingo)
    _intercambiar(plan, suyas_a, suyas_b, a, b)
    if _empeora(datos, plan, a, b, lunes, domingo, antes, pactadas):
        _intercambiar(plan, suyas_b, suyas_a, a, b)         # se deshace
        return False
    return True


# --------------------------------------------------------------------------- #
#  Bucle de pulido
# --------------------------------------------------------------------------- #
def _gana(cuentas: dict[str, Counter], a: str, b: str, media: dict[str, float],
          ra: Counter, rb: Counter) -> float:
    """Cuánto baja la desviación si `a` y `b` se intercambian esa semana.

    Se calcula analíticamente y no recalculando el grupo entero: el intercambio solo mueve a dos
    personas y la media no cambia (el total es el mismo), así que basta comparar sus dos términos.
    """
    total = 0.0
    for clase in CLASES:
        d = rb[clase] - ra[clase]
        if not d:
            continue
        m = media[clase]
        ca, cb = cuentas[a][clase], cuentas[b][clase]
        total += abs(ca - m) + abs(cb - m) - abs(ca + d - m) - abs(cb - d - m)
    return total


def pulir(datos: Datos, plan: Plan, libro: LibroHoras, vueltas: int = 400,
          pactadas: set | None = None) -> dict:
    if pactadas is None:
        pactadas = legal.pactadas(datos, esqueleto.construir(datos))
    grupos: dict[str, list[str]] = defaultdict(list)
    for w in datos.trabajadores:
        grupos[grupo_de(datos, w)].append(w)
    grupos = {g: sorted(ws) for g, ws in grupos.items() if len(ws) > 1}

    cuentas = _cuentas(datos, plan)
    inicial = {g: _desviacion(cuentas, ws) for g, ws in grupos.items()}
    descartados: set[tuple[str, str, date]] = set()
    hechos = 0

    for _ in range(vueltas):
        semanas = _semanas(datos, plan)
        reparto = {clave: _reparto_semana(datos, dias) for clave, dias in semanas.items()}
        medias = {g: {c: sum(cuentas[w][c] for w in ws) / len(ws) for c in CLASES}
                  for g, ws in grupos.items()}

        # Se ataca por el extremo: el que más tiene de una clase frente al que menos. Es lo que
        # más baja la desviación por intercambio, y evita recorrer los 35.000 pares posibles.
        candidatos = []
        for g, gente in grupos.items():
            for clase in CLASES:
                orden = sorted(gente, key=lambda w: cuentas[w][clase])
                for a in orden[-3:]:                        # los tres más cargados
                    for b in orden[:3]:                     # contra los tres más descargados
                        if a != b:
                            candidatos.append((g, a, b))
        mejor = None
        for g, a, b in candidatos:
            for lunes in {l for (w, l) in semanas if w in (a, b)}:
                if (a, b, lunes) in descartados:
                    continue
                ra, rb = reparto.get((a, lunes), Counter()), reparto.get((b, lunes), Counter())
                if ra == rb:
                    continue
                gana = _gana(cuentas, a, b, medias[g], ra, rb)
                if gana > 1e-9 and (mejor is None or gana > mejor[0]):
                    mejor = (gana, a, b, lunes)
        if mejor is None:
            break

        _, a, b, lunes = mejor
        suyas_a, suyas_b = semanas.get((a, lunes), []), semanas.get((b, lunes), [])
        if not _valido(datos, plan, libro, a, b, lunes, suyas_a, suyas_b, pactadas):
            descartados.add((a, b, lunes))
            continue
        for _, s in suyas_a:                                # el plan ya lo movió `_valido`
            libro.borra(a, s)
            libro.apunta(b, s)
        for _, s in suyas_b:
            libro.borra(b, s)
            libro.apunta(a, s)
        cuentas = _cuentas(datos, plan)
        hechos += 1

    dias = pulir_dias(datos, plan, libro, pactadas=pactadas)
    cuentas = _cuentas(datos, plan)
    final = {g: _desviacion(cuentas, ws) for g, ws in grupos.items()}
    return {"intercambios": hechos, "dias": dias, "inicial": inicial, "final": final}


# --------------------------------------------------------------------------- #
#  Intercambio de días sueltos
# --------------------------------------------------------------------------- #
def _dias_por_semana(plan: Plan) -> dict[tuple[str, date], list[date]]:
    salida: dict[tuple[str, date], list[date]] = defaultdict(list)
    for (w, f) in plan:
        salida[(w, _lunes(f))].append(f)
    return salida


def _valido_dia(datos: Datos, plan: Plan, libro: LibroHoras,
                a: str, da: date, b: str, db: date, pactadas: set) -> bool:
    """`a` le pasa su día `da` a `b` y se queda con el `db` de `b`. Ambos de la misma semana."""
    sa, sb = plan[(a, da)], plan[(b, db)]
    if (b, da) in plan or (a, db) in plan:
        return False
    if not (datos.disponible(b, da) and datos.elegible(b, sa, da)[0]):
        return False
    if not (datos.disponible(a, db) and datos.elegible(a, sb, db)[0]):
        return False
    ha, hb = datos.turnos[sa].horas, datos.turnos[sb].horas
    if libro.horas(a) - ha + hb > libro.objetivo(a) + EPS:
        return False
    if libro.horas(b) - hb + ha > libro.objetivo(b) + EPS:
        return False

    # Los dos días pueden venir en cualquier orden: sin ordenarlos aquí el tramo sale invertido y
    # la comprobación no mira nada.
    desde, hasta = min(da, db), max(da, db)
    antes = legal.formas(datos, plan, a, desde, hasta) + legal.formas(datos, plan, b, desde, hasta)
    del plan[(a, da)], plan[(b, db)]
    plan[(b, da)], plan[(a, db)] = sa, sb
    if _empeora(datos, plan, a, b, desde, hasta, antes, pactadas):
        del plan[(b, da)], plan[(a, db)]
        plan[(a, da)], plan[(b, db)] = sa, sb
        return False
    libro.borra(a, sa); libro.apunta(a, sb)
    libro.borra(b, sb); libro.apunta(b, sa)
    return True


def _clase(datos: Datos, plan: Plan, w: str, f: date) -> str | None:
    dia = datos.tipo_dia(f, datos.turnos[plan[(w, f)]].municipio)
    return dia if dia in CLASES else None


def pulir_dias(datos: Datos, plan: Plan, libro: LibroHoras, vueltas: int = 600,
               pactadas: set | None = None) -> int:
    if pactadas is None:
        pactadas = legal.pactadas(datos, esqueleto.construir(datos))
    """Afina lo que el intercambio de semana no puede: mueve un día de una clase concreta del que
    más tiene al que menos, dentro de la misma semana ISO."""
    grupos: dict[str, list[str]] = defaultdict(list)
    for w in datos.trabajadores:
        grupos[grupo_de(datos, w)].append(w)
    grupos = {g: sorted(ws) for g, ws in grupos.items() if len(ws) > 1}

    cuentas = _cuentas(datos, plan)
    descartados: set[tuple[str, date, str, date]] = set()
    hechos = 0

    semanal = _dias_por_semana(plan)                     # se mantiene al día, no se reconstruye
    for _ in range(vueltas):
        objetivo = None
        for g, gente in grupos.items():
            for clase in CLASES:
                v = [cuentas[w][clase] for w in gente]
                if max(v) - min(v) < 2:
                    continue                                # ya no hay nada que repartir
                peso = max(v) - min(v)
                if objetivo is None or peso > objetivo[0]:
                    objetivo = (peso, g, clase, gente)
        if objetivo is None:
            break

        _, g, clase, gente = objetivo
        altos = sorted(gente, key=lambda w: -cuentas[w][clase])[:4]
        bajos = sorted(gente, key=lambda w: cuentas[w][clase])[:4]
        hecho = False
        for a in altos:
            for b in bajos:
                if a == b or cuentas[a][clase] - cuentas[b][clase] < 2:
                    continue
                suyos = [f for (w, f) in plan if w == a and _clase(datos, plan, a, f) == clase]
                for da in sorted(suyos):
                    lunes = _lunes(da)
                    for db in sorted(semanal.get((b, lunes), [])):
                        if _clase(datos, plan, b, db) == clase:
                            continue                        # no cambiaría el reparto
                        if (a, da, b, db) in descartados:
                            continue
                        if _valido_dia(datos, plan, libro, a, da, b, db, pactadas):
                            semanal[(a, _lunes(da))].remove(da)
                            semanal[(b, _lunes(db))].remove(db)
                            semanal[(b, _lunes(da))].append(da)
                            semanal[(a, _lunes(db))].append(db)
                            cuentas[a][clase] -= 1
                            cuentas[b][clase] += 1
                            otra = _clase(datos, plan, a, db)
                            if otra:
                                cuentas[a][otra] += 1
                                cuentas[b][otra] -= 1
                            hechos += 1
                            hecho = True
                            break
                        descartados.add((a, da, b, db))
                    if hecho:
                        break
                if hecho:
                    break
            if hecho:
                break
        if not hecho:
            grupos[g] = [w for w in gente if w not in altos[:1]]    # se abandona ese extremo
            if len(grupos[g]) < 2:
                del grupos[g]
    return hechos


def resumen(datos: Datos, plan: Plan, info: dict) -> None:
    cuentas = _cuentas(datos, plan)
    grupos: dict[str, list[str]] = defaultdict(list)
    for w in datos.trabajadores:
        grupos[grupo_de(datos, w)].append(w)

    print(f"\nPASO E — {info['intercambios']} intercambios de semana "
          f"y {info['dias']} de día suelto")
    print(f"{'grupo':<18} {'n':>3} |{'sab':>16} |{'dom':>16} |{'fest':>16} | desviación")
    print("-" * 82)
    for g, gente in sorted(grupos.items()):
        if len(gente) < 2:
            continue
        fila = f"{g:<18} {len(gente):>3} |"
        for clase in CLASES:
            v = [cuentas[w][clase] for w in gente]
            fila += f" {sum(v)/len(v):>6.1f} ({min(v):>2}-{max(v):>2}) |"
        print(f"{fila} {info['inicial'][g]:>6.1f} -> {info['final'][g]:.1f}")
    print("-" * 82)

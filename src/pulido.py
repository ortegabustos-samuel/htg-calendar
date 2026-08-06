#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pulido.py — Pasada final de EQUIDAD: intercambia SEMANAS entre compañeros para igualar sábados,
domingos y festivos, sin tocar la cobertura.

Por qué existe (y por qué no puede hacerlo el modelo). En el objetivo lexicográfico la cobertura
manda sobre la equidad, y dentro del nivel de cobertura está `PESO_DEV`, el coste de sacar a alguien
de su rotación. La equidad vive por debajo, así que NUNCA puede pagar por mover a un trabajador de
patrón: para él el término de equidad es casi decorativo. Y ahí está justo la desigualdad que la
empresa señala — los cubridores de líneas críticas acumulan findes porque las noches y los UVI
operan también sábado y domingo, y compensarlos exigiría quitarles findes de su rotación normal.

Al operar DESPUÉS, sobre el plan ya resuelto, se puede imponer "cobertura y legalidad invariantes"
como restricción dura en vez de como precio. Eso permite trueques que el modelo tiene prohibidos por
construcción, con la garantía de que no se pierde ni un turno cubierto.

La unidad de intercambio es la SEMANA ISO COMPLETA, no el turno suelto. Es deliberado:
  · 31 de las 38 filas de PAT_GRANDE_VALL tienen UNA sola posición de lunes a viernes (cinco días
    del mismo turno). Meter un turno suelto de otra línea a mitad de semana rompería esa posición,
    que es justo lo que P5 protege en los mixtos. Al mover la semana entera, cada uno acaba con una
    semana de patrón coherente — la del otro — en lugar de un parche.
  · Nunca se tocan las semanas con línea CRÍTICA: esas coberturas solo las puede hacer su cubridor
    designado. Los cubridores solo ceden semanas "limpias", cuyo finde viene de la rotación normal.
"""
from __future__ import annotations

import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cargar_datos import Datos                                            # noqa: E402
from modelo import (JORNADA_LOCALIZADO_SEMANA, jornada_minutos,           # noqa: E402
                    lineas_localizadas, semana)

METRICAS_PULIDO = ("sabado", "domingo", "festivo")

# Tope de movimientos de cada pasada: son bucles de mejora, paran solos cuando dejan de encontrar
# intercambios que mejoren, y esto es el corte por tiempo si no lo hacen.
MAX_MOV_PULIR = 200
MAX_MOV_COHERENCIA = 400


# --------------------------------------------------------------------------- #
#  Medición
# --------------------------------------------------------------------------- #
def _cargas(datos: Datos, plan: dict) -> dict[str, dict[str, int]]:
    """Sábados, domingos y festivos de cada trabajador. El festivo se mira con el municipio del
    turno que hace ese día, no con un calendario global."""
    c: dict[str, dict[str, int]] = {w: {m: 0 for m in METRICAS_PULIDO} for w in datos.trabajadores}
    for (w, f), s in plan.items():
        if f.weekday() == 5:
            c[w]["sabado"] += 1
        if f.weekday() == 6:
            c[w]["domingo"] += 1
        if datos.es_festivo(f, datos.turnos[s].municipio):
            c[w]["festivo"] += 1
    return c


def _jornada_semana(datos: Datos, turnos: list[str], loc: set[str]) -> float:
    """Jornada (moneda del objetivo anual, la de modelo.jornada_minutos) que aporta UNA semana ISO.
    Una semana con plaza de localizado computa ENTERA —quien la asume no hace nada más—, no la suma
    de sus turnos; ver modelo.JORNADA_LOCALIZADO_SEMANA. `loc` = lineas_localizadas(datos), que se
    pasa hecho porque esto se llama una vez por semana y candidato."""
    if any(s in loc for s in turnos):
        return JORNADA_LOCALIZADO_SEMANA + sum(datos.turnos[s].horas_consumo
                                               for s in turnos if s not in loc)
    return sum(datos.turnos[s].horas_consumo for s in turnos)


def _grupos(datos: Datos) -> dict[str, list[str]]:
    """Grupos de equidad, igual que en el modelo: los fijos fuera (no hacen findes) y los que no
    tienen grupo declarado caen en un pool común."""
    g: dict[str, list[str]] = defaultdict(list)
    for w, t in datos.trabajadores.items():
        if t.tipo == "fijo":
            continue
        g[t.grupo or "__pool__"].append(w)
    return g


def _coste_grupo(vals: list[int]) -> float:
    """Coste de desigualdad de una métrica en un grupo: RANGO + desviación media.

    El rango es lo que se percibe como injusto (alguien con 10 domingos y otro con 3), pero por sí
    solo se ESTANCA: en cuanto hay dos personas empatadas en el extremo, mover a una no lo baja y el
    pulido se queda sin movimientos. La desviación media sigue empujando después, así que se suman:
    el rango marca la prioridad y la desviación desempata."""
    if len(vals) < 2:
        return 0.0
    media = sum(vals) / len(vals)
    return (max(vals) - min(vals)) + sum(abs(v - media) for v in vals) / len(vals)


def _desigualdad(cargas: dict, grupos: dict) -> float:
    """Suma del coste de desigualdad de todas las métricas en todos los grupos."""
    return sum(_coste_grupo([cargas[w][m] for w in miembros])
               for miembros in grupos.values() for m in METRICAS_PULIDO)


def resumen(datos: Datos, plan: dict, titulo: str) -> None:
    cargas, grupos = _cargas(datos, plan), _grupos(datos)
    print(f"\n{titulo}")
    print(f"{'grupo':<17} {'n':>3}  " + "  ".join(f"{m:<16}" for m in METRICAS_PULIDO))
    for g, miembros in sorted(grupos.items()):
        if len(miembros) < 2:
            continue
        cols = []
        for m in METRICAS_PULIDO:
            v = [cargas[w][m] for w in miembros]
            cols.append(f"{min(v)}-{max(v)} σ{st.pstdev(v):.2f}")
        print(f"{g:<17} {len(miembros):>3}  " + "  ".join(f"{c:<16}" for c in cols))
    print(f"  desigualdad (Σ rangos): {_desigualdad(cargas, grupos):.0f}")


# --------------------------------------------------------------------------- #
#  Legalidad de un intercambio
# --------------------------------------------------------------------------- #
def _fin_turno(f: date, t) -> datetime:
    ini = datetime.combine(f, t.hora_entrada)
    fin = datetime.combine(f, t.hora_salida)
    return fin + timedelta(days=1) if fin <= ini else fin


def _tope_dias(datos: Datos, w: str) -> int:
    """Días máximos por semana ISO, igual criterio que el modelo."""
    t = datos.trabajadores[w]
    filas = datos.patrones.get(t.patron or "") if t.tipo == "patron" else None
    if not filas:
        return datos.config.cmax_pool
    from cargar_datos import DIAS, LIBRE
    propio = max(sum(1 for dia in DIAS if fila.get(dia) and fila.get(dia) != LIBRE
                     and fila.get(dia) in datos.turnos) for fila in filas)
    return min(datos.config.cmax, max(datos.config.cmax_pool, propio))


def _descanso_ok(datos: Datos, plan: dict, w: str, f: date, s: str, pares_ok: set) -> bool:
    """¿Deja el turno s de w en el día f al menos config.rmin horas con lo que tiene el día anterior y el
    siguiente? Se exime lo que encadene una rotación pactada (mismos pares que C4 en el modelo)."""
    for delta in (-1, 1):
        vec = plan.get((w, f + timedelta(days=delta)))
        if vec is None:
            continue
        s1, s2 = (vec, s) if delta == -1 else (s, vec)
        if (s1, s2) in pares_ok:
            continue
        f1 = f + timedelta(days=delta) if delta == -1 else f
        desc = (datetime.combine(f1 + timedelta(days=1), datos.turnos[s2].hora_entrada)
                - _fin_turno(f1, datos.turnos[s1])).total_seconds() / 3600
        if desc < datos.config.rmin:
            return False
    return True


def _pares_pactados(datos: Datos) -> set[tuple[str, str]]:
    """Encadenamientos que alguna rotación contiene y por tanto pueden saltarse C4 (localizados)."""
    from cargar_datos import DIAS, LIBRE
    pares = set()
    ok = lambda s: bool(s) and s != LIBRE and s in datos.turnos          # noqa: E731
    for filas in datos.patrones.values():
        T = len(filas)
        for k, fila in enumerate(filas):
            for j, dia in enumerate(DIAS):
                s1 = fila.get(dia)
                s2 = (fila if j < 6 else filas[(k + 1) % T]).get(DIAS[(j + 1) % 7])
                if ok(s1) and ok(s2):
                    pares.add((s1, s2))
    return pares


# --------------------------------------------------------------------------- #
#  El intercambio
# --------------------------------------------------------------------------- #
def _semanas(plan: dict) -> dict[tuple[str, tuple[int, int]], list[tuple[date, str]]]:
    por: dict[tuple[str, tuple[int, int]], list[tuple[date, str]]] = defaultdict(list)
    for (w, f), s in plan.items():
        por[(w, semana(f))].append((f, s))
    return por


def _intercambiable(datos: Datos, items: list) -> bool:
    """Una semana solo se puede ceder si NO contiene ninguna línea crítica: esas coberturas son de
    su cubridor designado y nadie más puede asumirlas."""
    return bool(items) and not any(datos.turnos[s].prioridad >= 2 for _, s in items)


def _compatible(datos: Datos, A: str, B: str, itemsA: list, itemsB: list) -> bool:
    """¿Puede cada uno hacer TODA la semana del otro? Disponibilidad y capacidad, turno a turno."""
    for f, s in itemsA:
        if not datos.disponible(B, f) or not datos.elegible(B, s, f)[0]:
            return False
    for f, s in itemsB:
        if not datos.disponible(A, f) or not datos.elegible(A, s, f)[0]:
            return False
    return True


def _valida_tras_cambio(datos: Datos, plan: dict, w: str, sm: tuple[int, int],
                        horas: dict, pares_ok: set, horas_previas: dict) -> bool:
    """Comprueba, YA aplicado el cambio en `plan`, que a w le siguen cuadrando los límites: días y
    horas de esa semana, tope anual y descanso en las costuras (domingo→lunes de ambos lados)."""
    items = [(f, s) for (ww, f), s in plan.items() if ww == w and semana(f) == sm]
    if len(items) > _tope_dias(datos, w):
        return False
    if sum(datos.turnos[s].horas for _, s in items) > datos.config.hmax7:
        return False
    # El pulido no puede usar la tolerancia sobre el objetivo: esa holgura existe para CUBRIR un
    # turno que si no quedaría vacío, no para recolocar findes. Así que nadie puede acabar por
    # encima de 1776 por un intercambio (ni empeorar si ya estaba por encima por cobertura).
    tope = max(datos.config.horas_objetivo * datos.trabajadores[w].factor_jornada, horas_previas.get(w, 0.0))
    if horas[w] > tope + 1e-6:
        return False
    for f, s in items:
        if not _descanso_ok(datos, plan, w, f, s, pares_ok):
            return False
    return True


def pulir(datos: Datos, plan: dict) -> int:
    """Bucle de mejora: busca el intercambio de semana que más baja la desigualdad y lo aplica,
    mientras siga mejorando. Devuelve el nº de intercambios aplicados.

    Invariantes DUROS (por construcción o comprobados): la cobertura no cambia —los mismos turnos
    siguen cubiertos, solo cambia quién—, nadie supera su tope anual ni los límites semanales, y el
    descanso entre jornadas se revalida en las costuras, que es donde un cambio de semana puede
    romperlo."""
    pares_ok = _pares_pactados(datos)
    grupos = _grupos(datos)
    loc = lineas_localizadas(datos)
    horas: dict[str, float] = defaultdict(float)
    horas.update({w: m / 60 for w, m in
                  jornada_minutos(datos, plan, datos.fechas).items()})

    horas_ini = dict(horas)        # jornada de partida: el pulido no puede empeorarla
    cargas = _cargas(datos, plan)
    aplicados = 0
    for _ in range(MAX_MOV_PULIR):
        # Fuera las semanas que tocan días ANTERIORES al año: la del lunes 29 de diciembre es una
        # semana ISO real y está en el plan (el descanso del 1 de enero se mide contra ella), pero
        # esos días son cuadrante del año pasado, ya entregado. Ni cuentan en el libro ni se cambian
        # de dueño para igualar findes de este año.
        porsem = {k: items for k, items in _semanas(plan).items()
                  if all(f >= datos.inicio for f, _ in items)}
        # Aporte de cada semana a las métricas y a las horas: se calcula UNA vez y se reutiliza
        # para todos los candidatos. Evaluar un intercambio pasa a ser restar y sumar tres números
        # en vez de copiar el plan entero y recontarlo (miles de millones de operaciones por
        # movimiento con 703 parejas × 53 semanas).
        aporte: dict[tuple[str, tuple[int, int]], tuple[int, int, int, float]] = {}
        for k, items in porsem.items():
            sab = sum(1 for f, _ in items if f.weekday() == 5)
            dom = sum(1 for f, _ in items if f.weekday() == 6)
            fes = sum(1 for f, s in items if datos.es_festivo(f, datos.turnos[s].municipio))
            aporte[k] = (sab, dom, fes, _jornada_semana(datos, [s for _, s in items], loc))

        candidatos = []
        for miembros in grupos.values():
            if len(miembros) < 2:
                continue
            base = {m: [cargas[w][m] for w in miembros] for m in METRICAS_PULIDO}
            rango_ini = sum(_coste_grupo(v) for v in base.values())
            idx = {w: i for i, w in enumerate(miembros)}
            for A in miembros:
                for B in miembros:
                    if A >= B:
                        continue
                    for sm in {s for (w, s) in porsem if w in (A, B)}:
                        itemsA, itemsB = porsem.get((A, sm), []), porsem.get((B, sm), [])
                        if not itemsA and not itemsB:
                            continue
                        if not _intercambiable(datos, itemsA + itemsB):
                            continue
                        aA = aporte.get((A, sm), (0, 0, 0, 0.0))
                        aB = aporte.get((B, sm), (0, 0, 0, 0.0))
                        if aA[:3] == aB[:3]:
                            continue                  # el trueque no movería ninguna métrica
                        # rango del grupo tras el cambio, tocando solo las dos posiciones
                        nuevo = 0
                        for i, m in enumerate(METRICAS_PULIDO):
                            v = list(base[m])
                            v[idx[A]] += aB[i] - aA[i]
                            v[idx[B]] += aA[i] - aB[i]
                            nuevo += _coste_grupo(v)
                        if nuevo < rango_ini:
                            candidatos.append((nuevo - rango_ini, A, B, sm, aA, aB))
        candidatos.sort(key=lambda c: c[0])

        # Solo se validan (que es lo caro) los candidatos que de verdad mejoran, de mejor a peor,
        # y se aplica el primero que pase todas las comprobaciones.
        aplicado = None
        for delta, A, B, sm, aA, aB in candidatos:
            itemsA, itemsB = porsem.get((A, sm), []), porsem.get((B, sm), [])
            if not _compatible(datos, A, B, itemsA, itemsB):
                continue
            copia = dict(plan)
            for f, _s in itemsA:
                del copia[(A, f)]
            for f, _s in itemsB:
                del copia[(B, f)]
            for f, s in itemsA:
                copia[(B, f)] = s
            for f, s in itemsB:
                copia[(A, f)] = s
            hA = horas[A] - aA[3] + aB[3]
            hB = horas[B] - aB[3] + aA[3]
            hs = dict(horas, **{A: hA, B: hB})
            if not (_valida_tras_cambio(datos, copia, A, sm, hs, pares_ok, horas_ini)
                    and _valida_tras_cambio(datos, copia, B, sm, hs, pares_ok, horas_ini)):
                continue
            aplicado = (delta, A, B, sm, copia, hA, hB, aA, aB)
            break
        if aplicado is None:
            break

        delta, A, B, sm, copia, hA, hB, aA, aB = aplicado
        plan.clear()
        plan.update(copia)
        horas[A], horas[B] = hA, hB
        for i, m in enumerate(METRICAS_PULIDO):
            cargas[A][m] += aB[i] - aA[i]
            cargas[B][m] += aA[i] - aB[i]
        aplicados += 1
        print(f"  {aplicados:>3}. semana {sm[1]:>2}: {A} <-> {B}   "
              f"Σrangos {delta:+.0f}", flush=True)
    print(f"Pulido: {aplicados} intercambios de semana aplicados", flush=True)
    return aplicados


PESO_MUNICIPIO = 2      # ir al mismo sitio pesa más que coincidir de franja (prioridad de la empresa)


def _coste_semana(datos: Datos, turnos: list[str]) -> float:
    """Cuánto se desvía una semana de ser homogénea: TURNOS QUE SE SALEN del valor dominante, no
    cuántos valores distintos hay.

    La diferencia importa mucho. Contando valores distintos, una semana de cuatro turnos con tres
    mañanas y una tarde puntúa igual que otra con dos y dos —las dos tienen "2 franjas"— así que un
    intercambio que acerca la semana a la uniformidad da mejora CERO y se descarta. Solo se aceptan
    los movimientos que logran la homogeneidad perfecta de un golpe, y el pulido se atasca (medido:
    118 semanas con 1 turno descolocado y 130 con 2, todas puntuando lo mismo).

    Contando los que se salen del dominante, cada turno que se acerca cuenta, que es justo lo que se
    busca: la misma localización el mayor número de días posible y, si no puede ser, al menos la
    misma franja."""
    if len(turnos) < 2:
        return 0.0
    tipos = Counter(datos.turnos[s].tipo for s in turnos)
    munis = Counter(datos.turnos[s].municipio for s in turnos)
    return ((len(turnos) - max(tipos.values()))
            + PESO_MUNICIPIO * (len(turnos) - max(munis.values())))


def _incoherencia(datos: Datos, plan: dict, gente: list[str]) -> float:
    """Suma, sobre todas las semanas de esa gente, de los turnos que se salen de su franja y de su
    municipio dominantes. Cero = cada uno pasa cada semana en la misma franja y el mismo pueblo."""
    porsem: dict[tuple[str, tuple[int, int]], list[str]] = defaultdict(list)
    dentro = set(gente)
    for (w, f), s in plan.items():
        if w in dentro:
            porsem[(w, semana(f))].append(s)
    return sum(_coste_semana(datos, t) for t in porsem.values())


def coherencia(datos: Datos, plan: dict) -> int:
    """ÚLTIMA fase del pulido: intercambia turnos DEL MISMO DÍA entre correturnos para que cada uno
    pase la semana en la misma franja y, a poder ser, en el mismo municipio. Hoy solo el 31% de sus
    semanas tienen una sola franja y el 35% un solo municipio: es normal ver Mayorga, Íscar y
    Valladolid en la misma semana.

    El movimiento es un trueque EN EL MISMO DÍA, y esa elección lo hace muy seguro:
      · la cobertura no se mueve (los dos turnos siguen cubiertos, cambia quién);
      · cada uno trabaja los mismos días, así que los días por semana no varían;
      · y, sobre todo, NO toca el reparto de sábados y domingos, porque ambos trabajaban ese mismo
        día — con lo que la equidad de findes que costó tanto cuadrar queda intacta por construcción.
    Lo único que puede moverse son los festivos (dependen del municipio del turno) y las horas, así
    que se comprueban las dos cosas.

    Va la ÚLTIMA y solo acepta movimientos que no empeoren nada de lo anterior: es una comodidad,
    no una prioridad."""
    fechas = datos.fechas
    corre = [w for w, t in datos.trabajadores.items() if t.tipo == "correturno"]
    if len(corre) < 2:
        return 0
    pares_ok = _pares_pactados(datos)
    grupos = _grupos(datos)
    horas: dict[str, float] = defaultdict(float)
    # `fechas` = el año que se contabiliza; sin él contaría también los días del año anterior
    # con que arranca la primera semana ISO del plan (ver modelo.jornada_minutos).
    horas.update({w: m / 60 for w, m in jornada_minutos(datos, plan, fechas).items()})
    horas_ini = dict(horas)

    en_corre = set(corre)
    aplicados = 0
    for _ in range(MAX_MOV_COHERENCIA):
        # En un trueque del mismo día solo cambian DOS entradas: la semana de A y la de B. El delta
        # se calcula ahí y no recorriendo las 543 semanas por cada uno de los ~20.000 candidatos.
        porsem: dict[tuple[str, tuple[int, int]], list[str]] = defaultdict(list)
        pordia: dict[date, list[str]] = defaultdict(list)
        dentro = set(datos.fechas)
        for (w, f), s in plan.items():
            if w in en_corre:
                porsem[(w, semana(f))].append(s)
                if dentro is None or f in dentro:
                    pordia[f].append(w)    # solo se truecan días del año que se entrega
        mejor = None
        for f, gente in pordia.items():
            sm = semana(f)
            for i, A in enumerate(gente):
                for B in gente[i + 1:]:
                    sA, sB = plan[(A, f)], plan[(B, f)]
                    tA, tB = datos.turnos[sA], datos.turnos[sB]
                    if tA.tipo == tB.tipo and tA.municipio == tB.municipio:
                        continue                  # el trueque no cambiaría nada
                    if not (datos.elegible(A, sB, f)[0] and datos.elegible(B, sA, f)[0]):
                        continue
                    lA, lB = porsem[(A, sm)], porsem[(B, sm)]
                    nA, nB = list(lA), list(lB)
                    nA[nA.index(sA)] = sB
                    nB[nB.index(sB)] = sA
                    delta = ((_coste_semana(datos, nA) + _coste_semana(datos, nB))
                             - (_coste_semana(datos, lA) + _coste_semana(datos, lB)))
                    if delta >= 0 or (mejor is not None and delta >= mejor[0]):
                        continue
                    # sábados y domingos no se mueven (los dos trabajaban ESE día); el festivo sí
                    # podría, porque depende del municipio del turno, así que se descarta el caso
                    if datos.es_festivo(f, tA.municipio) != datos.es_festivo(f, tB.municipio):
                        continue
                    # jornada en la moneda del objetivo anual (los correturnos no hacen localizados,
                    # así que aquí horas_consumo == horas; se usa la misma que el resto del libro)
                    hA = horas[A] - tA.horas_consumo + tB.horas_consumo
                    hB = horas[B] - tB.horas_consumo + tA.horas_consumo
                    hs = dict(horas, **{A: hA, B: hB})
                    plan[(A, f)], plan[(B, f)] = sB, sA
                    ok = (_valida_tras_cambio(datos, plan, A, sm, hs, pares_ok, horas_ini)
                          and _valida_tras_cambio(datos, plan, B, sm, hs, pares_ok, horas_ini))
                    plan[(A, f)], plan[(B, f)] = sA, sB      # deshacer
                    if ok:
                        mejor = (delta, A, B, f, sA, sB, hA, hB)
        if mejor is None:
            break
        delta, A, B, f, sA, sB, hA, hB = mejor
        plan[(A, f)], plan[(B, f)] = sB, sA
        horas[A], horas[B] = hA, hB
        aplicados += 1
        if aplicados % 25 == 0:
            print(f"  {aplicados:>3} trueques · incoherencia "
                  f"{_incoherencia(datos, plan, corre):.0f}", flush=True)
    print(f"Coherencia: {aplicados} turnos intercambiados entre correturnos "
          f"(incoherencia final {_incoherencia(datos, plan, corre):.0f})", flush=True)
    return aplicados


# --------------------------------------------------------------------------- #
#  Aprovechar los refuerzos de calendario
# --------------------------------------------------------------------------- #
def _huecos_dia(datos: Datos, ocupado: Counter, f: date) -> list[str]:
    """Turnos con DEMANDA real sin cubrir ese día, uno por plaza que falte, del más crítico al menos
    (misma definición que salida.huecos_del_plan: los comodín no son demanda, luego no son hueco)."""
    huecos = []
    for s, t in datos.turnos.items():
        if t.prioridad >= 1 and datos.opera(s, f):
            huecos += [s] * max(0, t.dem - ocupado[(s, f)])
    return sorted(huecos, key=lambda s: -datos.turnos[s].prioridad)


def aprovechar(datos: Datos, plan: dict) -> int:
    """PRIMERA fase: cambia un REFUERZO DE CALENDARIO por un turno con demanda real sin cubrir del
    mismo día, cuando quien lo hace puede atenderlo.

    Un refuerzo (prioridad 0) es relleno: existe para dar horas a quien va corto, no responde a
    ninguna demanda. Dejar a alguien en un refuerzo mientras un turno real de ese mismo día se queda
    sin cubrir no beneficia a nadie, y el cambio es NEUTRO EN HORAS cuando los dos turnos computan lo
    mismo — que es el caso normal, ambos de jornada ordinaria.

    Qué queda aquí después de la corrección del modelo. La mayoría de esos refuerzos vienen prescritos
    por una rotación, y desviarse de un patrón costaba `PESO_DEV` (100) frente a los `PESO_COBERTURA`
    (10) de cubrir un turno normal, así que el modelo prefería, según sus pesos, mantener el refuerzo:
    medido sobre 2026, 70 de los 122 huecos del año tenían ese día a alguien capacitado en uno de
    ellos. Eso ya se arregló en su sitio (PESO_DEV_COMODIN), pero esta pasada sigue haciendo falta:
    el modelo solo ve su ventana de 14 días y solo mueve al que la rotación prescribe, mientras que
    aquí valen todos —también el correturno al que el relleno final le dejó un refuerzo— y con el año
    ya cerrado. Sobre el plan resuelto es una sustitución local que solo puede mejorar la cobertura.

    Invariantes: los días trabajados por cada uno NO cambian (mismo día, un turno por otro), luego el
    tope semanal de días, el fin de semana libre y el reparto de sábados y domingos quedan intactos
    por construcción. Se comprueban las tres cosas que sí pueden moverse: el descanso entre jornadas,
    las horas de la semana y el tope anual."""
    fechas = datos.fechas
    pares = _pares_pactados(datos)
    loc = lineas_localizadas(datos)
    ocupado: Counter = Counter()
    # `horas` = jornada contra el objetivo anual (moneda del libro); `hsem` = jornada LEGAL semanal,
    # que es la que topa config.hmax7. Son dos contabilidades distintas y aquí se mueven por separado.
    horas: dict[str, float] = defaultdict(float)
    horas.update({w: m / 60 for w, m in jornada_minutos(datos, plan, fechas).items()})
    hsem: dict[tuple[str, tuple[int, int]], float] = defaultdict(float)
    dia: dict[date, list[str]] = defaultdict(list)
    for (w, f), s in plan.items():
        if s not in datos.turnos:
            continue
        ocupado[(s, f)] += 1
        hsem[(w, semana(f))] += datos.turnos[s].horas
        dia[f].append(w)

    # Semana de cada uno, para elegir el turno que MEJOR SE ADAPTA: el que deja su semana más
    # parecida a sí misma (misma franja, mismo municipio), que es el criterio de `coherencia`.
    sem_turnos: dict[tuple[str, tuple[int, int]], list[str]] = defaultdict(list)
    for (w, f), s in plan.items():
        if s in datos.turnos:
            sem_turnos[(w, semana(f))].append(s)

    cambios, por_prio = 0, Counter()
    for f in fechas:
        # Las plazas de LOCALIZADO no se tapan con un refuerzo suelto: quien las coge asume la
        # semana entera con sus descansos (modelo._handover_critico), y aquí solo se cambia un día.
        huecos = [s for s in _huecos_dia(datos, ocupado, f) if s not in loc]
        if not huecos:
            continue
        libres = [w for w in dia.get(f, [])
                  if datos.turnos[plan[(w, f)]].prioridad <= 0]          # haciendo relleno
        for s in huecos:
            if not libres:
                break
            t = datos.turnos[s]
            mejor = None
            for w in libres:
                ref = plan[(w, f)]
                dh = t.horas - datos.turnos[ref].horas                    # legal (config.hmax7)
                dj = t.horas_consumo - datos.turnos[ref].horas_consumo    # jornada (objetivo anual)
                sm = semana(f)
                elegible, refuerzo = datos.elegible(w, s, f)
                if not elegible:
                    continue
                if hsem[(w, sm)] + dh > datos.config.hmax7:
                    continue
                if horas[w] + dj > datos.config.horas_objetivo * datos.trabajadores[w].factor_jornada:
                    continue
                if not _descanso_ok(datos, plan, w, f, s, pares):
                    continue
                # coste: cuánto desencaja su semana, más el desajuste de horas y, en último
                # término, preferir a quien puede hacerlo de forma ordinaria antes que de refuerzo
                resto = list(sem_turnos[(w, sm)])
                resto.remove(ref)
                coste = (_coste_semana(datos, resto + [s]) - _coste_semana(datos, sem_turnos[(w, sm)])
                         + abs(dh) / 8 + (1 if refuerzo else 0))
                if mejor is None or coste < mejor[0]:
                    mejor = (coste, w, ref, dh, dj)
            if mejor is None:
                continue
            _, w, ref, dh, dj = mejor
            sm = semana(f)
            plan[(w, f)] = s
            ocupado[(ref, f)] -= 1
            ocupado[(s, f)] += 1
            horas[w] += dj
            hsem[(w, sm)] += dh
            sem_turnos[(w, sm)].remove(ref)
            sem_turnos[(w, sm)].append(s)
            libres.remove(w)
            cambios += 1
            por_prio[t.prioridad] += 1
    detalle = ", ".join(f"prioridad {p}: {n}" for p, n in sorted(por_prio.items(), reverse=True))
    print(f"Refuerzos aprovechados: {cambios} cambiados por un turno real sin cubrir"
          + (f" ({detalle})" if detalle else ""), flush=True)
    return cambios


# --------------------------------------------------------------------------- #
#  Canjear refuerzos de CUALQUIER día del año por cobertura
# --------------------------------------------------------------------------- #
def _semanas_completas(fechas: list[date]) -> list[tuple[int, int]]:
    """Semanas ISO del horizonte con sus 7 días. Son las únicas que mira C7 (descanso semanal), igual
    criterio que el modelo: las truncadas del borde se ignoran."""
    cuenta = Counter(semana(f) for f in fechas)
    return sorted(s for s, n in cuenta.items() if n == 7)


def canjear(datos: Datos, plan: dict) -> int:
    """SEGUNDA fase: suelta REFUERZOS DE CALENDARIO de CUALQUIER día del año para poder cubrir un
    turno real que se quedó vacío. Devuelve el nº de huecos rescatados.

    Es `aprovechar` llevado al año entero. Aquel cambia un refuerzo por un turno real DEL MISMO DÍA,
    y con eso se rescata lo que se puede rescatar sin tocar el presupuesto de horas: el cambio es
    neutro. Pero el hueco típico que queda no es ese. Es este otro: hay alguien capacitado y LIBRE el
    día del hueco, y no puede cogerlo porque ya está en su objetivo anual de jornada — con horas que
    se le fueron en refuerzos de otros meses. Medido sobre 2026: de los 88 huecos que quedaban tras
    `aprovechar`, 36 tenían candidato libre y bloqueado por el tope de horas, y 23 de ellos lo tenían
    con refuerzos sueltos en el año.

    Un refuerzo (prioridad 0) es relleno: no responde a ninguna demanda, existe para dar horas a
    quien va corto. Así que la moneda está clara — un turno real sin cubrir vale más que cualquier
    refuerzo del año, esté en el mes que esté. Aquí se hace ese canje explícito: se le quitan al
    candidato los refuerzos justos para que le quepa el turno del hueco, y ni uno más.

    Por qué no lo hace el modelo. El rodante resuelve ventanas de 14 días: dentro de una ventana no
    existen ni el refuerzo de enero ni el hueco de agosto a la vez, así que el canje está fuera de su
    alcance por construcción — solo se ve con el año delante. (Lo que SÍ es suyo y ya hace es no
    preferir un refuerzo prescrito a un turno real dentro de la misma ventana: ver PESO_DEV_COMODIN.)

    Nunca se suelta un refuerzo "porque sí": solo cuando con ello se cubre un turno que si no queda
    vacío. Quien lo cede pierde como mucho las horas del último refuerzo soltado por debajo de su
    objetivo, y a cambio el cuadrante gana una plaza cubierta.

    Invariantes: se comprueban los mismos límites que el modelo impone —un turno al día, descanso
    entre jornadas (C4, con las exenciones de rotación pactada), días por semana (C5), 48 h semanales
    (C6), finde libre cada 4 semanas (C7) y tope anual de jornada—. Las plazas de LOCALIZADO quedan
    fuera: se ceden como semana entera con sus descansos, no como día suelto."""
    fechas = datos.fechas
    comodines = {s for s, t in datos.turnos.items() if t.prioridad == 0}
    if not comodines:
        return 0
    pares = _pares_pactados(datos)
    loc = lineas_localizadas(datos)
    semanas = _semanas_completas(fechas)
    pos = {sm: i for i, sm in enumerate(semanas)}

    # Libros que hay que mantener al día con cada canje. `horas` = jornada contra el objetivo anual
    # (moneda del libro); `hsem` = jornada LEGAL semanal, que es la que topa config.hmax7. Son dos
    # contabilidades distintas, igual que en `aprovechar`.
    ocupado: Counter = Counter()
    horas: dict[str, float] = defaultdict(float)
    horas.update({w: m / 60 for w, m in jornada_minutos(datos, plan, fechas).items()})
    hsem: dict[tuple[str, tuple[int, int]], float] = defaultdict(float)
    dias_sem: Counter = Counter()
    sem_turnos: dict[tuple[str, tuple[int, int]], list[str]] = defaultdict(list)
    findes: dict[str, set] = defaultdict(set)      # semanas en que trabaja sábado o domingo
    refs: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for (w, f), s in plan.items():
        sm = semana(f)
        ocupado[(s, f)] += 1
        hsem[(w, sm)] += datos.turnos[s].horas
        dias_sem[(w, sm)] += 1
        sem_turnos[(w, sm)].append(s)
        if f.weekday() >= 5:
            findes[w].add(sm)
        if s in comodines:
            refs[w].append((f, s))
    # Techo de jornada de cada uno: su objetivo anual o, si ya venía por encima —los localizados
    # cierran el año en ~1940 h por construcción—, lo que traía. Así al que ya está pasado no se le
    # carga ni un minuto más, pero sí puede cambiar un refuerzo por un turno real (jornada igual o
    # menor), que es justo lo que se busca.
    techo = {w: max(datos.config.horas_objetivo * t.factor_jornada, horas[w])
             for w, t in datos.trabajadores.items()}

    def finde_ok(w: str, sm: tuple[int, int]) -> bool:
        """C7: si empieza a trabajar el finde de `sm`, ¿le queda todavía un sábado+domingo libres en
        cada ventana de 4 semanas completas?"""
        i = pos.get(sm)
        if i is None:
            return True                            # semana truncada del borde: C7 no la mira
        ocupadas = findes[w] | {sm}
        for ini in range(max(0, i - 3), min(i, len(semanas) - 4) + 1):
            if all(semanas[j] in ocupadas for j in range(ini, ini + 4)):
                return False
        return True

    def a_soltar(w: str, f: date, falta: float) -> list[tuple[date, str]] | None:
        """Refuerzos del año que hay que quitarle a `w` para que le quepan `falta` horas de jornada.
        [] si no hace falta ninguno, None si ni soltándolos todos le cabe.

        Se prefieren los de la MISMA semana del hueco —ahí el canje es neutro también en días y en
        horas semanales, solo cambia el día— y después los de las semanas donde más días trabaja, que
        son las que menos echan de menos un relleno."""
        if falta <= 1e-6:
            return []
        sm = semana(f)
        candidatos = sorted((g, r) for g, r in refs[w] if g != f)
        candidatos.sort(key=lambda gr: (semana(gr[0]) != sm, -dias_sem[(w, semana(gr[0]))],
                                        abs((gr[0] - f).days)))
        sueltos = []
        for g, r in candidatos:
            sueltos.append((g, r))
            falta -= datos.turnos[r].horas_consumo
            if falta <= 1e-6:
                return sueltos
        return None

    def quitar(w: str, f: date) -> None:
        s = plan.pop((w, f))
        sm = semana(f)
        ocupado[(s, f)] -= 1
        horas[w] -= datos.turnos[s].horas_consumo
        hsem[(w, sm)] -= datos.turnos[s].horas
        dias_sem[(w, sm)] -= 1
        sem_turnos[(w, sm)].remove(s)
        if s in comodines:
            refs[w].remove((f, s))
        if f.weekday() >= 5 and not any(g.weekday() >= 5 for (ww, g) in plan
                                        if ww == w and semana(g) == sm):
            findes[w].discard(sm)

    def poner(w: str, f: date, s: str) -> None:
        sm = semana(f)
        plan[(w, f)] = s
        ocupado[(s, f)] += 1
        horas[w] += datos.turnos[s].horas_consumo
        hsem[(w, sm)] += datos.turnos[s].horas
        dias_sem[(w, sm)] += 1
        sem_turnos[(w, sm)].append(s)
        if f.weekday() >= 5:
            findes[w].add(sm)

    huecos = []
    for f in fechas:
        for s, t in datos.turnos.items():
            if t.prioridad >= 1 and s not in loc and datos.opera(s, f):
                huecos += [(f, s)] * max(0, t.dem - ocupado[(s, f)])
    huecos.sort(key=lambda fs: (-datos.turnos[fs[1]].prioridad, fs[0]))

    cambios, soltados, por_prio = 0, 0, Counter()
    for f, s in huecos:
        t = datos.turnos[s]
        if ocupado[(s, f)] >= t.dem:
            continue                               # ya lo tapó un canje anterior de este mismo bucle
        sm = semana(f)
        mejor = None
        for w in datos.trabajadores:
            actual = plan.get((w, f))
            if actual is not None and actual not in comodines:
                continue                           # ese día ya hace algo que sí responde a demanda
            elegible, es_ref = datos.elegible(w, s, f)
            if not elegible:
                continue
            if actual is None and any(datos.turnos[x].prioridad >= 2
                                      for x in sem_turnos[(w, sm)]):
                continue    # esa semana cubre una línea CRÍTICA: asumió la plaza entera con sus
                            # descansos (modelo._handover_critico), y sus días libres son parte de
                            # ella. Cambiarle un refuerzo por otro turno del mismo día sí vale.
            previo = datos.turnos[actual] if actual else None
            dj = t.horas_consumo - (previo.horas_consumo if previo else 0.0)
            sueltos = a_soltar(w, f, horas[w] + dj - techo[w])
            if sueltos is None:
                continue
            if not sueltos and actual is None:
                continue                           # sin refuerzo de por medio no es cosa de esta fase
            # Los refuerzos soltados en la semana DEL HUECO relajan también sus propios contadores
            fuera = [(g, r) for g, r in sueltos if semana(g) == sm]
            if dias_sem[(w, sm)] + (0 if actual else 1) - len(fuera) > _tope_dias(datos, w):
                continue
            h = (hsem[(w, sm)] + t.horas - (previo.horas if previo else 0.0)
                 - sum(datos.turnos[r].horas for _, r in fuera))
            if h > datos.config.hmax7 + 1e-6:
                continue
            if f.weekday() >= 5 and not finde_ok(w, sm):
                continue
            if not _descanso_ok(datos, plan, w, f, s, pares):
                continue
            resto = list(sem_turnos[(w, sm)])
            for x in ([actual] if actual else []) + [r for _, r in fuera]:
                resto.remove(x)
            coste = (len(sueltos), 1 if es_ref else 0,
                     _coste_semana(datos, resto + [s]) - _coste_semana(datos, sem_turnos[(w, sm)]))
            if mejor is None or coste < mejor[0]:
                mejor = (coste, w, actual, sueltos)
        if mejor is None:
            continue
        _, w, actual, sueltos = mejor
        for g, _r in sueltos:
            quitar(w, g)
        if actual:
            quitar(w, f)
        poner(w, f, s)
        cambios += 1
        soltados += len(sueltos)
        por_prio[t.prioridad] += 1
    detalle = ", ".join(f"prioridad {p}: {n}" for p, n in sorted(por_prio.items(), reverse=True))
    print(f"Refuerzos canjeados: {cambios} huecos cubiertos soltando {soltados} refuerzos del año"
          + (f" ({detalle})" if detalle else ""), flush=True)
    return cambios

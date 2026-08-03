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

import argparse
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cargar_datos import Datos, cargar                                    # noqa: E402
from modelo import (CMAX, CMAX_POOL, HMAX7, HORAS_OBJETIVO,                # noqa: E402
                    RMIN, rango_fechas, semana)

METRICAS_PULIDO = ("sabado", "domingo", "festivo")


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
        return CMAX_POOL
    from cargar_datos import DIAS, LIBRE
    propio = max(sum(1 for dia in DIAS if fila.get(dia) and fila.get(dia) != LIBRE
                     and fila.get(dia) in datos.turnos) for fila in filas)
    return min(CMAX, max(CMAX_POOL, propio))


def _descanso_ok(datos: Datos, plan: dict, w: str, f: date, s: str, pares_ok: set) -> bool:
    """¿Deja el turno s de w en el día f al menos RMIN horas con lo que tiene el día anterior y el
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
        if desc < RMIN:
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
    if sum(datos.turnos[s].horas for _, s in items) > HMAX7:
        return False
    # El pulido no puede usar la tolerancia sobre el objetivo: esa holgura existe para CUBRIR un
    # turno que si no quedaría vacío, no para recolocar findes. Así que nadie puede acabar por
    # encima de 1776 por un intercambio (ni empeorar si ya estaba por encima por cobertura).
    tope = max(HORAS_OBJETIVO * datos.trabajadores[w].factor_jornada, horas_previas.get(w, 0.0))
    if horas[w] > tope + 1e-6:
        return False
    for f, s in items:
        if not _descanso_ok(datos, plan, w, f, s, pares_ok):
            return False
    return True


def pulir(datos: Datos, plan: dict, inicio: date, fin: date,
          max_mov: int = 200, log: bool = True) -> int:
    """Bucle de mejora: busca el intercambio de semana que más baja la desigualdad y lo aplica,
    mientras siga mejorando. Devuelve el nº de intercambios aplicados.

    Invariantes DUROS (por construcción o comprobados): la cobertura no cambia —los mismos turnos
    siguen cubiertos, solo cambia quién—, nadie supera su tope anual ni los límites semanales, y el
    descanso entre jornadas se revalida en las costuras, que es donde un cambio de semana puede
    romperlo."""
    pares_ok = _pares_pactados(datos)
    grupos = _grupos(datos)
    horas: dict[str, float] = defaultdict(float)
    for (w, f), s in plan.items():
        horas[w] += datos.turnos[s].horas

    horas_ini = dict(horas)        # jornada de partida: el pulido no puede empeorarla
    cargas = _cargas(datos, plan)
    aplicados = 0
    for _ in range(max_mov):
        porsem = _semanas(plan)
        # Aporte de cada semana a las métricas y a las horas: se calcula UNA vez y se reutiliza
        # para todos los candidatos. Evaluar un intercambio pasa a ser restar y sumar tres números
        # en vez de copiar el plan entero y recontarlo (miles de millones de operaciones por
        # movimiento con 703 parejas × 53 semanas).
        aporte: dict[tuple[str, tuple[int, int]], tuple[int, int, int, float]] = {}
        for k, items in porsem.items():
            sab = sum(1 for f, _ in items if f.weekday() == 5)
            dom = sum(1 for f, _ in items if f.weekday() == 6)
            fes = sum(1 for f, s in items if datos.es_festivo(f, datos.turnos[s].municipio))
            aporte[k] = (sab, dom, fes, sum(datos.turnos[s].horas for _, s in items))

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
        if log:
            print(f"  {aplicados:>3}. semana {sm[1]:>2}: {A} <-> {B}   "
                  f"Σrangos {delta:+.0f}", flush=True)
    if log:
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


def coherencia(datos: Datos, plan: dict, max_mov: int = 400, log: bool = True) -> int:
    """SEGUNDA fase del pulido: intercambia turnos DEL MISMO DÍA entre correturnos para que cada uno
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
    corre = [w for w, t in datos.trabajadores.items() if t.tipo == "correturno"]
    if len(corre) < 2:
        return 0
    pares_ok = _pares_pactados(datos)
    grupos = _grupos(datos)
    horas: dict[str, float] = defaultdict(float)
    for (w, f), s in plan.items():
        horas[w] += datos.turnos[s].horas
    horas_ini = dict(horas)

    en_corre = set(corre)
    aplicados = 0
    for _ in range(max_mov):
        # En un trueque del mismo día solo cambian DOS entradas: la semana de A y la de B. El delta
        # se calcula ahí y no recorriendo las 543 semanas por cada uno de los ~20.000 candidatos.
        porsem: dict[tuple[str, tuple[int, int]], list[str]] = defaultdict(list)
        pordia: dict[date, list[str]] = defaultdict(list)
        for (w, f), s in plan.items():
            if w in en_corre:
                porsem[(w, semana(f))].append(s)
                pordia[f].append(w)
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
                    hA = horas[A] - tA.horas + tB.horas
                    hB = horas[B] - tB.horas + tA.horas
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
        if log and aplicados % 25 == 0:
            print(f"  {aplicados:>3} trueques · incoherencia "
                  f"{_incoherencia(datos, plan, corre):.0f}", flush=True)
    if log:
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


def aprovechar(datos: Datos, plan: dict, fechas: list[date], log: bool = True) -> int:
    """TERCERA fase: cambia un REFUERZO DE CALENDARIO por un turno con demanda real sin cubrir del
    mismo día, cuando quien lo hace puede atenderlo.

    Un refuerzo (prioridad 0) es relleno: existe para dar horas a quien va corto, no responde a
    ninguna demanda. Dejar a alguien en un refuerzo mientras un turno real de ese mismo día se queda
    sin cubrir no beneficia a nadie, y el cambio es NEUTRO EN HORAS cuando los dos turnos computan lo
    mismo — que es el caso normal, ambos de jornada ordinaria.

    Por qué no lo hace el modelo. La mayoría de esos refuerzos vienen prescritos por una rotación, y
    sacar a alguien de su patrón cuesta `PESO_DEV` (100), mientras que cubrir un turno normal vale
    `PESO_COBERTURA` (10). El modelo prefiere, correctamente según sus pesos, mantener el patrón. Pero
    esos pesos se calibraron pensando en turnos prescritos DE VERDAD: un refuerzo no es una posición
    que defender, y la asimetría no era intencionada. Medido sobre 2026: 70 de los 122 huecos del año
    tenían ese día a alguien capacitado haciendo un refuerzo, 56 de ellos en agosto.

    Cambiarlo dentro del modelo obligaría a que el coste de desviarse dependiese del turno prescrito,
    y ese peso está entretejido con la equidad y la estabilidad. Aquí, sobre el plan ya resuelto, es
    una sustitución local que solo puede mejorar la cobertura.

    Invariantes: los días trabajados por cada uno NO cambian (mismo día, un turno por otro), luego el
    tope semanal de días, el fin de semana libre y el reparto de sábados y domingos quedan intactos
    por construcción. Se comprueban las tres cosas que sí pueden moverse: el descanso entre jornadas,
    las horas de la semana y el tope anual."""
    pares = _pares_pactados(datos)
    ocupado: Counter = Counter()
    horas: dict[str, float] = defaultdict(float)
    hsem: dict[tuple[str, tuple[int, int]], float] = defaultdict(float)
    dia: dict[date, list[str]] = defaultdict(list)
    for (w, f), s in plan.items():
        if s not in datos.turnos:
            continue
        ocupado[(s, f)] += 1
        horas[w] += datos.turnos[s].horas
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
        huecos = _huecos_dia(datos, ocupado, f)
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
                dh = t.horas - datos.turnos[ref].horas
                sm = semana(f)
                elegible, refuerzo = datos.elegible(w, s, f)
                if not elegible:
                    continue
                if hsem[(w, sm)] + dh > HMAX7:
                    continue
                if horas[w] + dh > HORAS_OBJETIVO * datos.trabajadores[w].factor_jornada:
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
                    mejor = (coste, w, ref, dh)
            if mejor is None:
                continue
            _, w, ref, dh = mejor
            sm = semana(f)
            plan[(w, f)] = s
            ocupado[(ref, f)] -= 1
            ocupado[(s, f)] += 1
            horas[w] += dh
            hsem[(w, sm)] += dh
            sem_turnos[(w, sm)].remove(ref)
            sem_turnos[(w, sm)].append(s)
            libres.remove(w)
            cambios += 1
            por_prio[t.prioridad] += 1
    if log:
        detalle = ", ".join(f"prioridad {p}: {n}" for p, n in sorted(por_prio.items(), reverse=True))
        print(f"Refuerzos aprovechados: {cambios} cambiados por un turno real sin cubrir"
              + (f" ({detalle})" if detalle else ""), flush=True)
    return cambios


def main() -> int:
    p = argparse.ArgumentParser(description="Pule la equidad de findes/festivos de un plan")
    p.add_argument("plan", help="CSV con id_trab,fecha,id_turno")
    p.add_argument("--anio", type=int, default=2026)
    p.add_argument("--datos", default=str(Path(__file__).resolve().parents[1] / "data" / "input"))
    p.add_argument("--max-mov", type=int, default=200)
    p.add_argument("--sin-coherencia", action="store_true",
                   help="omite la 2ª fase (semanas coherentes de los correturnos)")
    p.add_argument("--salida", help="CSV donde escribir el plan pulido")
    a = p.parse_args()

    import csv
    datos = cargar(a.datos)
    plan = {}
    with open(a.plan, encoding="utf-8") as fh:
        for w, f, s in csv.reader(fh):
            plan[(w, date.fromisoformat(f))] = s
    inicio, fin = date(a.anio, 1, 1), date(a.anio, 12, 31)

    resumen(datos, plan, "ANTES del pulido")
    pulir(datos, plan, inicio, fin, max_mov=a.max_mov)
    aprovechar(datos, plan, rango_fechas(inicio - timedelta(days=inicio.weekday()), fin))
    if not a.sin_coherencia:
        coherencia(datos, plan)
    resumen(datos, plan, "DESPUÉS del pulido")

    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            for (w, f), s in sorted(plan.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                fh.write(f"{w},{f.isoformat()},{s}\n")
        print(f"\nplan pulido en {a.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

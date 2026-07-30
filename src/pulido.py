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
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cargar_datos import Datos, cargar                                    # noqa: E402
from modelo import (CMAX, CMAX_POOL, HMAX7, HMAX_AÑO, HORAS_OBJETIVO,     # noqa: E402
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


def main() -> int:
    p = argparse.ArgumentParser(description="Pule la equidad de findes/festivos de un plan")
    p.add_argument("plan", help="CSV con id_trab,fecha,id_turno")
    p.add_argument("--anio", type=int, default=2026)
    p.add_argument("--datos", default=str(Path(__file__).resolve().parents[1] / "data" / "input"))
    p.add_argument("--max-mov", type=int, default=200)
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
    resumen(datos, plan, "DESPUÉS del pulido")

    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            for (w, f), s in sorted(plan.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                fh.write(f"{w},{f.isoformat()},{s}\n")
        print(f"\nplan pulido en {a.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

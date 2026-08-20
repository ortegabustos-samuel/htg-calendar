#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnostico.py — Radiografía ESTRUCTURAL de un juego de datos, sin resolver nada.

Responde, para CUALQUIER zona (no solo Valladolid), las preguntas que deciden si el
cuadrante es siquiera posible y qué maquinaria hace falta. Todo se DERIVA de los CSV;
no hay semántica de turno (noche/UVI/localizado) escrita en el código:

  1. BALANCE      ¿la plantilla da las horas que pide la demanda, a `objetivo` h/año?
                  Si no, ningún solver lo arregla: faltan FTE (dato para la empresa).
  2. PATRONES     horas/año que PRESCRIBE cada patrón vs el objetivo. El excedente es lo
                  que hay que CEDER (libranzas); el defecto, lo que sobra de capacidad.
                  Se reporta en unidades naturales de cesión: semana y periodo completo.
  3. EXENCIONES   qué límites legales incumple cada rotación PACTADA por sí misma. Es la
                  única justificación para eximirla; lo demás debe cumplir el convenio.
  4. LÍNEAS       profundidad de cobertura de cada turno (titulares/cubridores): dónde un
                  solo hueco de disponibilidad deja la línea sin nadie.

Uso:  python3 src/diagnostico.py
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from v3.cargar_datos import DIAS, LIBRE, Datos, cargar
from modelo import jornada_minutos


def _rango(inicio: date, fin: date) -> list[date]:
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


def _intervalo(f: date, t) -> tuple[datetime, datetime]:
    ini = datetime.combine(f, t.hora_entrada)
    fin = datetime.combine(f, t.hora_salida)
    if fin <= ini:
        fin += timedelta(days=1)
    return ini, fin


# --------------------------------------------------------------------------- #
def balance(datos: Datos, fechas: list[date]) -> None:
    """¿Cuadra la aritmética gruesa? Horas que EXIGE la demanda del año frente a las que
    APORTA la plantilla a `objetivo` h/año. Si el balance es negativo, el cuadrante es
    imposible sin huecos o sin superar el objetivo: es un problema de plantilla, no de
    solver. Se da en las dos métricas: computada (legal) y de consumo (capacidad real,
    que es mayor cuando hay localizados)."""
    objetivo = datos.config.horas_objetivo
    dem_h = dem_c = 0.0
    for s, t in datos.turnos.items():
        if t.prioridad < 1:                     # comodines: no son demanda
            continue
        dias = sum(1 for f in fechas if datos.opera(s, f))
        dem_h += dias * t.dem * t.horas
        dem_c += dias * t.dem * t.horas_consumo
    cap = sum(objetivo * w.factor_jornada for w in datos.trabajadores.values())
    n = len(datos.trabajadores)

    print("=" * 78)
    print(f"1. BALANCE ANUAL  ({fechas[0]:%d/%m/%Y}–{fechas[-1]:%d/%m/%Y}, objetivo {objetivo} h)")
    print("=" * 78)
    print(f"  plantilla: {n} trabajadores  ->  capacidad {cap:>10,.0f} h")
    print(f"  demanda computada:              {dem_h:>10,.0f} h   "
          f"balance {cap-dem_h:>+9,.0f} h ({(cap-dem_h)/objetivo:+.1f} FTE)")
    print(f"  demanda de consumo:             {dem_c:>10,.0f} h   "
          f"balance {cap-dem_c:>+9,.0f} h ({(cap-dem_c)/objetivo:+.1f} FTE)")
    print(f"  media exigida por trabajador:   {dem_h/n:>10,.0f} h computadas / "
          f"{dem_c/n:,.0f} h de consumo")
    if cap < dem_c:
        print("  >> DÉFICIT: no hay horas para la demanda. Habrá huecos o exceso de jornada.")
    else:
        print("  >> Hay holgura: el objetivo es alcanzable si el reparto lo permite.")


# --------------------------------------------------------------------------- #
def _prescripcion(datos: Datos, patron: str, trabs: list[str],
                  fechas: list[date], ancla: date) -> dict[str, list[tuple[date, str]]]:
    """(trabajador -> [(fecha, turno)]) que la rotación del patrón prescribe en el periodo.
    Misma regla que modelo._prescripcion_patron: una fila por semana desde el ancla, y cada
    trabajador del grupo arranca en la fila que le fija `datos.offsets` (declarada en
    trabajadores.csv, o el orden del grupo si no se declara)."""
    filas = datos.patrones.get(patron) or []
    T = len(filas)
    pres: dict[str, list[tuple[date, str]]] = {}
    for w in sorted(trabs):
        off = datos.offsets.get(w, 0)
        items = []
        for f in fechas:
            s = filas[(off + (f - ancla).days // 7) % T][DIAS[f.weekday()]]
            if s and s != LIBRE and s in datos.turnos:
                items.append((f, s))
        pres[w] = items
    return pres


def patrones(datos: Datos, fechas: list[date], ancla: date) -> None:
    """Horas/año que PRESCRIBE cada patrón (contando solo días en que la línea opera y el
    trabajador está disponible) frente al objetivo. El EXCEDENTE es la cantidad que ese
    patrón debe ceder al año — el mecanismo de 'libranzas' NO es propio de las noches: lo
    necesita todo patrón con excedente. Se expresa en unidades de cesión para que se vea
    cuántas hay que liberar: una SEMANA de la rotación y un PERIODO completo (len(filas))."""
    objetivo = datos.config.horas_objetivo
    grupos: dict[str, list[str]] = defaultdict(list)
    for w, t in datos.trabajadores.items():
        if t.tipo == "patron" and t.patron:
            grupos[t.patron].append(w)

    print("\n" + "=" * 78)
    print(f"2. PATRONES: horas prescritas vs objetivo ({objetivo} h)")
    print("=" * 78)
    print(f"{'patron':<18} {'per':>4} {'h/año':>7} {'jornada':>8} {'Δ obj':>7} "
          f"{'h/sem':>6} {'ceder':>13}")
    print("-" * 78)
    for p, trabs in sorted(grupos.items()):
        filas = datos.patrones.get(p)
        if not filas:
            continue
        pres = _prescripcion(datos, p, trabs, fechas, ancla)
        hs, cs = [], []
        for w, items in pres.items():
            reales = {(w, f): s for f, s in items
                      if datos.opera(s, f) and datos.disponible(w, f)}
            hs.append(sum(datos.turnos[s].horas for s in reales.values()))
            cs.append(jornada_minutos(datos, reales, fechas).get(w, 0) / 60)
        med, medc = sum(hs) / len(hs), sum(cs) / len(cs)
        delta = medc - objetivo          # lo que cuenta contra el objetivo es la JORNADA, no la legal
        # unidades de cesión: jornada de una semana media y de un periodo completo
        h_periodo = sum(jornada_minutos(datos, {("·", ancla + timedelta(days=7 * i + j)):
                                                filas[i][DIAS[j]]
                                                for i in range(len(filas)) for j in range(7)
                                                if filas[i][DIAS[j]] not in (None, "", LIBRE)
                                                and filas[i][DIAS[j]] in datos.turnos}).values()) / 60
        h_sem = h_periodo / len(filas)
        ceder = (f"{delta/h_sem:.1f} sem = {delta/h_periodo:.2f} per" if delta > 0 else "—")
        print(f"{p:<18} {len(filas):>4} {med:>7.0f} {medc:>8.0f} {delta:>+7.0f} "
              f"{h_sem:>6.0f} {ceder:>13}")
    print("-" * 78)
    print("  Δ obj > 0: hay que CEDER esas horas (libranzas) para aterrizar en el objetivo.")
    print("  Δ obj < 0: el patrón deja capacidad libre (puede absorber coberturas).")
    print("  'h/año' = horas LEGALES computadas (las del convenio, C5/C6/C7).")
    print("  'jornada' = lo que la plaza OCUPA, que es lo que cuenta contra el objetivo anual:")
    print("  una semana de localizado computa entera (ver modelo.JORNADA_LOCALIZADO_SEMANA), y por")
    print("  eso en esas líneas 'jornada' >> 'h/año'.")


# --------------------------------------------------------------------------- #
def exenciones(datos: Datos) -> None:
    """¿Qué límites legales incumple cada rotación PACTADA por sí misma? Solo eso justifica
    eximirla (la conciliación pactada con los sindicatos). Un patrón que cumple los límites
    no necesita exención alguna; y ningún trabajador debería estar exento en las semanas en
    que NO sigue su rotación (p.ej. cuando cubre otra línea): ahí manda el convenio."""
    cfg = datos.config
    print("\n" + "=" * 78)
    print("3. EXENCIONES LEGALES QUE LA ROTACIÓN PACTADA REALMENTE NECESITA")
    print("=" * 78)
    print(f"{'patron':<18} {'max h/sem':>9} {'max d/sem':>9} {'min desc.':>9}   "
          f"{f'C6>{cfg.hmax7}h':>6} {f'C5>{cfg.cmax}d':>6} {f'C4<{cfg.rmin}h':>7}")
    print("-" * 78)
    base = date(2026, 1, 5)                    # un lunes cualquiera: solo importa la forma
    for p, filas in sorted(datos.patrones.items()):
        T = len(filas)
        maxh = maxd = 0
        for fila in filas:
            h = nd = 0
            for dia in DIAS:
                s = fila.get(dia)
                if s and s != LIBRE and s in datos.turnos:
                    h += datos.turnos[s].horas
                    nd += 1
            maxh, maxd = max(maxh, h), max(maxd, nd)
        # descanso mínimo entre días consecutivos, recorriendo el ciclo entero (con costuras)
        peor = float("inf")
        for k in range(T):
            for j in range(7):
                f1 = base + timedelta(days=k * 7 + j)
                f2 = f1 + timedelta(days=1)
                s1 = filas[k % T][DIAS[f1.weekday()]]
                s2 = filas[((k * 7 + j + 1) // 7) % T][DIAS[f2.weekday()]]
                if not s1 or s1 == LIBRE or s1 not in datos.turnos:
                    continue
                if not s2 or s2 == LIBRE or s2 not in datos.turnos:
                    continue
                _, fin1 = _intervalo(f1, datos.turnos[s1])
                ini2, _ = _intervalo(f2, datos.turnos[s2])
                peor = min(peor, (ini2 - fin1).total_seconds() / 3600)
        marca = lambda b: "SÍ" if b else "no"          # noqa: E731
        print(f"{p:<18} {maxh:>9.0f} {maxd:>9} {peor:>9.1f}   "
              f"{marca(maxh > cfg.hmax7):>6} {marca(maxd > cfg.cmax):>6} {marca(peor < cfg.rmin):>7}")
    print("-" * 78)
    print("  'no' en las tres = ese patrón NO necesita ninguna exención: debe cumplir el")
    print("  convenio como el resto de la plantilla.")


# --------------------------------------------------------------------------- #
def lineas(datos: Datos, fechas: list[date]) -> None:
    """Profundidad de cobertura por turno: cuánta gente puede hacerlo (titulares con v=0 y
    cubridores con v=1). Una línea crítica con pocos cubridores es un punto único de fallo:
    basta que coincidan vacaciones para dejarla sin nadie, y eso NO lo arregla el solver."""
    elig: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for (w, s), c in datos.capacidades.items():
        elig[s][c.v].append(w)

    print("\n" + "=" * 78)
    print("4. PROFUNDIDAD DE COBERTURA POR LÍNEA (prioridad >= 1)")
    print("=" * 78)
    print(f"{'turno':<12} {'prio':>4} {'dem':>3} {'días/año':>8} {'titulares':>9} "
          f"{'cubridores':>10}   aviso")
    print("-" * 78)
    for s, t in sorted(datos.turnos.items(), key=lambda kv: (-kv[1].prioridad, kv[0])):
        if t.prioridad < 1:
            continue
        dias = sum(1 for f in fechas if datos.opera(s, f))
        tit, cub = len(elig[s][0]), len(elig[s][1])
        aviso = ""
        if t.prioridad >= 2 and cub <= 1:
            aviso = "<< punto único de fallo"
        elif tit + cub <= t.dem:
            aviso = "<< sin margen"
        print(f"{s:<12} {t.prioridad:>4} {t.dem:>3} {dias:>8} {tit:>9} {cub:>10}   {aviso}")


# --------------------------------------------------------------------------- #
def main() -> None:
    datos = cargar()
    # El rodante arranca el lunes anterior al 1 de enero; el diagnóstico mira el mismo tramo para
    # que sus cifras sean las que verá el modelo.
    inicio = datos.inicio - timedelta(days=datos.inicio.weekday())
    fechas = _rango(inicio, datos.fin)

    balance(datos, fechas)
    patrones(datos, fechas, ancla=inicio)
    exenciones(datos)
    lineas(datos, fechas)
    print("=" * 78)


if __name__ == "__main__":
    main()

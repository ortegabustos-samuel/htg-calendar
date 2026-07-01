#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validar_datos.py — Comprobaciones de coherencia de los 6 CSV antes de resolver.

Recorre la instancia cargada y reporta problemas clasificados en:
  * ERROR  — rompería el modelo (referencias rotas, duplicados, días imposibles).
  * AVISO  — sospechoso pero no fatal (cobertura estructural, solapes, formato).

No corrige nada: solo informa. Ejecutado como script imprime la lista y sale con
código 1 si hay algún ERROR.
"""
from __future__ import annotations

from pathlib import Path

from cargar_datos import DATOS_DEF, DIAS, LIBRE, Datos, _leer, cargar


# --------------------------------------------------------------------------- #
#  Chequeos (cada uno añade problemas a la lista)
# --------------------------------------------------------------------------- #
def _referencias_capacidades(directorio: Path, datos: Datos, error, aviso) -> None:
    """Trabajador/turno de cada fila existen; sin filas (trab, turno) duplicadas."""
    df = _leer(directorio, "capacidades.csv")
    vistos = set()
    for _, fila in df.iterrows():
        trab, turno = fila["id_trab"], fila["id_turno"]
        if trab not in datos.trabajadores:
            error(f"capacidades: trabajador desconocido '{trab}'")
        if turno not in datos.turnos:
            error(f"capacidades: turno desconocido '{turno}' (trabajador {trab})")
        if (trab, turno) in vistos:
            error(f"capacidades: fila duplicada ({trab}, {turno})")
        vistos.add((trab, turno))


def _dias_capacidad(datos: Datos, error, aviso) -> None:
    """Los días marcados en una capacidad deben estar entre los días que opera el turno;
    V va sola; una fila normal debe marcar al menos un día."""
    for (trab, turno), cap in datos.capacidades.items():
        if turno not in datos.turnos:
            continue                                   # ya reportado en referencias
        t = datos.turnos[turno]
        dias_marcados = cap.lv or cap.sab or cap.dom or cap.fest

        if cap.v == 1 and dias_marcados:
            aviso(f"capacidades ({trab}, {turno}): v=1 debería ir sola, sin días")
        if cap.v == 0 and not dias_marcados:
            aviso(f"capacidades ({trab}, {turno}): fila normal sin ningún día marcado")

        for dia, en_capacidad, opera_turno in [
            ("LV", cap.lv, t.lv), ("SAB", cap.sab, t.sab),
            ("DOM", cap.dom, t.dom), ("FEST", cap.fest, t.fes),
        ]:
            if en_capacidad and not opera_turno:
                error(f"capacidades ({trab}, {turno}): marca {dia} pero el turno no opera ese día")


def _patrones(datos: Datos, error, aviso) -> None:
    """Cada trabajador de patrón apunta a un patrón existente; el nº de filas del patrón
    coincide con el nº de sus trabajadores; los turnos de la matriz existen."""
    miembros: dict[str, list[str]] = {}
    for w in datos.trabajadores.values():
        if w.tipo != "patron":
            continue
        if not w.patron:
            error(f"trabajador {w.id}: es de patrón pero no indica cuál")
            continue
        miembros.setdefault(w.patron, []).append(w.id)
        if w.patron not in datos.patrones:
            error(f"trabajador {w.id}: patrón '{w.patron}' no existe en patrones.csv")

    for patron, filas in datos.patrones.items():
        n_trab = len(miembros.get(patron, []))
        if n_trab != len(filas):
            aviso(f"patrón {patron}: {len(filas)} filas pero {n_trab} trabajadores asignados")
        for indice, semana in enumerate(filas):
            for dia, turno in semana.items():
                if turno != LIBRE and turno not in datos.turnos:
                    error(f"patrón {patron} fila {indice} ({dia}): turno desconocido '{turno}'")


def _calendarios(datos: Datos, error, aviso) -> None:
    """Todo municipio con turnos tiene un calendario de festivos asignado."""
    for municipio in sorted({t.municipio for t in datos.turnos.values()}):
        if municipio not in datos.calendario_municipio:
            aviso(f"municipio '{municipio}' sin calendario de festivos asignado")


def _cobertura_posible(datos: Datos, error, aviso) -> None:
    """Cada turno debería tener al menos un trabajador con capacidad NORMAL (no solo refuerzo),
    o sería imposible de cubrir en régimen ordinario."""
    con_normal = set()
    for (trab, turno), cap in datos.capacidades.items():
        if cap.v == 0 and (cap.lv or cap.sab or cap.dom or cap.fest):
            con_normal.add(turno)
    for turno in datos.turnos:
        if turno not in con_normal:
            aviso(f"turno {turno}: ningún trabajador con capacidad normal (solo refuerzo)")


def _vacaciones(datos: Datos, error, aviso) -> None:
    """Las dos quincenas de un trabajador no deberían solaparse."""
    for w in datos.trabajadores.values():
        periodos = sorted(w.vacaciones)
        for (ini_a, fin_a), (ini_b, fin_b) in zip(periodos, periodos[1:]):
            if ini_b <= fin_a:
                aviso(f"trabajador {w.id}: sus dos quincenas de vacaciones se solapan")


# --------------------------------------------------------------------------- #
#  Orquestación
# --------------------------------------------------------------------------- #
def validar(directorio: Path | str = DATOS_DEF) -> list[tuple[str, str]]:
    """Devuelve la lista de problemas como tuplas (nivel, mensaje)."""
    directorio = Path(directorio)
    datos = cargar(directorio)

    problemas: list[tuple[str, str]] = []
    error = lambda msg: problemas.append(("ERROR", msg))
    aviso = lambda msg: problemas.append(("AVISO", msg))

    _referencias_capacidades(directorio, datos, error, aviso)
    _dias_capacidad(datos, error, aviso)
    _patrones(datos, error, aviso)
    _calendarios(datos, error, aviso)
    _cobertura_posible(datos, error, aviso)
    _vacaciones(datos, error, aviso)
    return problemas


def main() -> int:
    problemas = validar()
    for nivel, mensaje in problemas:
        print(f"[{nivel}] {mensaje}")

    errores = sum(1 for nivel, _ in problemas if nivel == "ERROR")
    avisos = len(problemas) - errores
    print(f"\n{errores} errores, {avisos} avisos")
    return 1 if errores else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cargar_datos.py — Capa de carga de datos del generador de cuadrantes.

Lee los 6 CSV de data/input/ con pandas, deriva lo que el modelo necesita (duración,
nocturnidad, tipo, operatividad) y expone consultas:
  * opera(turno, fecha)          — ¿la línea opera ese día? (festivo manda sobre día de semana)
  * disponible(trab, fecha)      — ¿no está de vacaciones?
  * tipo_dia(fecha, municipio)   — LV / SAB / DOM / FEST
  * elegible(trab, turno, fecha) — (elegible?, es_refuerzo?)  a partir de capacidades

Base sobre la que se apoyan modelo.py / validar_datos.py. Ejecutado como script imprime
un resumen y comprobaciones de la instancia cargada.
"""
from __future__ import annotations

import sys

# --- Workaround de entorno (Anaconda) ---------------------------------------
# pyarrow (conda) enlaza libprotobuf 5.29 y OR-Tools trae libprotobuf 6.33; cargar ambas en el
# mismo proceso rompe la importación de ortools ("File already exists in database"). No usamos
# pyarrow (read_csv va con el parser C de pandas), así que evitamos que pandas lo cargue. En un
# entorno sin ese conflicto esta línea es inofensiva y se puede quitar.
sys.modules.setdefault("pyarrow", None)
# ----------------------------------------------------------------------------

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATOS_DEF = RAIZ / "data" / "input"

DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]   # patrones.csv; índice = weekday()
LIBRE = "LIBRE"
DUR_GUARDIA = 22          # h a partir de las cuales el turno es de guardia (24 h)
HORAS_PARTIDO = 8         # un turno partido computa 8 h (no sabemos la duración del corte)
HORAS_GUARDIA = 8         # una guardia de 24 h computa 8 h de trabajo efectivo (resto: pausa)


# --------------------------------------------------------------------------- #
#  Derivaciones horarias
# --------------------------------------------------------------------------- #
def hora_a_float(h: str) -> float:
    hh, mm = str(h).strip().split(":")
    return int(hh) + int(mm) / 60.0


def duracion(entrada: str, salida: str) -> float:
    d = hora_a_float(salida) - hora_a_float(entrada)
    return d + 24 if d <= 0 else d          # la salida puede caer al día siguiente


def nocturnidad(entrada: str, dur: float) -> float:
    """Horas de solape con la franja nocturna 22:00-06:00 (replicada por cruce de medianoche)."""
    ini = hora_a_float(entrada)
    fin = ini + dur
    return sum(max(0.0, min(fin, b) - max(ini, a)) for a, b in [(0, 6), (22, 30), (46, 48)])


def tipo_turno(entrada: str, dur: float, partido: int) -> str:
    e = hora_a_float(entrada)
    if partido:
        return "partido"
    if dur >= DUR_GUARDIA:
        return "24h"
    if e >= 20 or e < 6:
        return "noche"
    if e < 12:
        return "mañana"
    return "tarde"


def _fecha(texto: str) -> date:
    return datetime.strptime(str(texto).strip(), "%d/%m/%Y").date()


def _quincena(inicio: str) -> tuple[date, date]:
    """Periodo de vacaciones de 15 días: (inicio, inicio + 14) a partir de su fecha de inicio."""
    ini = _fecha(inicio)
    return (ini, ini + timedelta(days=14))


def _entero(valor, defecto: int) -> int:
    """Convierte a int; devuelve el valor por defecto si la celda viene vacía."""
    texto = str(valor).strip()
    return int(texto) if texto else defecto


# --------------------------------------------------------------------------- #
#  Estructuras del dominio
# --------------------------------------------------------------------------- #
@dataclass
class Turno:
    id: str
    municipio: str
    lv: int
    sab: int
    dom: int
    fes: int
    hora_entrada: str
    hora_salida: str
    dem: int
    partido: int
    dur: float          # duración real (salida - entrada), para derivar tipo/nocturnidad
    horas: float        # horas efectivas computables para la jornada (partido y 24h -> 8)
    noct: float
    tipo: str


@dataclass
class Trabajador:
    id: str
    tipo: str                       # fijo | patron | correturno | mixto
    patron: str | None              # id del patrón (solo tipo=patron)
    vacaciones: list[tuple[date, date]]


@dataclass
class Capacidad:
    lv: int
    sab: int
    dom: int
    fest: int
    v: int                          # refuerzo (cobertura de ausencias); va solo


@dataclass
class Datos:
    turnos: dict[str, Turno]
    trabajadores: dict[str, Trabajador]
    calendario_municipio: dict[str, str]           # municipio -> calendario de festivos
    festivos: dict[str, set[date]]                 # ambito(calendario) -> fechas
    capacidades: dict[tuple[str, str], Capacidad]  # (id_trab, id_turno) -> flags
    patrones: dict[str, list[dict[str, str]]]      # patron -> [ {weekday: turno|LIBRE} ]

    # -- Consultas derivadas ------------------------------------------------- #
    def es_festivo(self, f: date, municipio: str) -> bool:
        calendario = self.calendario_municipio.get(municipio, municipio)
        return f in self.festivos.get("Comun", set()) or f in self.festivos.get(calendario, set())

    def tipo_dia(self, f: date, municipio: str) -> str:
        if self.es_festivo(f, municipio):
            return "FEST"
        wd = f.weekday()
        return "LV" if wd < 5 else "SAB" if wd == 5 else "DOM"

    def opera(self, turno_id: str, f: date) -> bool:
        t = self.turnos[turno_id]
        if self.es_festivo(f, t.municipio):
            return t.fes == 1
        wd = f.weekday()
        return (t.lv if wd < 5 else t.sab if wd == 5 else t.dom) == 1

    def disponible(self, trab_id: str, f: date) -> bool:
        return not any(ini <= f <= fin for ini, fin in self.trabajadores[trab_id].vacaciones)

    def elegible(self, trab_id: str, turno_id: str, f: date) -> tuple[bool, bool]:
        """(elegible, es_refuerzo). Solo si la línea opera y el trabajador está disponible.
        Normal si su capacidad cubre el tipo de día; si no, de refuerzo si tiene v=1."""
        if not self.opera(turno_id, f) or not self.disponible(trab_id, f):
            return (False, False)
        cap = self.capacidades.get((trab_id, turno_id))
        if cap is None:
            return (False, False)
        td = self.tipo_dia(f, self.turnos[turno_id].municipio)
        normal = {"LV": cap.lv, "SAB": cap.sab, "DOM": cap.dom, "FEST": cap.fest}[td] == 1
        if normal:
            return (True, False)
        if cap.v == 1:
            return (True, True)
        return (False, False)


# --------------------------------------------------------------------------- #
#  Carga: una función por fichero (código llano, no optimizado a propósito)
# --------------------------------------------------------------------------- #
def _leer(directorio: Path, nombre: str) -> pd.DataFrame:
    # dtype=str + keep_default_na=False: todo como texto y celdas vacías "" (no NaN);
    # las conversiones a int/fecha las hacemos nosotros de forma explícita.
    df = pd.read_csv(directorio / nombre, dtype=str, keep_default_na=False)
    df.columns = df.columns.str.strip()
    return df


def _cargar_turnos(directorio: Path) -> dict[str, Turno]:
    df = _leer(directorio, "turnos.csv")
    turnos = {}
    for _, fila in df.iterrows():
        entrada = fila["hora_entrada"]
        salida = fila["hora_salida"]
        partido = _entero(fila.get("partido", ""), 0)
        dur = duracion(entrada, salida)
        tipo = tipo_turno(entrada, dur, partido)
        # Horas computables: partido y guardia de 24 h cuentan 8 h; el resto, su duración real.
        horas = HORAS_PARTIDO if partido else HORAS_GUARDIA if tipo == "24h" else dur
        turnos[fila["id_turno"]] = Turno(
            id=fila["id_turno"],
            municipio=fila["municipio"],
            lv=int(fila["lv"]),
            sab=int(fila["sabado"]),
            dom=int(fila["domingo"]),
            fes=int(fila["festivo"]),
            hora_entrada=entrada,
            hora_salida=salida,
            dem=_entero(fila.get("dem", ""), 1),
            partido=partido,
            dur=dur,
            horas=horas,
            noct=nocturnidad(entrada, dur),
            tipo=tipo,
        )
    return turnos


def _cargar_trabajadores(directorio: Path) -> dict[str, Trabajador]:
    df = _leer(directorio, "trabajadores.csv")
    trabajadores = {}
    for _, fila in df.iterrows():
        vacaciones = []
        for columna in ("vac1_inicio", "vac2_inicio"):
            if fila[columna].strip():
                vacaciones.append(_quincena(fila[columna]))
        patron = fila["patron"].strip()
        trabajadores[fila["id_trab"]] = Trabajador(
            id=fila["id_trab"],
            tipo=fila["tipo"],
            patron=patron if patron else None,
            vacaciones=vacaciones,
        )
    return trabajadores


def _cargar_calendarios(directorio: Path) -> dict[str, str]:
    df = _leer(directorio, "calendarios_municipio.csv")
    calendario = {}
    for _, fila in df.iterrows():
        calendario[fila["municipio"]] = fila["calendario_festivos"]
    return calendario


def _cargar_festivos(directorio: Path) -> dict[str, set[date]]:
    df = _leer(directorio, "festivos.csv")
    festivos = {}
    for _, fila in df.iterrows():
        ambito = fila["ambito"].strip()
        festivos.setdefault(ambito, set()).add(_fecha(fila["fecha"]))
    return festivos


def _cargar_capacidades(directorio: Path) -> dict[tuple[str, str], Capacidad]:
    df = _leer(directorio, "capacidades.csv")
    capacidades = {}
    for _, fila in df.iterrows():
        clave = (fila["id_trab"], fila["id_turno"])
        capacidades[clave] = Capacidad(
            lv=int(fila["lv"]),
            sab=int(fila["sab"]),
            dom=int(fila["dom"]),
            fest=int(fila["fest"]),
            v=int(fila["v"]),
        )
    return capacidades


def _cargar_patrones(directorio: Path) -> dict[str, list[dict[str, str]]]:
    df = _leer(directorio, "patrones.csv")
    # Agrupamos las filas por patrón, guardando su índice para ordenarlas después.
    sin_ordenar: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for _, fila in df.iterrows():
        semana = {dia: fila[dia] for dia in DIAS}
        sin_ordenar.setdefault(fila["patron"], []).append((int(fila["fila"]), semana))

    patrones = {}
    for patron, filas in sin_ordenar.items():
        filas.sort()                                  # por índice de fila
        patrones[patron] = [semana for _, semana in filas]
    return patrones


def cargar(directorio: Path | str = DATOS_DEF) -> Datos:
    d = Path(directorio)
    return Datos(
        turnos=_cargar_turnos(d),
        trabajadores=_cargar_trabajadores(d),
        calendario_municipio=_cargar_calendarios(d),
        festivos=_cargar_festivos(d),
        capacidades=_cargar_capacidades(d),
        patrones=_cargar_patrones(d),
    )


# --------------------------------------------------------------------------- #
#  Comprobación rápida
# --------------------------------------------------------------------------- #
def _resumen(datos: Datos) -> None:
    from collections import Counter

    print(f"Turnos: {len(datos.turnos)}  por tipo: "
          f"{dict(Counter(t.tipo for t in datos.turnos.values()))}")
    print(f"Municipios: {sorted({t.municipio for t in datos.turnos.values()})}")
    print(f"Trabajadores: {len(datos.trabajadores)}  por tipo: "
          f"{dict(Counter(w.tipo for w in datos.trabajadores.values()))}")
    print(f"Calendarios festivos: {datos.calendario_municipio}")
    print(f"Ámbitos de festivos: { {k: len(v) for k, v in datos.festivos.items()} }")
    print(f"Filas de capacidad: {len(datos.capacidades)}  "
          f"(refuerzo v: {sum(1 for c in datos.capacidades.values() if c.v)})")
    print(f"Patrones: { {p: len(f) for p, f in datos.patrones.items()} }")

    f = date(2026, 5, 13)            # festivo regional de Valladolid
    print(f"\n[Festivo regional {f:%d/%m/%Y}] Valladolid={datos.es_festivo(f, 'Valladolid')}  "
          f"Medina={datos.es_festivo(f, 'Medina')}")

    tid = next(t.id for t in datos.turnos.values() if t.tipo == "mañana" and t.lv)
    mie, reyes = date(2026, 1, 7), date(2026, 1, 6)
    print(f"[Turno {tid}] opera miércoles {mie:%d/%m}={datos.opera(tid, mie)}  "
          f"opera Reyes {reyes:%d/%m}={datos.opera(tid, reyes)}")

    w0 = next(iter(datos.trabajadores))
    v = datos.trabajadores[w0].vacaciones
    print(f"[{w0}] vacaciones={[(a.strftime('%d/%m'), b.strftime('%d/%m')) for a, b in v]}  "
          f"disponible {v[0][0]:%d/%m}={datos.disponible(w0, v[0][0])}")


if __name__ == "__main__":
    _resumen(cargar())

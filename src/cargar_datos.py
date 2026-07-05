"""
cargar_datos.py — Capa de carga de datos del generador de cuadrantes.

Lee los 6 CSV de data/input/ con pandas, deriva lo que el modelo necesita (duración, tipo, operatividad) y expone consultas:
  * opera(turno, fecha)          — ¿la línea opera ese día? (festivo manda sobre día de semana)
  * disponible(trab, fecha)      — ¿no está de vacaciones?
  * tipo_dia(fecha, municipio)   — LV / SAB / DOM / FEST
  * elegible(trab, turno, fecha) — (elegible?, es_refuerzo?)  a partir de capacidades

Base sobre la que se apoyan modelo.py / validar_datos.py. Ejecutado como script imprime
un resumen y comprobaciones de la instancia cargada.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta , time
from pathlib import Path
import csv

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ / "data" / "input"

DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]   # patrones.csv; índice = weekday()
LIBRE = "LIBRE"
# --------------------------------------------------------------------------- #
#  Derivaciones horarias
# --------------------------------------------------------------------------- #

def duracion_turno(entrada: time, salida: time) -> float:
    fecha_base = date(2000, 1, 1)

    dt_entrada = datetime.combine(fecha_base, entrada)
    dt_salida = datetime.combine(fecha_base, salida)

    if dt_salida <= dt_entrada:
        dt_salida += timedelta(days=1)

    return (dt_salida - dt_entrada).total_seconds() / 3600


def tipo_turno(entrada: time, salida: time) -> str:
    dur = duracion_turno(entrada,salida)
    if dur >= 20:
        return "24h"
    elif dur >=12:
        return "12h"
    elif entrada>salida:
        return "noche"
    elif salida>time(17):
        return "tarde"
    else:
        return "mañana"

# --------------------------------------------------------------------------- #
#  Estructuras del dominio
# --------------------------------------------------------------------------- #
@dataclass
class Turno:
    id: str             #Id del turno (lo suponemos único)
    municipio: str      #Municipio en el que opera el turno
    lv: int             #Flag que indica si se trabaja de lunes a viernes 0/1
    sab: int            #Flag que indica si se trabaja de sabados 0/1
    dom: int            #Flag que indica si se trabaja de domingos 0/1
    fes: int            #Flag que indica si se trabaja de festivos 0/1
    hora_entrada: time  #Hora de entrada del turno
    hora_salida: time   #Hora de salida del turno
    dem: int            #Demanda del turno (para valladolid es 1 siempre)
    partido: int        #Flag que indica si es turno partido 0/1
    horas: float        # horas efectivas computables para la jornada (partido y 24h -> 8)
    tipo: str           #Tipo de turno (24h, partido, tarde, mañana, noche)


@dataclass
class Trabajador:
    id: str
    tipo: str                       # fijo | patron | correturno | mixto
    patron: str | None              # id del patrón (solo tipo=patron)
    vacaciones: list[tuple[date, date]]


@dataclass
class Capacidad:
    lv: int                         #Trabaja de lunes a viernes flag 0/1
    sab: int                        #Trabaja los sabados flag 0/1
    dom: int                        #Trabaja los domingos flag 0/1
    fest: int                       #Trabaja los festivos flag 0/1
    v: int                          #Trabaja como cubre vacaciones 0/1


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
        return f in self.festivos.get("Nacional", set()) or f in self.festivos.get(calendario, set())

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
    turnos = {}
    with open(directorio / "turnos.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            hora_entrada = datetime.strptime(fila["hora_entrada"].strip(),"%H:%M").time()
            hora_salida = datetime.strptime(fila["hora_salida"].strip(),"%H:%M").time()
            partido = fila.get("partido",0)
            turnos[fila["id_turno"]] = Turno(
                id=fila["id_turno"],
                municipio=fila["municipio"],
                lv=int(fila["lv"]),
                sab=int(fila["sabado"]),
                dom=int(fila["domingo"]),
                fes=int(fila["festivo"]),
                hora_entrada=hora_entrada,
                hora_salida=hora_salida,
                dem=fila.get("dem",1),
                partido=partido,
                horas=float(fila["horas_computadas"].strip()),
                tipo=tipo_turno(hora_entrada, hora_salida) if partido==0 else "partido",
            )
    return turnos


def _cargar_trabajadores(directorio: Path) -> dict[str, Trabajador]:
    trabajadores = {}
    with open(directorio / "trabajadores.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            vac1 = datetime.strptime(fila["vac1_inicio"].strip(), "%d/%m/%Y").date()
            vac2 = datetime.strptime(fila["vac2_inicio"].strip(), "%d/%m/%Y").date()

            trabajadores[fila["id_trab"]] = Trabajador(
                id=fila["id_trab"],
                tipo=fila["tipo"],
                patron=fila.get("patron",None),
                vacaciones = [(vac1, vac1 + timedelta(days=14)),(vac2, vac2 + timedelta(days=14))]
            )
    return trabajadores


def _cargar_calendarios(directorio: Path) -> dict[str, str]:
    calendario = {}
    with open(directorio / "calendarios_municipio.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            calendario[fila["municipio"]] = fila["calendario_festivos"]
    return calendario


def _cargar_festivos(directorio: Path) -> dict[str, set[date]]:
    festivos = {}
    with open(directorio / "festivos.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            ambito = fila["ambito"].strip()
            festivos.setdefault(ambito, set()).add(datetime.strptime(fila["fecha"].strip(), "%d/%m/%Y").date())
    return festivos


def _cargar_capacidades(directorio: Path) -> dict[tuple[str, str], Capacidad]:
    capacidades = {}
    with open(directorio / "capacidades.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            capacidades[(fila["id_trab"], fila["id_turno"])] = Capacidad(
            lv=int(fila["lv"]),
            sab=int(fila["sab"]),
            dom=int(fila["dom"]),
            fest=int(fila["fest"]),
            v=int(fila["v"]),
        )
    return capacidades


def _cargar_patrones(directorio: Path) -> dict[str, list[dict[str, str]]]:
    sin_ordenar = {}
    with open(directorio / "patrones.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            semana = {dia: fila[dia] for dia in DIAS}
            sin_ordenar.setdefault(fila["patron"], []).append((int(fila["fila"]), semana))

    patrones = {}
    for patron, filas in sin_ordenar.items():
        filas.sort()                                  # por índice de fila
        patrones[patron] = [semana for _, semana in filas]
    return patrones


def cargar(directorio: Path | str = DATA) -> Datos:
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


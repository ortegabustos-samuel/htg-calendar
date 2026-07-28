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
# Consumo de capacidad de un turno LOCALIZADO 24h para la EQUIDAD (no lo legal): una quincena de
# localizado son 7 turnos (2 días una semana + 5 la otra) y equivale a una quincena normal de
# 80 h (8h·5días·2sem) → cada turno localizado "consume" 80/7 h. (Calibrar con el patrón real.)
CONSUMO_LOCALIZADO = 80 / 7
# --------------------------------------------------------------------------- #
#  Derivaciones horarias
# --------------------------------------------------------------------------- #

def duracion_turno(entrada: time, salida: time) -> float:
    """
    Metodo devuelve duracion turno con entrada y salida

    Args:
        entrada (time): Objeto time que representa la hora de entrada del turno
        salida (time): Objeto time que representa la hora de salida del turno

    Returns:
        float: Tiempo en horas entre entrada y salida turno 
    """
    fecha_base = date(2000, 1, 1)

    dt_entrada = datetime.combine(fecha_base, entrada)
    dt_salida = datetime.combine(fecha_base, salida)

    if dt_salida <= dt_entrada:
        dt_salida += timedelta(days=1)

    return (dt_salida - dt_entrada).total_seconds() / 3600


def tipo_turno(entrada: time, salida: time) -> str:
    """
    Metodo que en base a hora entrada y salida devuelve el tipo de turno

    Args:
        entrada (time): Objeto time que representa la hora de entrada del turno
        salida (time): Objeto time que representa la hora de salida del turno
    
    Returns:
        str: tipo de turno [24h, 12h, noche, tarde, mañana]
    """
    dur = duracion_turno(entrada,salida)
    if dur >= 22:
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
    dem: int            #Demanda del turno
    partido: int        #Flag que indica si es turno partido 0/1
    horas: float        # horas COMPUTADAS (jornada legal; partido y 24h -> 8). Para el tope 1826.
    tipo: str           #Tipo de turno (24h, partido, tarde, mañana, noche)
    prioridad: int = 1  # rango de cobertura (entero >=0; mayor = más crítico). El coste real de dejarlo
                        # SIN cubrir lo calcula modelo.peso_cobertura() como (prioridad+1), NO prioridad
                        # a secas, para que 0 no sea nunca coste cero: 0 = turno COMODÍN (p.ej. REF CAL,
                        # herramienta para asignar horas de ayuda, no cobertura real) — el escalón más
                        # bajo, SIEMPRE dominado por cualquier turno de prioridad>=1, pero se sigue
                        # cubriendo si sobra margen (que es su función).
    horas_consumo: float = 0.0  # horas que CONSUME de la capacidad anual (para la EQUIDAD, no lo legal).
                                # Localizado 24h -> CONSUMO_LOCALIZADO (Suponiendo dos semanas equivalente a 80 horas);
                                # normal -> = horas computadas. Lo deriva el cargador.


@dataclass
class Trabajador:
    id: str                         #Nif único
    tipo: str                       # fijo | patron | correturno | mixto
    patron: str | None              # id del patrón (solo tipo=patron)
    vacaciones: list[tuple[date, date]] #Lista con tupla (inicio_vacaciones,fin_vacaciones)
    linea: str | None = None        # id del turno que cubre un FIJO (solo tipo=fijo). Se declara aquí
                                    # igual que el patrón: es lo que define su trabajo. Su capacidad
                                    # se deriva sola (ver _anadir_capacidad_fijo), no va en capacidades.csv.
    factor_jornada: float = 1.0     # reducción de jornada: escala objetivo (1776) y tope (1826). 1.0 = jornada completa
    grupo: str | None = None        # grupo de EQUIDAD: mismos grupo se equiparan entre sí (findes/festivos).
                                    # Lo asigna la empresa; None = sin grupo (no entra en equidad de grupo)


@dataclass
class Capacidad:
    lv: int                         #Trabaja de lunes a viernes flag 0/1
    sab: int                        #Trabaja los sabados flag 0/1
    dom: int                        #Trabaja los domingos flag 0/1
    fest: int                       #Trabaja los festivos flag 0/1
    v: int                          # Cobertura excepcional, con ORDEN de preferencia:
                                    #   0 = no es cubridor de esta línea (capacidad normal)
                                    #   1 = cubridor PRINCIPAL — el que el gestor prefiere para ella
                                    #   2, 3… = suplentes, por orden: solo entran si el principal no
                                    #           puede (vacaciones, ya ocupado, descanso obligado).
                                    # El orden es una preferencia BLANDA (ver _preferencia_cubridor):
                                    # nunca deja una línea sin cubrir por respetarlo.


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
        """Retorna true si esa fecha es festivo en ese municipio"""
        calendario = self.calendario_municipio.get(municipio, municipio)
        return f in self.festivos.get("Comun", set()) or f in self.festivos.get(calendario, set())

    def tipo_dia(self, f: date, municipio: str) -> str:
        """Devuelve el tipo de dia en LV | SAB | DOM | FEST """
        if self.es_festivo(f, municipio):
            return "FEST"
        wd = f.weekday()
        return "LV" if wd < 5 else "SAB" if wd == 5 else "DOM"

    def opera(self, turno_id: str, f: date) -> bool:
        """Devuelve si opera el turno en esa fecha teniendo en cuenta municipio y dias semana"""
        t = self.turnos[turno_id]
        if self.es_festivo(f, t.municipio):
            return t.fes == 1
        wd = f.weekday()
        return (t.lv if wd < 5 else t.sab if wd == 5 else t.dom) == 1

    def disponible(self, trab_id: str, f: date) -> bool:
        """Devuelve si el trabajador esta disponible en esa fecha en base a sus vacaciones"""
        return not any(ini <= f <= fin for ini, fin in self.trabajadores[trab_id].vacaciones)

    def elegible(self, trab_id: str, turno_id: str, f: date) -> tuple[bool, bool]:
        """Devuelve (elegible, es_refuerzo)
        Solo si la línea opera y el trabajador está disponible.
        Normal si su capacidad cubre el tipo de día, si no, de refuerzo si tiene v=1.
        """
        if not self.opera(turno_id, f) or not self.disponible(trab_id, f):
            return (False, False)
        cap = self.capacidades.get((trab_id, turno_id))
        if cap is None:
            return (False, False)
        td = self.tipo_dia(f, self.turnos[turno_id].municipio)
        normal = {"LV": cap.lv, "SAB": cap.sab, "DOM": cap.dom, "FEST": cap.fest}[td] == 1
        if normal:
            return (True, False)
        if cap.v >= 1:          # cubridor: principal (v=1) o suplente (v>=2); el orden lo pesa el modelo
            return (True, True)
        return (False, False)


# --------------------------------------------------------------------------- #
#  Carga: una función por fichero
# --------------------------------------------------------------------------- #

def _cargar_turnos(directorio: Path) -> dict[str, Turno]:
    turnos = {}
    with open(directorio / "turnos.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            hora_entrada = datetime.strptime(fila["hora_entrada"].strip(),"%H:%M").time()
            hora_salida = datetime.strptime(fila["hora_salida"].strip(),"%H:%M").time()
            partido = int(fila.get("partido",0))
            horas = float(fila["horas_computadas"].strip())
            tipo = tipo_turno(hora_entrada, hora_salida) if partido==0 else "partido"
            # Consumo para la EQUIDAD: un localizado 24h consume más capacidad que sus 8 h computadas
            consumo = CONSUMO_LOCALIZADO if tipo == "24h" else horas
            # prioridad: entero >=0 (columna opcional, vacío -> 1). 0 = turno COMODÍN (ver
            # modelo.peso_cobertura: nunca coste cero); mayor = más crítico de cubrir.
            crudo_prio = (fila.get("prioridad") or "").strip()
            prioridad = int(crudo_prio) if crudo_prio else 1
            if prioridad < 0:
                raise ValueError(f"prioridad de {fila['id_turno']} negativa ({prioridad}); debe ser >=0")
            turnos[fila["id_turno"]] = Turno(
                id=fila["id_turno"],
                municipio=fila["municipio"],
                lv=int(fila["lv"]),
                sab=int(fila["sabado"]),
                dom=int(fila["domingo"]),
                fes=int(fila["festivo"]),
                hora_entrada=hora_entrada,
                hora_salida=hora_salida,
                dem=int(fila.get("dem",1)),
                partido=partido,
                horas=horas,
                tipo=tipo,
                prioridad=prioridad,
                horas_consumo=consumo,
            )
    return turnos


def _cargar_trabajadores(directorio: Path) -> dict[str, Trabajador]:
    trabajadores = {}
    with open(directorio / "trabajadores.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            vac1 = datetime.strptime(fila["vac1_inicio"].strip(), "%d/%m/%Y").date()
            vac2 = datetime.strptime(fila["vac2_inicio"].strip(), "%d/%m/%Y").date()

            # factor_jornada: columna OPCIONAL (default 1.0). Reducción de jornada -> (0,1].
            crudo = (fila.get("factor_jornada") or "").strip().replace(",", ".")
            factor = float(crudo) if crudo else 1.0
            if not (0 < factor <= 1):
                raise ValueError(
                    f"factor_jornada de {fila['id_trab']} fuera de rango (0,1]: {factor}"
                )

            trabajadores[fila["id_trab"]] = Trabajador(
                id=fila["id_trab"],
                tipo=fila["tipo"],
                patron=fila.get("patron",None),
                linea=(fila.get("linea") or "").strip() or None,   # columna OPCIONAL, solo para fijos
                vacaciones = [(vac1, vac1 + timedelta(days=14)),(vac2, vac2 + timedelta(days=14))],
                factor_jornada=factor,
                grupo=(fila.get("grupo") or "").strip() or None,   # columna OPCIONAL de grupo de equidad
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


def _anadir_capacidades_patron(
    trabajadores: dict[str, Trabajador],
    patrones: dict[str, list[dict[str, str]]],
    turnos: dict[str, Turno],
    capacidades: dict[tuple[str, str], Capacidad],
) -> int:
    """
    Deriva las capacidades de los trabajadores de patron a partir de la estructura del patrón y
    las introduce en el conjunto de capacidades.
    """
    # Turnos (excluye LIBRE y cualquier celda vacía) que rota cada patrón.
    turnos_por_patron: dict[str, set[str]] = {}
    for patron, filas in patrones.items():
        rotados = {turno for fila in filas for turno in fila.values()
                   if turno and turno != LIBRE and turno in turnos}
        turnos_por_patron[patron] = rotados

    anadidas = 0
    for w, t in trabajadores.items():
        if t.tipo != "patron" or not t.patron:
            continue
        for turno in turnos_por_patron.get(t.patron, ()):
            if (w, turno) not in capacidades:               # respeta lo que ya venga del CSV
                capacidades[(w, turno)] = Capacidad(lv=1, sab=1, dom=1, fest=1, v=0)
                anadidas += 1
    return anadidas


def _anadir_capacidad_fijo(
    trabajadores: dict[str, Trabajador],
    turnos: dict[str, Turno],
    capacidades: dict[tuple[str, str], Capacidad],
) -> int:
    """Deriva la capacidad de los FIJOS a partir de su `linea` (declarada en trabajadores.csv), igual
    que las de patrón se derivan de patrones.csv.

    Un fijo trabaja su plaza de LUNES A VIERNES: eso es lo que significa ser fijo, así que la regla
    vive AQUÍ y no en los datos. Importa que sea así y no deducirla de los días en que opera la línea:
    VADN022 opera sábados, domingos y festivos, pero su fijo solo la cubre L-V (el finde lo hace otro).
    Si un fijo tuviera además días atípicos, basta una fila explícita en capacidades.csv: esta
    derivación respeta lo que ya venga del CSV. Devuelve el nº de capacidades añadidas."""
    anadidas = 0
    for w, t in trabajadores.items():
        if t.tipo != "fijo":
            continue
        if not t.linea:
            # Falla en voz alta: con un trabajadores.csv anterior a la columna `linea`, el fijo se
            # quedaría sin plaza congelada y el cuadrante saldría en silencio con su línea vacía.
            raise ValueError(f"El fijo {w} no declara `linea` en trabajadores.csv "
                             f"(columna obligatoria para tipo=fijo)")
        if t.linea not in turnos:
            raise ValueError(f"El fijo {w} declara la línea '{t.linea}', que no existe en turnos.csv")
        if (w, t.linea) not in capacidades:                 # respeta lo que ya venga del CSV
            capacidades[(w, t.linea)] = Capacidad(lv=1, sab=0, dom=0, fest=0, v=0)
            anadidas += 1
    return anadidas


def _derivar_grupos_equidad(trabajadores: dict[str, Trabajador]) -> int:
    """Grupo de equidad de findes/festivos: cada PATRÓN es un grupo CERRADO propio — sus miembros se
    equiparan solo entre sí (el patrón ya codifica una rotación equilibrada; la equidad se mide DENTRO
    del grupo, no contra el resto de la plantilla, que hace otro tipo de trabajo). Mixtos y correturnos
    quedan en el pool general (grupo=None). No pisa un grupo fijado a mano en trabajadores.csv. Devuelve
    nº de grupos asignados. (El patrón grande de Valladolid, que en la práctica admite reajustes para
    una equidad global, es una peculiaridad local; se revisa aparte según cómo queden los findes.)"""
    asignados = 0
    for w, t in trabajadores.items():
        if t.tipo == "patron" and t.patron and t.grupo is None:
            t.grupo = t.patron
            asignados += 1
    return asignados


def cargar(directorio: Path | str = DATA) -> Datos:
    d = Path(directorio)
    turnos = _cargar_turnos(d)
    trabajadores = _cargar_trabajadores(d)
    capacidades = _cargar_capacidades(d)
    patrones = _cargar_patrones(d)
    # Capacidades de los trabajadores de patrón: derivadas de la estructura del patrón (no están en
    # capacidades.csv porque esa info ya vive en patrones.csv).
    _anadir_capacidades_patron(trabajadores, patrones, turnos, capacidades)
    # Capacidad de los fijos: derivada de su `linea` (trabajadores.csv), L-V por definición.
    _anadir_capacidad_fijo(trabajadores, turnos, capacidades)
    # Grupo de equidad: cada patrón, su propio grupo cerrado; mixtos/correturnos en el pool general.
    _derivar_grupos_equidad(trabajadores)
    return Datos(
        turnos=turnos,
        trabajadores=trabajadores,
        calendario_municipio=_cargar_calendarios(d),
        festivos=_cargar_festivos(d),
        capacidades=capacidades,
        patrones=patrones,
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


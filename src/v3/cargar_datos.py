"""
cargar_datos.py — Capa de carga de datos del generador de cuadrantes.

Lee config.toml y los 6 CSV de data/input/, deriva lo que el modelo necesita (duración,
tipo, operatividad) y expone consultas:
  * opera(turno, fecha)          — ¿la línea opera ese día? (festivo manda sobre día de semana)
  * disponible(trab, fecha)      — ¿no está de vacaciones?
  * tipo_dia(fecha, municipio)   — LV / SAB / DOM / FEST
  * elegible(trab, turno, fecha) — (elegible?, es_refuerzo?)  a partir de capacidades

Base sobre la que se apoyan modelo.py / validar_datos.py. Ejecutado como script imprime
un resumen y comprobaciones de la instancia cargada.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta , time
from pathlib import Path
import csv
import tomllib

RAIZ = Path(__file__).resolve().parents[2]
DATA = RAIZ / "data" / "input"

DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]   # patrones.csv; índice = weekday()
LIBRE = "LIBRE"

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
    horas: float        # horas COMPUTADAS (jornada legal; partido y 24h -> 8)


@dataclass
class Trabajador:
    id: str                         #Nif único
    tipo: str                       # fijo | patron | correturno | mixto
    patron: str | None              # id del patrón (solo tipo=patron)
    vacaciones: list[tuple[date, date]] #Lista con tupla (inicio_vacaciones,fin_vacaciones)
    linea: str | None = None        # id del turno que cubre un FIJO (solo tipo=fijo). Se declara aquí
                                    # igual que el patrón: es lo que define su trabajo. Su capacidad
                                    # se deriva sola (ver _anadir_capacidad_fijo), no va en capacidades.csv.
    factor_jornada: float = 1.0     # reducción de jornada: escala el objetivo anual. 1.0 = jornada completa
    fila_inicial: int | None = None  # solo tipo=patron: fila de `patrones.csv` que hace en la PRIMERA
                                    # semana del horizonte. Es lo que da continuidad entre años —ver
                                    # `offsets_patron`—. None = no declarada (se deduce del orden).


@dataclass(frozen=True)
class Config:
    """
    Parámetros de la INSTANCIA (`config.toml`): qué año se resuelve y bajo qué convenio.
    `anio` es obligatorio en config.toml: el horizonte lo declaran los datos, no se deduce.
    """
    anio: int                    # Anio sobre el que estamos haciendo el calendario
    horas_objetivo: int = 1776   # jornada anual objetivo (h): techo de todo lo que no sea cubrir
    descanso_minimo: int = 12               # descanso mínimo entre jornadas (h)              — C4
    horas_max_semana: int = 48              # máx. horas en cualquier ventana de 7 días       — C6
    dias_max_semana: int = 6                # máx. días trabajados por semana ISO             — C5
    ratio_rigido: float = 0.6               # a partir de qué descanso/trabajo una plaza no se
                                            # fracciona al ceder horas (ver v3/ritmo.py)

@dataclass
class Capacidad:
    lv: int                         #Trabaja de lunes a viernes flag 0/1
    sab: int                        #Trabaja los sabados flag 0/1
    dom: int                        #Trabaja los domingos flag 0/1
    fest: int                       #Trabaja los festivos flag 0/1
    v: int                          # Cobertura excepcional, con ORDEN de preferencia:
                                    #   0 = no es cubridor de esta línea (capacidad normal)
                                    #   1 = cubridor PRINCIPAL 
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
    offsets: dict[str, int] = field(default_factory=dict)  # trabajador de patrón -> fila de arranque
                                                   # (ver `offsets_patron`). Lo deriva `cargar()`.
    config: Config = field(default_factory=Config)  # año y parámetros del convenio (config.toml)

    # -- Horizonte -------------------------------------------------------- #
    # El cuadrante es SIEMPRE el año natural de config.toml, así que no se pasa por parámetro.
    @property
    def inicio(self) -> date:
        return date(self.config.anio, 1, 1)

    @property
    def fin(self) -> date:
        return date(self.config.anio, 12, 31)

    @property
    def fechas(self) -> list[date]:
        """Días del año, del 1 de enero al 31 de diciembre."""
        return [self.inicio + timedelta(days=i) for i in range((self.fin - self.inicio).days + 1)]

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

    def intervalo(self, turno_id: str, f: date) -> tuple[datetime, datetime]:
        """Momento real de entrada y de salida del turno ese día. Si la salida no es posterior a
        la entrada, el turno cruza medianoche y termina al día siguiente — de ahí sale que un
        nocturno bloquee el turno de mañana del día siguiente (descanso mínimo entre jornadas)."""
        t = self.turnos[turno_id]
        inicio = datetime.combine(f, t.hora_entrada)
        fin = datetime.combine(f, t.hora_salida)
        if fin <= inicio:
            fin += timedelta(days=1)
        return inicio, fin

    def franja(self, turno_id: str) -> str:
        """Tramo del día en que se trabaja: mañana | tarde | noche.

        Sale del reloj y los cortes no son arbitrarios: en estos datos ninguna línea entra entre
        las 11:30 y las 13:30 ni entre las 16:00 y las 21:30, así que 13:00 y 21:00 caen en huecos
        reales de la distribución. Es lo que se le fija a un correturno para toda la semana — la
        franja es lo que de verdad organiza la vida de quien la hace.
        """
        t = self.turnos[turno_id]
        if t.hora_salida <= t.hora_entrada:              # cruza medianoche
            return "noche"
        if t.hora_entrada.hour < 13:
            return "mañana"
        return "tarde" if t.hora_entrada.hour < 21 else "noche"

    def duracion(self, turno_id: str) -> float:
        """Horas REALES que dura el turno de reloj a reloj. No es lo mismo que `Turno.horas`, que
        son las computadas por convenio (el partido y el de 24 h computan 8)."""
        inicio, fin = self.intervalo(turno_id, date(2000, 1, 1))
        return (fin - inicio).total_seconds() / 3600

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

def _cargar_turnos() -> dict[str, Turno]:
    turnos = {}
    with open(DATA / "turnos.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            hora_entrada = datetime.strptime(fila["hora_entrada"].strip(),"%H:%M").time()
            hora_salida = datetime.strptime(fila["hora_salida"].strip(),"%H:%M").time()
            horas = float(fila["horas_computadas"].strip())
            # dem: columna OPCIONAL (sparse) — solo se rellena cuando la demanda es >1; vacía -> 1.
            # OJO: `fila.get("dem", 1)` NO vale para esto: en una fila más corta que la cabecera,
            # DictReader mete la clave igualmente con valor None (no la deja ausente), así que el
            # default de `.get()` nunca se dispara y `int(None)` revienta.
            crudo_dem = (fila.get("dem") or "").strip()
            turnos[fila["id_turno"]] = Turno(
                id=fila["id_turno"],
                municipio=fila["municipio"],
                lv=int(fila["lv"]),
                sab=int(fila["sabado"]),
                dom=int(fila["domingo"]),
                fes=int(fila["festivo"]),
                hora_entrada=hora_entrada,
                hora_salida=hora_salida,
                dem=int(crudo_dem) if crudo_dem else 1,
                horas=horas,
            )
    return turnos


def _cargar_trabajadores() -> dict[str, Trabajador]:
    trabajadores = {}
    with open(DATA / "trabajadores.csv", mode="r", encoding="utf-8",newline="") as archivo:
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

            # fila_inicial: columna OPCIONAL (solo tipo=patron). Vacía -> None: el offset se deduce
            # del orden dentro del grupo, como se hacía antes de existir la columna.
            crudo_fila = (fila.get("fila_inicial") or "").strip()
            if crudo_fila:
                try:
                    fila_inicial = int(crudo_fila)
                except ValueError:
                    raise ValueError(
                        f"fila_inicial de {fila['id_trab']} no es un entero: '{crudo_fila}'"
                    ) from None
                if fila_inicial < 0:
                    raise ValueError(
                        f"fila_inicial de {fila['id_trab']} es negativa ({fila_inicial})"
                    )
            else:
                fila_inicial = None

            trabajadores[fila["id_trab"]] = Trabajador(
                id=fila["id_trab"],
                tipo=fila["tipo"],
                patron=fila.get("patron",None),
                linea=(fila.get("linea") or "").strip() or None,   # columna OPCIONAL, solo para fijos
                vacaciones = [(vac1, vac1 + timedelta(days=14)),(vac2, vac2 + timedelta(days=14))],
                factor_jornada=factor,
                fila_inicial=fila_inicial,
            )
    return trabajadores


def _cargar_calendarios() -> dict[str, str]:
    """
    Cargar calendarios permite un mapping de municipio al calendario de festivos que sigue
    Iscar -> Valladolid
    Medina -> Medina
    Peñafiel -> Valladolid
    """
    calendario = {}
    with open(DATA / "calendarios_municipio.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            calendario[fila["municipio"]] = fila["calendario_festivos"]
    return calendario


def _cargar_festivos() -> dict[str, set[date]]:
    """
    Festivos es de la forma diccionario con key y valor un set:
    Comun = (01/01/2026, 24/12/2026..)
    Valladolid = (13/05/2026,08/09/2026)
    """
    festivos = {}
    with open(DATA / "festivos.csv", mode="r", encoding="utf-8",newline="") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            ambito = fila["ambito"].strip()
            festivos.setdefault(ambito, set()).add(datetime.strptime(fila["fecha"].strip(), "%d/%m/%Y").date())
    return festivos


def _cargar_capacidades() -> dict[tuple[str, str], Capacidad]:
    capacidades = {}
    with open(DATA / "capacidades.csv", mode="r", encoding="utf-8",newline="") as archivo:
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


def _cargar_config() -> Config:
    """Lee `config.toml`. Los parámetros del convenio son opcionales (caen a los defaults); `anio`
    no. Que los valores tengan sentido lo dice `validar_datos.py`, como con los CSV."""
    ruta = DATA / "config.toml"
    valores: dict[str, object] = {}
    if ruta.is_file():
        with open(ruta, "rb") as archivo:              # tomllib exige binario
            datos = tomllib.load(archivo)
        campos = {f.name for f in fields(Config)}
        for seccion, variable in datos.items():
            if not isinstance(variable, dict):
                raise ValueError(f"config.toml: '{seccion}' está suelto; todo va dentro de una "
                                 f"sección ([horizonte], [jornada], [convenio])")
            for clave, valor in variable.items():
                if clave not in campos:
                    raise ValueError(f"config.toml: '{clave}' (en [{seccion}]) no es un parámetro; "
                                     f"los que hay son {sorted(campos)}")
                if valor is None:
                    raise ValueError(f"config.toml: {clave}={valor!r} debería ser un valor")
                valores[clave] = valor
    return Config(**valores)                            # type: ignore[arg-type]


def _cargar_patrones() -> dict[str, list[dict[str, str]]]:
    sin_ordenar = {}
    with open(DATA / "patrones.csv", mode="r", encoding="utf-8",newline="") as archivo:
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
    que las de patrón se derivan de patrones.csv."""
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


def _anadir_capacidades_correturno(
    trabajadores: dict[str, Trabajador],
    turnos: dict[str, Turno],
    capacidades: dict[tuple[str, str], Capacidad],
) -> int:
    """Un CORRETURNO puede hacer cualquier línea: esa es su función. En vez de enumerarle 67 filas
    una a una, se derivan todas, y en `capacidades.csv` solo se declaran sus EXCEPCIONES.
    
    Las filas explícitas MANDAN sobre lo derivado, por si hiciera falta declarar una excepción.
    Devuelve el nº de capacidades añadidas."""
    # Orden más alto ya declarado en cada línea (0 = nadie designado para ella)
    orden_max: dict[str, int] = {}
    for (_, turno), cap in capacidades.items():
        if cap.v >= 1:
            orden_max[turno] = max(orden_max.get(turno, 0), cap.v)

    anadidas = 0
    for w, t in trabajadores.items():
        if t.tipo != "correturno":
            continue
        for turno in turnos:
            if (w, turno) in capacidades:            # excepción declarada: manda ella
                continue
            if orden_max.get(turno):                 # línea con designados: no es para él
                continue
            capacidades[(w, turno)] = Capacidad(lv=1, sab=1, dom=1, fest=1, v=0)
            anadidas += 1
    return anadidas


def offsets_patron(
    trabajadores: dict[str, Trabajador],
    patrones: dict[str, list[dict[str, str]]],
) -> dict[str, int]:
    """Fila de la rotación en que arranca cada trabajador de patrón: {id_trab -> offset}.
    Cada anio tenemos que los trabajadores de patron deben adaptarse al fin de la semana que hizo en 
    el cuadrante anterior, esto puede venir implicito en los datos o bien que se asigne automaticamente por 
    orden alfabetico, aunque por defecto suele ser indicarlo
    """
    grupos: dict[str, list[str]] = {}
    for w, t in trabajadores.items():
        if t.tipo == "patron" and t.patron:
            grupos.setdefault(t.patron, []).append(w)

    offsets: dict[str, int] = {}
    for patron, trabs in grupos.items():
        T = len(patrones.get(patron) or ())
        if not T:
            continue                                   # patrón sin filas: no hay rotación que anclar
        for orden, w in enumerate(sorted(trabs)):
            declarada = trabajadores[w].fila_inicial
            offsets[w] = (declarada if declarada is not None else orden) % T
    return offsets


def cargar() -> Datos:
    turnos = _cargar_turnos()
    trabajadores = _cargar_trabajadores()
    capacidades = _cargar_capacidades()
    patrones = _cargar_patrones()
    # Capacidades de los trabajadores de patrón: derivadas de la estructura del patrón (no están en
    # capacidades.csv porque esa info ya vive en patrones.csv).
    _anadir_capacidades_patron(trabajadores, patrones, turnos, capacidades)
    # Capacidad de los fijos: derivada de su `linea` (trabajadores.csv), L-V por definición.
    _anadir_capacidad_fijo(trabajadores, turnos, capacidades)
    # Correturnos: pueden con cualquier línea, así que se derivan todas; en las que tienen cubridor
    # designado entran como último recurso. Va DESPUÉS para ver los órdenes ya declarados.
    _anadir_capacidades_correturno(trabajadores, turnos, capacidades)
    # Grupo de equidad: cada patrón, su propio grupo cerrado; mixtos/correturnos en el pool general.
    return Datos(
        turnos=turnos,
        trabajadores=trabajadores,
        calendario_municipio=_cargar_calendarios(),
        festivos=_cargar_festivos(),
        capacidades=capacidades,
        patrones=patrones,
        # Fila de arranque de cada trabajador de patrón: `fila_inicial` si la declara, orden
        # alfabético si no. Única fuente para todo lo que prescribe la rotación.
        offsets=offsets_patron(trabajadores, patrones),
        # Año y parámetros del convenio (config.toml). Única fuente: el modelo, el pulido, la
        # salida, el diagnóstico y el validador leen todos de aquí.
        config=_cargar_config(),
    )

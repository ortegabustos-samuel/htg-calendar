"""
cargar_datos.py — Capa de carga de datos del generador de cuadrantes.

Lee config.toml y los 6 CSV de data/input/, deriva lo que el generador necesita (duración,
intervalo real, franja, localizado, operatividad) y expone consultas:
  * opera(turno, fecha)          — ¿la línea opera ese día? (festivo manda sobre día de semana)
  * disponible(trab, fecha)      — ¿no está de vacaciones?
  * tipo_dia(fecha, municipio)   — LV / SAB / DOM / FEST
  * elegible(trab, turno, fecha) — (elegible?, es_refuerzo?)  a partir de capacidades

Base sobre la que se apoya todo el generador. Ejecutado como script imprime un resumen y
comprobaciones de la instancia cargada.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta , time
from pathlib import Path
import csv
import os
import tomllib

RAIZ = Path(__file__).resolve().parents[1]
# La interfaz (interfaz/app.py) apunta aquí la carpeta de cada escenario; sin ella, data/input.
DATA = Path(os.environ.get("HT_DATOS") or RAIZ / "data" / "input")

DIAS = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]  # patrones.csv; índice = weekday()
LIBRE = "LIBRE"
# DO (descanso obligatorio): el descanso que da por ley un turno de 24 h, hoy solo en los patrones
# de UVI. Para la PLANIFICACIÓN es exactamente un LIBRE —no se trabaja, no computa horas, no ocupa
# plaza—; la única diferencia es que en el cuadrante sale con su etiqueta en vez de en blanco.
DO = "DO"
DESCANSOS = frozenset({LIBRE, DO})      # celdas de patrón que significan "ese día no se trabaja"


def turno_de(plan, trabajador_id, fecha):
    """El TURNO DE TRABAJO que hace ese día, o None si lo que tiene es un descanso.

    El plan lleva también los descansos etiquetados (DO). Eso es deliberado y tiene dos efectos
    que NO hay que confundir:

      * OCUPA el día, igual que un turno. Por eso se mete en el plan: así el solver, las cesiones
        y los traspasos lo respetan sin tener que saber qué es, y un DO heredado llega intacto al
        final. Para esa pregunta se sigue usando `(trab, fecha) in plan`, que ya lo blinda.
      * NO es trabajo: no computa horas, no ocupa plaza de ninguna línea y no cuenta para los
        topes del convenio. Para esa pregunta se usa ESTA función, que devuelve el mismo `None`
        que se recibía cuando el día venía vacío.
    """
    valor = plan.get((trabajador_id, fecha))
    return None if valor in DESCANSOS else valor

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
    horas: float        # horas de computo


@dataclass
class Trabajador:
    id: str                         #Nif único
    nombre: str                     # nombre y apellidos, solo para la salida a Excel
    tipo: str                       # fijo | patron | correturno
    patron: str | None              # id del patrón (solo tipo=patron)
    vacaciones: list[tuple[date, date]] #Lista con tupla (inicio_vacaciones,fin_vacaciones)
    linea: str | None = None        # id del turno que cubre un FIJO (solo tipo=fijo)
    municipio: str = ""             # zona a la que pertenece: define con quien compite en equidad
    factor_jornada: float = 1.0     # reducción de jornada: escala el objetivo anual. 1.0 = jornada completa
    fila_inicial: int | None = None  # solo tipo=patron: fila de `patrones.csv` que hace en la PRIMERA
                                    # semana del horizonte.


@dataclass(frozen=True)             #El uso de forzen impide que se modifique el propio objeto Config (logico la configuracion no deberia modificarse)
class Config:
    """
    Parámetros de la INSTANCIA (`config.toml`): qué año se resuelve y bajo qué convenio.
    `anio` es obligatorio en config.toml: el horizonte lo declaran los datos, no se deduce.
    """
    anio: int                    # Anio sobre el que estamos haciendo el calendario
    horas_objetivo: int   # jornada anual objetivo (h): techo de todo lo que no sea cubrir
    descanso_minimo: int                # descanso mínimo entre jornadas (h)              -> C4
    horas_max_semana: int             # máx. horas en cualquier ventana de 7 días       -> C6
    dias_max_semana: int                # máx. días trabajados por semana ISO             -> C5
    grupos_rigidos: tuple[str, ...] = ()    # grupos (patrón o tipo) cuyo descanso no se fracciona:
                                            # se cede el ciclo entero, nunca un día suelto (libranzas.py)

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
    turnos: dict[str, Turno]                       # Lista con nombre de turno y su objeto turno correspondiente
    trabajadores: dict[str, Trabajador]            # Lista con nif del trabajador y su objeto trabajador
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
    def lista_dias_calendario(self) -> list[date]:
        """Días del año, del 1 de enero al 31 de diciembre."""
        return [self.inicio + timedelta(days=i) for i in range((self.fin - self.inicio).days + 1)]

    @property
    def primer_lunes(self) -> date:
        """Lunes desde el que se cuentan las semanas de la rotación"""
        return self.inicio - timedelta(days=self.inicio.weekday())    

    # -- Consultas derivadas ------------------------------------------------- #
    def es_festivo(self, fecha: date, municipio: str) -> bool:
        """Retorna true si esa fecha es festivo en ese municipio"""
        calendario = self.calendario_municipio.get(municipio, municipio)
        return fecha in self.festivos.get("Nacional", set()) or fecha in self.festivos.get(calendario, set())

    def tipo_dia(self, fecha: date, municipio: str) -> str:
        """Devuelve el tipo de dia en LV | SAB | DOM | FEST """
        if self.es_festivo(fecha, municipio):
            return "FEST"
        dia_semana = fecha.weekday()
        return "LV" if dia_semana < 5 else "SAB" if dia_semana == 5 else "DOM"

    def opera(self, turno_id: str, fecha: date) -> bool:
        """Devuelve si opera el turno en esa fecha teniendo en cuenta municipio y dias semana"""
        turno = self.turnos[turno_id]
        if self.es_festivo(fecha, turno.municipio):
            return turno.fes == 1
        dia_semana = fecha.weekday()
        return (turno.lv if dia_semana < 5 else turno.sab if dia_semana == 5 else turno.dom) == 1

    def intervalo(self, turno_id: str, fecha: date) -> tuple[datetime, datetime]:
        """ Metodo que retorna el objeto datetime de inicio y datetime de fin, su finalidad es detectar aquellos turnos
        que trascurren pasada las 0:00 con el objetivo de medir correctamente descansos"""
        turno = self.turnos[turno_id]
        inicio = datetime.combine(fecha, turno.hora_entrada)
        fin = datetime.combine(fecha, turno.hora_salida)
        if fin <= inicio:
            fin += timedelta(days=1)
        return inicio, fin

    def franja(self, turno_id: str) -> str:
        """Tramo del día en que se trabaja: mañana | tarde | noche"""
        turno = self.turnos[turno_id]
        if turno.hora_salida <= turno.hora_entrada:              # cruza medianoche
            return "noche"
        if turno.hora_entrada.hour < 13:
            return "mañana"
        return "tarde" if turno.hora_entrada.hour < 22 else "noche"

    def localizado(self, turno_id: str) -> bool:
        """Guardia de LOCALIZACIÓN: 24 h de reloj (entrada = salida) que computan 8. No es
        presencia física sino disponibilidad, así que no ocupa el día siguiente por eso los
        patrones la encadenan con otros turnos sin contradicción.
        """
        return self.duracion(turno_id) == 24

    def duracion(self, turno_id: str) -> float:
        """Horas REALES que dura el turno de reloj a reloj. No es lo mismo que `Turno.horas`, que
        son las computadas por convenio (el partido y el de 24 h computan 8)"""
        inicio, fin = self.intervalo(turno_id, date(2000, 1, 1))
        return (fin - inicio).total_seconds() / 3600

    def disponible(self, trab_id: str, fecha: date) -> bool:
        """Devuelve si el trabajador esta disponible en esa fecha en base a sus vacaciones"""
        return not any(ini <= fecha <= fin for ini, fin in self.trabajadores[trab_id].vacaciones)

    def elegible(self, trab_id: str, turno_id: str, fecha: date) -> tuple[bool, bool]:
        """Devuelve (elegible, es_refuerzo)
        Solo si la línea opera y el trabajador está disponible.
        Normal si su capacidad cubre el tipo de día, si no, de refuerzo si tiene v=1.
        """
        if not self.opera(turno_id, fecha) or not self.disponible(trab_id, fecha):
            return (False, False)
        cap = self.capacidades.get((trab_id, turno_id))
        if cap is None:
            return (False, False)
        td = self.tipo_dia(fecha, self.turnos[turno_id].municipio)
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
                municipio=(fila.get("municipio") or "").strip(),
                vacaciones = [(vac1, vac1 + timedelta(days=14)),(vac2, vac2 + timedelta(days=14))],
                factor_jornada=factor,
                fila_inicial=fila_inicial,
                nombre=(fila.get("nombre") or "").strip(),
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
    Nacional = (01/01/2026, 24/12/2026..)
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
    for patron_id, filas_patron in patrones.items():
        rotados = {turno for fila in filas_patron for turno in fila.values()
                   if turno and turno != LIBRE and turno in turnos}
        turnos_por_patron[patron_id] = rotados

    anadidas = 0
    for trabajador_id, trabajador in trabajadores.items():
        if trabajador.tipo != "patron" or not trabajador.patron:
            continue
        for turno_id in turnos_por_patron.get(trabajador.patron, ()):
            if (trabajador_id, turno_id) not in capacidades:               # respeta lo que ya venga del CSV
                capacidades[(trabajador_id, turno_id)] = Capacidad(lv=1, sab=1, dom=1, fest=1, v=0) #Suponemos que puede hacer cualquier dia ese turno
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
    for trabajador_id, trabajador in trabajadores.items():
        if trabajador.tipo != "fijo":
            continue
        if not trabajador.linea:
            raise ValueError(f"El fijo {trabajador_id} no declara `linea` en trabajadores.csv "
                             f"(columna obligatoria para tipo=fijo)")
        if trabajador.linea not in turnos:
            raise ValueError(f"El fijo {trabajador_id} declara la línea '{trabajador.linea}', que no existe en turnos.csv")
        if (trabajador_id, trabajador.linea) not in capacidades:                 # respeta lo que ya venga del CSV
            capacidades[(trabajador_id, trabajador.linea)] = Capacidad(lv=1, sab=0, dom=0, fest=0, v=0) # Suponemos fijos trabajan lunes-viernes
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
    for trabajador_id, trabajador in trabajadores.items():
        if trabajador.tipo != "correturno":
            continue
        for turno in turnos:
            if (trabajador_id, turno) in capacidades:            # excepción declarada: manda ella, prevalece algo designado
                continue
            if orden_max.get(turno):                 # línea con designados: no es para el, debe ceder su capacidad
                continue
            capacidades[(trabajador_id, turno)] = Capacidad(lv=1, sab=1, dom=1, fest=1, v=0)
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
    for trabajador_id, trabajador in trabajadores.items():
        if trabajador.tipo == "patron" and trabajador.patron:
            grupos.setdefault(trabajador.patron, []).append(trabajador_id)

    offsets: dict[str, int] = {}
    for patron_id, lista_trabajadores_id in grupos.items():
        T = len(patrones.get(patron_id) or ())
        if not T:
            continue                                   # patrón sin filas: no hay rotación que anclar
        for orden, trabajador_id in enumerate(sorted(lista_trabajadores_id)):
            declarada = trabajadores[trabajador_id].fila_inicial
            offsets[trabajador_id] = (declarada if declarada is not None else orden) % T
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
        # Año y parámetros del convenio (config.toml). Única fuente: todas las etapas, la salida
        # y el validador leen de aquí.
        config=_cargar_config(),
    )

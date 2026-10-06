"""
Fichero con el dominio del Problema
"""
from enum import Enum
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta



class TipoDia(Enum):
    LV = "LV"
    SABADO = "Sabado"
    DOMINGO = "Domingo"
    FESTIVO = "Festivos"


class Franja(Enum):
    MANANA = "mañana"
    TARDE = "tarde"
    NOCHE = "noche"
    PARTIDO = "partido"
    LOCALIZADO = "localizado"

class TipoDescanso(Enum):
    DS = "DS"
    DF = "DF"
    DO = "DO"

class TipoTrabajador(Enum):
    FIJO = "fijo"
    CORRETURNO = "correturno"
    PATRON = "patron"

@dataclass
class Turno:
    id_turno: str       #Id del turno (lo suponemos único)
    base: str           #Base en el que opera el turno
    franja: Franja        #Tipo del turno en base "mañana","tarde","partido"...
    hora_entrada: time  #Hora de entrada del turno
    hora_salida: time   #Hora de salida del turno
    dias: set[TipoDia] #Los dias de la semana que opera
    minutos_computo: int # horas de computo
    ayudante: bool
    titulares: list[str] = field(default_factory=list)   # los que la tienen asignada: la hacen todos los
                                                          # días que opera y no hacen otra. Los de patrón
                                                          # se añaden solos al crear Datos
    cubridores: list[str] = field(default_factory=list)  # si faltan los titulares, en orden de preferencia:
                                                          # el primero es el principal, el resto suplentes

    @property
    def personas(self) -> int:
        """Cuántas personas pide la línea cada día que opera: conductor, y ayudante si lo lleva."""
        return 2 if self.ayudante else 1


@dataclass
class Restriccion:
    dias: set[TipoDia]              # a qué tipos de día aplica
    lineas: set[str] | None = None  # esos días, solo estas líneas. set() = ninguna
    desde: time | None = None       # solo turnos que entren a esta hora o después
    hasta: time | None = None       # y que salgan a esta hora o antes. Si no es posterior a
                                    # `desde`, es del día siguiente: 16:00 a 0:00 acaba a medianoche


@dataclass
class Trabajador:
    id: str                         #Nif único
    nombre: str                     # nombre y apellidos, solo para la salida a Excel
    tipo: TipoTrabajador            # fijo | patron | correturno
    patron: str | None              # id del patrón (solo tipo=patron: noches y UVI)
    vacaciones: list[tuple[date, date]] #Lista con tupla (inicio_vacaciones,fin_vacaciones)
    base: str                       # base a la que pertenece
    factor_jornada: int = 100       # reducción de jornada: escala el objetivo anual. 100 = jornada completa
    fila_inicial: int | None = None # solo tipo=patron: fila de `patrones.csv` que hace en la PRIMERA
                                    # semana del horizonte.
    restricciones: list[Restriccion] = field(default_factory=list)  # conciliaciones y días que no trabaja


@dataclass
class Patron:
    id: str
    filas: list[list[str]]      # cada fila son 7 celdas (índice = weekday()): turno o descanso
    turnos: set[str] = field(init=False)    # las líneas que recorre; se calcula una vez al crearlo

    def __post_init__(self):
        descansos = {d.value for d in TipoDescanso}
        self.turnos = {c for fila in self.filas for c in fila if c not in descansos}

    def celda(self, fila_inicial: int, semanas: int, dia_semana: int) -> str:
        """Lo que toca hacer `semanas` después de la primera, empezando en `fila_inicial`."""
        return self.filas[(fila_inicial + semanas) % len(self.filas)][dia_semana]


@dataclass(frozen=True)
class Convenio:
    nombre: str
    horas_anuales: int              # jornada anual
    descanso_minimo: int            # horas entre el fin de un turno y el inicio del siguiente  -> C4
    dias_max_semana: int            # máx. días trabajados por semana ISO                        -> C5
    horas_max_semana: int           # máx. horas trabajados por semana ISO                     -> C6
    libres_semana: int              # días de descanso semanal, nunca en festivo


@dataclass(frozen=True)
class Zona:
    nombre: str
    convenio: Convenio


@dataclass
class Datos:
    anio: int                                   # año del cuadrante: del 1 de enero al 31 de diciembre
    zona: Zona
    turnos: dict[str, Turno]                    # id_turno -> turno
    trabajadores: dict[str, Trabajador]         # id -> trabajador
    patrones: dict[str, Patron]                 # id -> patrón
    festivos: dict[str, set[date]]              # base -> sus festivos, nacionales incluidos

    def __post_init__(self):
        # Los de patrón son titulares de las líneas que recorre su patrón: no hace falta declararlo
        for trabajador_id, t in self.trabajadores.items():
            if t.tipo == TipoTrabajador.PATRON:
                for turno_id in sorted(self.patrones[t.patron].turnos):
                    if trabajador_id not in self.turnos[turno_id].titulares:
                        self.turnos[turno_id].titulares.append(trabajador_id)
        # El pool de cada línea no depende del día: se calcula una vez
        self.pools = {turno_id: self._pool(turno_id) for turno_id in self.turnos}

    def _pool(self, turno_id: str) -> list[str]:
        """Quién más puede hacer la línea, además de sus titulares y cubridores:

          * sin titulares ni cubridores: los fijos de su base y los correturnos
          * con titulares y sin cubridores: solo correturnos
          * con cubridores: nadie

        Quien es titular de alguna línea no entra en ningún pool: solo hace las suyas. Y un
        correturno nunca hace noches ni guardias localizadas de otra base."""
        turno = self.turnos[turno_id]
        if turno.cubridores:
            return []
        con_lineas = {w for t in self.turnos.values() for w in t.titulares}
        pool = []
        for trabajador_id, t in self.trabajadores.items():
            if trabajador_id in con_lineas:
                continue
            if t.tipo == TipoTrabajador.CORRETURNO:
                if not (turno.franja in (Franja.LOCALIZADO, Franja.NOCHE) and turno.base != t.base):
                    pool.append(trabajador_id)
            elif t.tipo == TipoTrabajador.FIJO and not turno.titulares and turno.base == t.base:
                pool.append(trabajador_id)
        return pool

    @property
    def inicio(self) -> date:
        return date(self.anio, 1, 1)

    @property
    def fin(self) -> date:
        return date(self.anio, 12, 31)

    @property
    def lista_dias_calendario(self) -> list[date]:
        """Días del año, del 1 de enero al 31 de diciembre."""
        return [self.inicio + timedelta(days=i) for i in range((self.fin - self.inicio).days + 1)]

    @property
    def primer_lunes(self) -> date:
        """Lunes desde el que se cuentan las semanas de la rotación"""
        return self.inicio - timedelta(days=self.inicio.weekday())

    def es_festivo(self, fecha: date, base: str) -> bool:
        return fecha in self.festivos.get(base, set())

    def tipo_dia(self, fecha: date, base: str) -> TipoDia:
        """El festivo manda sobre el día de la semana: un sábado festivo es FESTIVO."""
        if self.es_festivo(fecha, base):
            return TipoDia.FESTIVO
        dia_semana = fecha.weekday()
        return TipoDia.LV if dia_semana < 5 else TipoDia.SABADO if dia_semana == 5 else TipoDia.DOMINGO

    def opera(self, turno_id: str, fecha: date) -> bool:
        turno = self.turnos[turno_id]
        return self.tipo_dia(fecha, turno.base) in turno.dias

    def disponible(self, trabajador_id: str, fecha: date) -> bool:
        """Si no está de vacaciones ese día."""
        return not any(ini <= fecha <= fin for ini, fin in self.trabajadores[trabajador_id].vacaciones)

    def minutos_objetivo(self, trabajador_id: str) -> int:
        """Su jornada anual: la del convenio, escalada por su reducción de jornada."""
        return self.zona.convenio.horas_anuales * 60 * self.trabajadores[trabajador_id].factor_jornada // 100

    def prescrito(self, trabajador_id: str, fecha: date) -> str | TipoDescanso | None:
        """Lo que su rotación le marca ese día, esté o no de vacaciones: un turno si la línea
        opera ese día, el descanso si la celda es un descanso, o None si no le marca nada."""
        trabajador = self.trabajadores[trabajador_id]
        if trabajador.tipo != TipoTrabajador.PATRON:
            return None
        semanas = (fecha - self.primer_lunes).days // 7
        celda = self.patrones[trabajador.patron].celda(trabajador.fila_inicial, semanas, fecha.weekday())
        if celda in {d.value for d in TipoDescanso}:
            return TipoDescanso(celda)
        return celda if self.opera(celda, fecha) else None

    def puede(self, trabajador_id: str, turno_id: str, fecha: date) -> bool:
        """Si ese día puede hacer esa línea: no está de vacaciones y sus restricciones de ese tipo
        de día (conciliaciones, días que no trabaja) se lo permiten."""
        if not self.disponible(trabajador_id, fecha):
            return False
        tipo = self.tipo_dia(fecha, self.turnos[turno_id].base)
        for r in self.trabajadores[trabajador_id].restricciones:
            if tipo not in r.dias:
                continue
            if r.lineas is not None and turno_id not in r.lineas:
                return False
            inicio, fin = self.intervalo(turno_id, fecha)
            if r.desde is not None and inicio < datetime.combine(fecha, r.desde):
                return False
            if r.hasta is not None:
                limite = datetime.combine(fecha, r.hasta)
                if r.desde is not None and r.hasta <= r.desde:
                    limite += timedelta(days=1)     # la ventana acaba al día siguiente (16:00 a 0:00)
                if fin > limite:
                    return False
        return True

    def grupos(self, turno_id: str, fecha: date) -> tuple[list[str], list[str], list[str]]:
        """Quién puede hacer esa línea ese día, en los tres grupos que se consultan en orden:
        (titulares, cubridores, pool). Se pasa al siguiente solo si en el anterior no hay nadie
        a quien poner. De cada grupo sale quien ese día no puede (vacaciones, restricciones): un
        titular que no trabaja findes no aparece el sábado. No mira la legalidad contra el plan:
        eso es legal.py."""
        if not self.opera(turno_id, fecha):
            return [], [], []
        turno = self.turnos[turno_id]
        return tuple([w for w in grupo if self.puede(w, turno_id, fecha)]
                     for grupo in (turno.titulares, turno.cubridores, self.pools[turno_id]))

    def intervalo(self, turno_id: str, fecha: date) -> tuple[datetime, datetime]:
        """Inicio y fin reales del turno empezando en `fecha`. Si sale a la misma hora o antes de
        entrar, acaba al día siguiente: así se miden bien los descansos de las noches."""
        turno = self.turnos[turno_id]
        inicio = datetime.combine(fecha, turno.hora_entrada)
        fin = datetime.combine(fecha, turno.hora_salida)
        if fin <= inicio:
            fin += timedelta(days=1)
        return inicio, fin


class Plan:
    """El cuadrante en construcción: (trabajador, fecha) -> id de turno o descanso. Un día que no
    está es un HUECO.

    Lleva además los minutos trabajados de cada uno. Como solo se escribe con `poner` y `quitar`,
    la cuenta y el plan no pueden discrepar."""

    def __init__(self, datos: Datos):
        self.datos = datos
        self._casillas: dict[tuple[str, date], str | TipoDescanso] = {}
        self._minutos: dict[str, int] = {trabajador_id: 0 for trabajador_id in datos.trabajadores}

    # -- escribir ------------------------------------------------------------ #
    def poner(self, trabajador_id: str, fecha: date, valor: str | TipoDescanso) -> None:
        """Le pone ese turno o descanso ese día. Si ya tenía algo, lo sustituye."""
        self.quitar(trabajador_id, fecha)
        self._casillas[(trabajador_id, fecha)] = valor
        if not isinstance(valor, TipoDescanso):
            self._minutos[trabajador_id] += self.datos.turnos[valor].minutos_computo

    def quitar(self, trabajador_id: str, fecha: date) -> str | TipoDescanso | None:
        """Deja ese día en hueco. Devuelve lo que había, o None."""
        valor = self._casillas.pop((trabajador_id, fecha), None)
        if valor is not None and not isinstance(valor, TipoDescanso):
            self._minutos[trabajador_id] -= self.datos.turnos[valor].minutos_computo
        return valor

    # -- leer ---------------------------------------------------------------- #
    def get(self, trabajador_id: str, fecha: date) -> str | TipoDescanso | None:
        """Lo que tiene ese día: turno, descanso, o None si es hueco."""
        return self._casillas.get((trabajador_id, fecha))

    def items(self):
        """((trabajador, fecha), valor) de todo lo que hay en el plan."""
        return self._casillas.items()

    def turno_de(self, trabajador_id: str, fecha: date) -> str | None:
        """El turno que trabaja ese día. None si descansa O si está en hueco: para contar horas,
        días y descanso entre jornadas los dos son iguales, no se trabaja."""
        valor = self.get(trabajador_id, fecha)
        return None if isinstance(valor, TipoDescanso) else valor

    def hueco(self, trabajador_id: str, fecha: date) -> bool:
        """Si ese día no tiene nada asignado y se puede rellenar. Un descanso NO es hueco: se
        respeta igual que un turno."""
        return (trabajador_id, fecha) not in self._casillas

    def minutos(self, trabajador_id: str) -> int:
        """Minutos trabajados en todo el año con lo que hay ahora en el plan."""
        return self._minutos[trabajador_id]

    def cabe(self, trabajador_id: str, turno_id: str) -> bool:
        """Si ese turno le cabe todavía en su jornada anual."""
        return (self._minutos[trabajador_id] + self.datos.turnos[turno_id].minutos_computo
                <= self.datos.minutos_objetivo(trabajador_id))


CONVENIO_CYL = Convenio(
    nombre="V Convenio de transporte sanitario de Castilla y León",
    horas_anuales=1776,
    descanso_minimo=12,
    dias_max_semana=6,
    horas_max_semana=48,
    libres_semana=2,
)

ZONAS = {
    "Valladolid": Zona("Valladolid", CONVENIO_CYL),
}

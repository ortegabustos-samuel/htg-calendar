"""
libranzas.py — Paso B: bajar a todo el mundo de la jornada anual cediendo días de trabajo.

Casi todo patrón prescribe más horas de las que caben en el objetivo (+39 a +71 h de media en
Valladolid 2026), así que ceder tiempo no es un caso raro: es la mecánica central del problema.
Este paso decide QUÉ días se ceden y, cuando la plaza tiene cubridor designado, QUIÉN la asume.

Va en dos fases, y el orden importa:

  FASE 1 — plazas con cubridor designado (`v >= 1` en capacidades.csv). Son pocas —5 de 72 líneas—
  pero son las delicadas: solo una o dos personas pueden hacerlas, y ese cubridor va él mismo
  cargado de horas. Aquí ceder no es abrir un hueco, es TRASPASAR la plaza: el cubridor asume el
  ciclo entero (los turnos y el descanso que la plaza arrastra) y suelta lo que él tuviera esos
  días, que sí queda como hueco para los pasos siguientes. Cubrir, para él, es también una forma
  de bajar horas.

  FASE 2 — el resto. Ceder hasta bajar del objetivo, eligiendo los días en que más gente hay libre
  para tapar el agujero. Sin traspaso: el hueco queda para los pasos C y D.

Cuánto se cede de golpe lo decide `ritmo.py`: las plazas rígidas (las que devuelven tanto descanso
como trabajo, como el binomio de noche) se ceden por ciclos enteros, porque un día suelto de noche
no tiene descanso que se le pueda medir; las flexibles admiten bloques o días sueltos.

Durante la fase 1 el techo anual NO bloquea —el cubridor puede quedar por encima a mitad de
camino, porque la fase 2 lo baja después—, pero al terminar el paso B nadie puede seguir por
encima. `comprobar` lo verifica y lo canta.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import base, legal, ritmo as ritmo_mod
from cargar_datos import Datos
from horas import EPS, LibroHoras
from ritmo import Ritmo

Plan = dict[tuple[str, date], str]

VENTANA_REPARTO = 28        # a partir de 4 semanas de separación, dos cesiones ya no se estorban


@dataclass
class Unidad:
    """Lo que se cede de una vez: los días de trabajo que suelta el titular y el descanso que la
    plaza arrastra con ellos. La `ventana` es todo el tramo, y es lo que asume un cubridor."""
    dias: list[date]
    descanso: list[date]
    horas: float

    @property
    def ventana(self) -> list[date]:
        return sorted(self.dias + self.descanso)


@dataclass
class Cesion:
    """Una libranza concedida. Es la unidad del informe que se revisa a mano."""
    fase: int
    titular: str
    dias: list[date]
    horas: float
    cubridor: str | None            # None = la plaza queda como hueco
    desalojadas: int                # plazas que el cubridor soltó al asumirla
    motivo: str


@dataclass
class Registro:
    """Estado que arrastra el paso B mientras decide."""
    cesiones: list[Cesion] = field(default_factory=list)
    protegidos: dict[str, set[date]] = field(default_factory=lambda: defaultdict(set))
    compromisos: dict[str, list[tuple[date, date]]] = field(default_factory=lambda: defaultdict(list))
    cedidos_grupo: dict[tuple[str, date], int] = field(default_factory=lambda: defaultdict(int))


# --------------------------------------------------------------------------- #
#  Consultas de apoyo
# --------------------------------------------------------------------------- #
def designados(datos: Datos) -> dict[str, list[str]]:
    """Línea -> cubridores declarados, por orden de preferencia (`v` = 1 principal, 2, 3…)."""
    por_linea: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (trab, turno), cap in datos.capacidades.items():
        if cap.v >= 1:
            por_linea[turno].append((cap.v, trab))
    return {turno: [w for _, w in sorted(gente)] for turno, gente in por_linea.items()}


def _libres(datos: Datos, plan: Plan, libro: LibroHoras, turno: str, f: date,
            excluir: set[str], cache: dict | None = None) -> int:
    """Cuánta gente podría tapar esta plaza este día: puede hacerla, no tiene nada asignado y le
    caben las horas. No se comprueba la legalidad — es cara y aquí solo se está PUNTUANDO, no
    asignando; quien asigne de verdad (pasos C y D) sí la comprueba."""
    clave = (turno, f, frozenset(excluir))
    if cache is not None and clave in cache:
        return cache[clave]
    n = 0
    for c in datos.trabajadores:
        if c in excluir or (c, f) in plan:
            continue
        if datos.elegible(c, turno, f)[0] and libro.cabe(c, turno):
            n += 1
    if cache is not None:
        cache[clave] = n
    return n


def _distancia(reg: Registro, quien: str, unidad: Unidad) -> int:
    """Días hasta la cesión ya concedida más cercana de esta misma persona. Es lo que reparte las
    libranzas por el año en vez de amontonarlas en las semanas flojas."""
    if not reg.compromisos[quien]:
        return VENTANA_REPARTO
    ini, fin = unidad.ventana[0], unidad.ventana[-1]
    cerca = min(max((a - fin).days, (ini - b).days, 0) for a, b in reg.compromisos[quien])
    return min(cerca, VENTANA_REPARTO)


def _solapa(reg: Registro, quien: str, unidad: Unidad) -> bool:
    ini, fin = unidad.ventana[0], unidad.ventana[-1]
    return any(a <= fin and ini <= b for a, b in reg.compromisos[quien])


# --------------------------------------------------------------------------- #
#  Unidades candidatas
# --------------------------------------------------------------------------- #
def _descanso_de(dias: list[date], rit: Ritmo, plan: Plan, trab: str) -> list[date]:
    """Días de descanso que la plaza arrastra con esos días de trabajo: `ratio` días por cada día
    cedido, tomados justo detrás. Con ratio 1,00 (noches) un bloque de 7 arrastra 7; con 0,40
    (semana laboral normal) un día suelto arrastra 0, que es lo correcto."""
    cuantos = int(round(rit.ratio * len(dias)))
    salida: list[date] = []
    f = dias[-1] + timedelta(days=1)
    while len(salida) < cuantos and (trab, f) not in plan:
        salida.append(f)
        f += timedelta(days=1)
    return salida


def _huerfano_domingo(datos: Datos, plan: Plan, trab: str, dias: list[date]) -> bool:
    """Ceder estos días juntos, ¿deja huérfano un domingo que el titular seguiría trabajando?

    Un día `f` lo deja huérfano si el siguiente tiene turno en `plan`, ese siguiente NO está
    también en `dias` (si lo estuviera, se ceden ambos juntos: no hay orfandad) y es domingo. No
    se exige que `f` mismo sea tipo SAB: un sábado festivo debe seguir contando como sábado
    trabajado si el titular lo trabaja — el criterio es posicional (el día natural anterior), no
    por etiqueta de tipo (mismo criterio que ya corrigió la restricción del CP-SAT en residuo.py)."""
    conjunto = set(dias)
    for f in dias:
        siguiente = f + timedelta(days=1)
        if siguiente in conjunto:
            continue
        turno_siguiente = plan.get((trab, siguiente))
        if turno_siguiente is None:
            continue
        if datos.tipo_dia(siguiente, datos.turnos[turno_siguiente].municipio) == "DOM":
            return True
    return False


def candidatas(datos: Datos, plan: Plan, rit: Ritmo, trab: str, exceso: float,
               protegidos: set[date]) -> list[Unidad]:
    """Qué se le puede quitar a este trabajador, según lo rígida que sea su plaza."""
    bloques = [b for b in ritmo_mod.bloques(plan, trab)
               if not (protegidos & set(b))]
    if not bloques:
        return []

    def unidad(dias: list[date]) -> Unidad:
        return Unidad(dias=dias,
                      descanso=_descanso_de(dias, rit, plan, trab),
                      horas=sum(datos.turnos[plan[(trab, f)]].horas for f in dias))

    if rit.rigido:
        # No se fracciona: se cede el ciclo entero aunque pase de largo del exceso. Lo que sobre
        # deja al titular por debajo del objetivo, y esa holgura la aprovechan los pasos C y D.
        return [unidad(b) for b in bloques if not _huerfano_domingo(datos, plan, trab, b)]

    # En las plazas flexibles se cede SIEMPRE día a día, nunca el bloque entero. Ceder una semana
    # de golpe abre cinco días seguidos de la misma línea, y taparlos exige encontrar a alguien
    # libre los cinco; repartidos por el año, cada uno se tapa por separado y con mucha más gente
    # disponible. El exceso típico de un patrón (+71 h) son nueve días sueltos, no dos semanas.
    return [unidad([f]) for b in bloques for f in b
            if not _huerfano_domingo(datos, plan, trab, [f])]


# --------------------------------------------------------------------------- #
#  FASE 1 — plazas con cubridor designado
# --------------------------------------------------------------------------- #
def _rompe_costura(datos: Datos, plan: Plan, cubridor: str, dias: list[date],
                   dentro: set[date], turnos: dict[date, str]) -> tuple[date, date] | None:
    """Primer día asumido cuya COSTURA con el horario propio del cubridor incumple el descanso.

    Solo se mira la costura, nunca el interior: los días de dentro de la ventana son el ciclo que
    la plaza prescribe y van exentos por decisión —si era válido para el titular lo es para quien lo
    hereda—. Pero el empalme con lo que el cubridor tenía justo antes o justo después de la ventana
    no lo hereda de nadie: se lo inventa el pipeline al juntar dos horarios, y ahí es donde salían
    noches que acaban a las 08:30 pegadas a un turno propio que empieza a las 07:00.

    El localizado no cuenta: es disponibilidad, no presencia, así que no ocupa el día contiguo.

    Se mira también el tope de HORAS por ventana de 7 días, y ahí hay que hilar fino: el bloque de
    noche por sí solo son 77 h contra un tope de 48, así que exigir C6 a secas haría fallar cualquier
    ventana que lo contenga y el recorte se comería el traspaso entero. Lo que se busca es otra cosa:
    una ventana que se pase Y que ademas mezcle turnos PROPIOS del cubridor. Esos son los que
    sobran — el ciclo que hereda le da derecho a descansar esos días.

    Devuelve (día asumido, día vecino que estorba) para que quien llama pueda elegir qué ceder.
    """
    minimo = timedelta(hours=datos.config.descanso_minimo)
    for f in dias:
        s = turnos[f]
        for vecino in (f - timedelta(days=1), f + timedelta(days=1)):
            if vecino in dentro:
                continue
            otro = plan.get((cubridor, vecino))
            if otro is None or datos.localizado(otro) or datos.localizado(s):
                continue
            antes, despues = ((otro, s) if vecino < f else (s, otro))
            dia = min(vecino, f)
            if (datos.intervalo(despues, dia + timedelta(days=1))[0]
                    - datos.intervalo(antes, dia)[1]) < minimo:
                return f, vecino

    asumidos = set(dias)
    for ancla in dias:
        for arranque in range(-6, 1):
            inicio = ancla + timedelta(days=arranque)
            ventana = [inicio + timedelta(days=i) for i in range(7)]
            if not (asumidos & set(ventana)):
                continue
            propios = [g for g in ventana if g not in dentro and (cubridor, g) in plan]
            if not propios:
                continue                        # solo el bloque heredado: eso va pactado
            total = (sum(datos.turnos[turnos[g]].horas for g in ventana if g in asumidos)
                     + sum(datos.turnos[plan[(cubridor, g)]].horas for g in propios))
            if total > datos.config.horas_max_semana:
                return ancla, max(propios, key=lambda g: datos.turnos[plan[(cubridor, g)]].horas)
    return None


def _recortar(datos: Datos, plan: Plan, cubridor: str, unidad: Unidad,
              turnos: dict[date, str]) -> Unidad | None:
    """La misma unidad sin los días en que el cubridor no está. Devuelve None si no le queda ninguno.

    Un solo filtro: sus vacaciones. Y recorta en vez de rechazar, porque un día de solape no debe
    tirar por tierra el traspaso de una quincena entera — se le da lo que sí puede hacer y el resto
    queda como hueco para los pasos siguientes.

    No hay comprobación legal, y es deliberado. Lo que se traspasa es el ciclo de una plaza tal y
    como lo prescribe su patrón — un horario que otro trabajador ya venía haciendo. Si era válido
    para el titular lo es para quien lo hereda; los patrones se dan por buenos como están. Exigirle
    aquí el convenio significaría rechazar todos los traspasos, porque la semana del binomio de
    noche son 77 h en 7 días contra un tope de 48.

    Sus horas tampoco filtran: va cargado como todo el mundo, y asumir la plaza soltando la suya es
    precisamente una forma de bajarlas — la fase 2 le ajusta lo que quede.
    """
    dias = [f for f in unidad.dias if datos.disponible(cubridor, f)]
    descanso = [f for f in unidad.descanso if datos.disponible(cubridor, f)]
    for _ in range(len(unidad.ventana) + 4):        # se resuelve la costura, con tope de vueltas
        # `dentro` se recalcula en cada vuelta, y es imprescindible: un día que sale de la ventana
        # deja de traspasarse, así que el cubridor CONSERVA su turno propio de ese día y pasa a ser
        # un vecino que hay que mirar.
        dentro = set(dias) | set(descanso)
        malo = _rompe_costura(datos, plan, cubridor, dias, dentro, turnos)
        if malo is None:
            break
        f, vecino = malo
        if vecino not in dentro and datos.disponible(cubridor, vecino):
            # Se estira la ventana para que LIBRE ese día suyo, en vez de renunciar a la plaza. Es
            # lo natural: quien asume un ciclo hereda también su descanso, así que el turno propio
            # que choca con él es precisamente el que sobra. Además deja ese turno como hueco, que
            # el pool puede cubrir en lugar de hacer un refuerzo sin demanda.
            descanso.append(vecino)
        else:
            dias.remove(f)
        if not dias:
            break
    if not dias:
        return None
    if len(dias) == len(unidad.dias) and len(descanso) == len(unidad.descanso):
        return unidad
    return Unidad(dias=dias, descanso=descanso,
                  horas=sum(datos.turnos[turnos[f]].horas for f in dias))


def _coste_fase1(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro, titular: str,
                 cubridor: str, unidad: Unidad, sin_asumir: int) -> tuple:
    """Menor es mejor, por orden:

    1. `sin_asumir` — días de la plaza designada que este cubridor NO puede coger. Va primero
       porque es el objetivo de la fase: que la plaza no se quede sola. Sin este término el coste
       premiaba recortar, y un traspaso de 3 días parecía más barato que el de 15 que hacía falta.
    2. Huecos que el cubridor abre en SU propia línea y que nadie más puede tapar.
    3. Distancia a sus otros compromisos: reparte por el año.
    4. Cuánto lo acerca a su propio objetivo de horas — esto es lo que reparte la carga entre el
       principal y los suplentes, en vez del orden ciego de `v`.
    """
    sin_tapar = 0
    suelta = 0.0
    for f in unidad.ventana:
        s = plan.get((cubridor, f))
        if s is None:
            continue
        suelta += datos.turnos[s].horas
        if _libres(datos, plan, libro, s, f, {titular, cubridor}) == 0:
            sin_tapar += 1
    horas_finales = libro.horas(cubridor) + unidad.horas - suelta
    return (sin_asumir,
            sin_tapar,
            -_distancia(reg, cubridor, unidad),
            abs(horas_finales - libro.objetivo(cubridor)))


def _mejor_cubridor(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro, titular: str,
                    unidad: Unidad, turnos: dict[date, str], cubridores: list[str], *,
                    protege_domingo_titular: bool = False):
    """(coste, cubridor, unidad) del cubridor más barato, ya recortada a lo que ese cubridor puede
    asumir. None si ninguno puede.

    `_recortar` puede dejar la unidad más pequeña que la que aprobó `candidatas()` — si le quita
    el domingo de un sábado que sigue dentro, el titular se queda con ese domingo huérfano al
    soltar el sábado. `protege_domingo_titular` (solo lo activa la cesión por exceso, donde el
    titular SÍ suelta lo suyo) descarta esos cubridores; la ausencia no lo necesita porque ahí no
    se toca el plan del titular."""
    mejor = None
    for cubridor in cubridores:
        if cubridor == titular or _solapa(reg, cubridor, unidad):
            continue
        suya = _recortar(datos, plan, cubridor, unidad, turnos)
        if suya is None:
            continue
        if protege_domingo_titular and _huerfano_domingo(datos, plan, titular, suya.dias):
            continue
        coste = _coste_fase1(datos, plan, libro, reg, titular, cubridor, suya,
                             sin_asumir=len(unidad.dias) - len(suya.dias))
        if mejor is None or coste < mejor[0]:
            mejor = (coste, cubridor, suya)
    return mejor


def _aplicar_fase1(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro,
                   titular: str, cubridor: str, unidad: Unidad, turnos: dict[date, str],
                   motivo: str, suelta_titular: bool) -> None:
    if suelta_titular:                                  # cesión por exceso: el titular los suelta
        for f in unidad.dias:
            libro.borra(titular, plan.pop((titular, f)))
    desalojadas = 0
    for f in unidad.ventana:                            # el cubridor suelta lo suyo en la ventana
        if (cubridor, f) in plan:
            libro.borra(cubridor, plan.pop((cubridor, f)))
            desalojadas += 1
    for f in unidad.dias:                               # y asume la plaza
        plan[(cubridor, f)] = turnos[f]
        libro.apunta(cubridor, turnos[f])

    reg.protegidos[cubridor] |= set(unidad.dias)
    reg.compromisos[cubridor].append((unidad.ventana[0], unidad.ventana[-1]))
    reg.cesiones.append(Cesion(
        fase=1, titular=titular, dias=list(unidad.dias), horas=unidad.horas,
        cubridor=cubridor, desalojadas=desalojadas, motivo=motivo))


def _ausencias(datos: Datos, titular: str, designadas: dict[str, list[str]]) -> list[Unidad]:
    """Periodos de vacaciones del titular en que su plaza tiene cubridor designado.

    La unidad es la QUINCENA ENTERA, no las rachas de días sueltos que haya dentro: el cubridor
    hereda el periodo tal cual lo habría hecho el titular — trabaja donde él habría trabajado y
    descansa donde él habría descansado. Como las vacaciones son 15 días y el ciclo del patrón 14,
    siempre sobra o falta algún día suelto en los bordes, y eso da igual.
    """
    unidades: list[Unidad] = []
    for inicio, fin in datos.trabajadores[titular].vacaciones:
        ventana = [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)
                   if datos.inicio <= inicio + timedelta(days=i) <= datos.fin]
        dias = [f for f in ventana if base.prescrito(datos, titular, f) in designadas]
        if not dias:
            continue
        unidades.append(Unidad(
            dias=dias,
            descanso=[f for f in ventana if f not in set(dias)],
            horas=sum(datos.turnos[base.prescrito(datos, titular, f)].horas for f in dias)))
    return unidades


def fase1(datos: Datos, plan: Plan, libro: LibroHoras, ritmos: dict[str, Ritmo],
          reg: Registro) -> None:
    """Mantiene atendidas las plazas con cubridor designado, traspasándolas por ciclos completos.

    Dos motivos las dejan solas, y se atienden en ese orden: la AUSENCIA del titular (forzado — la
    plaza está vacía y solo esa gente puede hacerla) y su CESIÓN por exceso de horas (electivo —
    ahí elegimos nosotros qué ciclo se cede). Lo forzado antes que lo elegible.
    """
    cubridores = designados(datos)
    # Titular de una plaza designada = quien la hace por capacidad NORMAL. Los cubridores son los
    # que llevan `v >= 1` en esa misma línea, y no son titulares de ella.
    titulares: set[str] = {w for (w, s), cap in datos.capacidades.items()
                           if s in cubridores and cap.v == 0}

    for titular in sorted(titulares):                   # 1) ausencias
        for unidad in _ausencias(datos, titular, cubridores):
            turnos = {f: base.prescrito(datos, titular, f) for f in unidad.dias}
            linea = turnos[unidad.dias[0]]
            # Lo que un cubridor no pueda asumir (porque libre él también) se le ofrece al
            # siguiente: una quincena puede repartirse entre el principal y el suplente.
            pendiente: Unidad | None = unidad
            while pendiente is not None:
                mejor = _mejor_cubridor(datos, plan, libro, reg, titular, pendiente, turnos,
                                        cubridores[linea])
                if mejor is None:
                    print(f"  aviso  {linea} sin cubrir del {pendiente.dias[0]:%d/%m} al "
                          f"{pendiente.dias[-1]:%d/%m} ({len(pendiente.dias)} d): "
                          f"{titular} de vacaciones y ningún cubridor libre")
                    break
                _, cubridor, asumida = mejor
                _aplicar_fase1(datos, plan, libro, reg, titular, cubridor, asumida, turnos,
                               motivo=f"vacaciones de {titular}", suelta_titular=False)
                resto = [f for f in pendiente.dias if f not in set(asumida.dias)]
                pendiente = None if not resto else Unidad(
                    dias=resto,
                    descanso=[f for f in pendiente.descanso if f not in set(asumida.ventana)],
                    horas=sum(datos.turnos[turnos[f]].horas for f in resto))

    pendientes = sorted((w for w in titulares if libro.exceso(w) > EPS),
                        key=lambda w: -libro.exceso(w))
    for titular in pendientes:                          # 2) cesión por exceso
        rit = ritmos[ritmo_mod.grupo_de(datos, titular)]
        while libro.exceso(titular) > EPS:
            mejor = None
            for unidad in candidatas(datos, plan, rit, titular, libro.exceso(titular),
                                     reg.protegidos[titular]):
                turnos = {f: plan[(titular, f)] for f in unidad.dias}
                if not all(s in cubridores for s in turnos.values()):
                    continue                            # tramo mixto: no es plaza designada
                elegido = _mejor_cubridor(datos, plan, libro, reg, titular, unidad, turnos,
                                          cubridores[turnos[unidad.dias[0]]],
                                          protege_domingo_titular=True)
                if elegido is not None and (mejor is None or elegido[0] < mejor[0]):
                    mejor = (elegido[0], elegido[2], elegido[1], turnos)
            if mejor is None:
                print(f"  aviso  {titular} se queda en {libro.horas(titular):.0f} h "
                      f"({libro.exceso(titular):+.0f}): ningún cubridor puede asumir lo que cede")
                break
            _, unidad, cubridor, turnos = mejor
            _aplicar_fase1(datos, plan, libro, reg, titular, cubridor, unidad, turnos,
                           motivo=f"exceso de {titular}", suelta_titular=True)


# --------------------------------------------------------------------------- #
#  FASE 2 — el resto de la plantilla
# --------------------------------------------------------------------------- #
def _tope_simultaneo(datos: Datos, libro: LibroHoras, grupo: str, gente: list[str]) -> int:
    """Cuántos del mismo grupo pueden estar librando el mismo día. Se deriva de lo que el grupo
    tiene que ceder: el mínimo que hace el reparto posible, para que salga lo más espaciado que
    quepa. Si aun así no cabe, `fase2` lo relaja y avisa."""
    a_ceder = sum(max(0.0, libro.exceso(w)) for w in gente)
    if a_ceder <= 0:
        return 1
    horas_dia = sum(libro.horas(w) for w in gente) / max(1, sum(1 for w in gente))
    dias_persona = a_ceder / max(1.0, horas_dia / 365)
    return max(1, math.ceil(dias_persona / 365))


def _coste_fase2(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro,
                 trab: str, unidad: Unidad, cache: dict | None = None) -> tuple:
    sin_tapar, total = 0, 0
    for f in unidad.dias:
        libres = _libres(datos, plan, libro, plan[(trab, f)], f, {trab}, cache)
        total += libres
        if libres == 0:
            sin_tapar += 1
    return (sin_tapar, -_distancia(reg, trab, unidad), -total)


def fase2(datos: Datos, plan: Plan, libro: LibroHoras, ritmos: dict[str, Ritmo],
          reg: Registro) -> None:
    grupos: dict[str, list[str]] = defaultdict(list)
    for w in datos.trabajadores:
        grupos[ritmo_mod.grupo_de(datos, w)].append(w)
    topes = {g: _tope_simultaneo(datos, libro, g, gente) for g, gente in grupos.items()}

    pendientes = sorted((w for w in datos.trabajadores if libro.exceso(w) > EPS),
                        key=lambda w: -libro.exceso(w))
    for trab in pendientes:
        grupo = ritmo_mod.grupo_de(datos, trab)
        rit = ritmos[grupo]
        while libro.exceso(trab) > EPS:
            mejor = None
            cache: dict = {}                            # válido mientras no se aplique una cesión
            for holgura in range(0, 4):                 # el tope se relaja solo si no cabe
                tope = topes[grupo] + holgura
                for unidad in candidatas(datos, plan, rit, trab, libro.exceso(trab),
                                         reg.protegidos[trab]):
                    if any(reg.cedidos_grupo[(grupo, f)] >= tope for f in unidad.dias):
                        continue
                    coste = _coste_fase2(datos, plan, libro, reg, trab, unidad, cache)
                    if mejor is None or coste < mejor[0]:
                        mejor = (coste, unidad)
                if mejor is not None:
                    if holgura:
                        topes[grupo] = tope             # se queda relajado: el grupo no cabía
                    break
            if mejor is None:
                print(f"  aviso  {trab} se queda en {libro.horas(trab):.0f} h "
                      f"({libro.exceso(trab):+.0f}): no le queda nada que ceder")
                break
            _, unidad = mejor
            for f in unidad.dias:
                libro.borra(trab, plan.pop((trab, f)))
                reg.cedidos_grupo[(grupo, f)] += 1
            reg.compromisos[trab].append((unidad.ventana[0], unidad.ventana[-1]))
            reg.cesiones.append(Cesion(
                fase=2, titular=trab, dias=list(unidad.dias), horas=unidad.horas,
                cubridor=None, desalojadas=0,
                motivo="ciclo entero" if rit.rigido else
                       ("bloque" if len(unidad.dias) > 1 else "día suelto")))


# --------------------------------------------------------------------------- #
#  Orquestación e informe
# --------------------------------------------------------------------------- #
def ceder(datos: Datos, plan: Plan, libro: LibroHoras,
          protegidos: dict[str, set[date]] | None = None,
          ritmos: dict[str, Ritmo] | None = None) -> Registro:
    """`protegidos` son días que no se pueden ceder aunque sobren horas — hoy, los fines de semana
    de cuota que el paso A2 le dio a los mixtos: no son exceso, son la equidad que justifica que el
    mixto salga de su línea.

    `ritmos`, si no se pasa, se mide aquí mismo (comportamiento de siempre) — pipeline.py ya lo
    calcula en este mismo punto (tras colocar_mixtos) y lo pasa, para no remedirlo tres veces."""
    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, plan)
    ritmo_mod.resumen(ritmos)
    reg = Registro()
    for w, dias in (protegidos or {}).items():
        reg.protegidos[w] |= dias
    fase1(datos, plan, libro, ritmos, reg)
    fase2(datos, plan, libro, ritmos, reg)
    _forzar_descanso_finde(datos, plan, libro, reg, ritmos)
    return reg


def escribir_csv(reg: Registro, ruta: Path) -> None:
    """El listado de cesiones: es lo que se revisa a mano para corregir el criterio con ejemplos
    reales, ya que la empresa no tiene una regla escrita que copiar."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as fichero:
        escritor = csv.writer(fichero)
        escritor.writerow(["fase", "titular", "desde", "hasta", "dias", "horas",
                           "cubridor", "plazas_desalojadas", "motivo"])
        for c in sorted(reg.cesiones, key=lambda c: (c.fase, c.titular, c.dias[0])):
            escritor.writerow([c.fase, c.titular, f"{c.dias[0]:%d/%m/%Y}", f"{c.dias[-1]:%d/%m/%Y}",
                               len(c.dias), f"{c.horas:.1f}", c.cubridor or "",
                               c.desalojadas, c.motivo])


def _forzar_descanso_finde(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro,
                           ritmos: dict[str, Ritmo]) -> None:
    """Tras fase1+fase2: si una semana de sábado+domingo trabajado que el pipeline SÍ tocó (le
    cedió al menos un día) sigue sin un par consecutivo libre, cede uno más para completarlo —
    aunque cueste una cesión de más de la que pedían solo las horas. Las semanas que nadie tocó se
    dejan como están, igual que el resto de reglas legales sobre un patrón heredado."""
    def lunes_de(f: date) -> date:
        return f - timedelta(days=f.weekday())

    tocadas = {(c.titular, lunes_de(f)) for c in reg.cesiones for f in c.dias}
    titulares = sorted({c.titular for c in reg.cesiones})
    for titular in titulares:
        if ritmo_mod.es_rigido(datos, ritmos, titular):
            continue
        lunes_de_titular = sorted({lunes_de(f) for (w, f) in plan if w == titular})
        for lunes in lunes_de_titular:
            if legal.descanso_finde_ok(datos, plan, titular, lunes):
                continue
            if (titular, lunes) not in tocadas:
                continue                                  # esqueleto puro: se tolera
            dias_semana = [lunes + timedelta(days=i) for i in range(5)]
            libres = [d for d in dias_semana if (titular, d) not in plan]
            trabajados = [d for d in dias_semana if (titular, d) in plan]
            adyacentes = [d for d in trabajados if any(abs((d - lb).days) == 1 for lb in libres)]
            mejor = None
            for candidato in (adyacentes or trabajados):
                libres_para_cubrir = _libres(datos, plan, libro, plan[(titular, candidato)],
                                             candidato, {titular})
                if libres_para_cubrir == 0:
                    continue
                if mejor is None or libres_para_cubrir > mejor[1]:
                    mejor = (candidato, libres_para_cubrir)
            if mejor is None:
                print(f"  aviso  {titular} semana del {lunes:%d/%m}: sábado+domingo sin par "
                      f"consecutivo y nadie puede cubrir el día que lo completaría")
                continue
            candidato, _ = mejor
            s = plan.pop((titular, candidato))
            libro.borra(titular, s)
            reg.cesiones.append(Cesion(fase=2, titular=titular, dias=[candidato],
                                       horas=datos.turnos[s].horas, cubridor=None,
                                       desalojadas=0, motivo="descanso de finde"))


def comprobar(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro) -> None:
    """El invariante del paso B: al terminar, nadie por encima de la jornada anual. Es justo lo
    que se le escapó al intento anterior, donde la cobertura salía alta porque media plantilla
    acababa muy por encima del objetivo."""
    f1 = [c for c in reg.cesiones if c.fase == 1]
    print(f"\nPASO B — {len(reg.cesiones)} cesiones "
          f"({len(f1)} traspasadas a un cubridor, {len(reg.cesiones) - len(f1)} que dejan hueco)")
    if f1:
        por_cubridor: dict[str, int] = defaultdict(int)
        for c in f1:
            por_cubridor[c.cubridor] += 1
        print("  traspasos por cubridor: "
              + ", ".join(f"{w}: {n}" for w, n in sorted(por_cubridor.items())))
    por_mes: dict[int, int] = defaultdict(int)
    for c in reg.cesiones:
        por_mes[c.dias[0].month] += 1
    print("  cesiones por mes: " + " ".join(f"{m:02d}:{por_mes.get(m, 0)}" for m in range(1, 13)))

    pasados = sorted((w for w in datos.trabajadores if libro.exceso(w) > EPS),
                     key=lambda w: -libro.exceso(w))
    if pasados:
        print(f"  *** {len(pasados)} POR ENCIMA DEL OBJETIVO: "
              + ", ".join(f"{w} {libro.horas(w):.0f}h ({libro.exceso(w):+.0f})"
                          for w in pasados[:8]) + " ***")
    else:
        print("  nadie por encima del objetivo anual")


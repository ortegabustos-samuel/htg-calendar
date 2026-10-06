"""
zonas/valladolid/libranzas.py — Paso B de Valladolid: las ausencias y el exceso de horas de los
trabajadores de patrón.

En Valladolid todas las líneas de patrón (noches y UVI) tienen cubridores designados, así que lo
que el titular deja lo hereda un cubridor con su ciclo entero, turnos y descansos:

  1. cubrir_vacaciones — el titular está de vacaciones: su cubridor hace su ciclo esos días
  2. ceder             — mientras el titular pase de su jornada, cede un BLOQUE (una racha de
                         trabajo y los descansos que la siguen) a un cubridor. Si con el bloque
                         entero se quedaría corto, solo las últimas jornadas de la racha que hagan
                         falta para no pasarse

El fichero tiene dos partes: la MECÁNICA (qué es un bloque, cómo se hereda), que probablemente
sirva a otras zonas, y los CRITERIOS de Valladolid (dónde ceder y a quién).
"""
from __future__ import annotations

from datetime import date, timedelta

from dominio import Datos, Plan, TipoDescanso, TipoTrabajador

SEPARACION_MINIMA = 15   # días de vida normal que se le deja a un cubridor entre dos coberturas:
                         # tantos como dura una ausencia. Sin esto el principal encadena
                         # quincenas y el suplente no entra nunca.


# --------------------------------------------------------------------------- #
#  Mecánica
# --------------------------------------------------------------------------- #
def trabaja(datos: Datos, trabajador_id: str, fecha: date) -> bool:
    """Si su rotación le marca un turno ese día (no un descanso ni nada)."""
    valor = datos.prescrito(trabajador_id, fecha)
    return valor is not None and not isinstance(valor, TipoDescanso)


def bloque_desde(datos: Datos, trabajador_id: str, fecha: date) -> list[date] | None:
    """Si su racha de trabajo EMPIEZA ese día: la racha y los días sin trabajo que la siguen.
    None si ese día no trabaja o va en medio de una racha."""
    if not trabaja(datos, trabajador_id, fecha) or trabaja(datos, trabajador_id, fecha - timedelta(days=1)):
        return None
    dias = []
    actual = fecha
    while actual <= datos.fin and trabaja(datos, trabajador_id, actual):
        dias.append(actual)
        actual += timedelta(days=1)
    while actual <= datos.fin and not trabaja(datos, trabajador_id, actual):
        dias.append(actual)
        actual += timedelta(days=1)
    return dias


def intactos(datos: Datos, plan: Plan, trabajador_id: str, dias: list[date]) -> bool:
    """Si esos días siguen tal como se los marca su rotación: está (no de vacaciones) y nadie los
    ha tocado. Para quien no tiene patrón, que estén libres.

    Se pregunta desde los dos lados: de un titular, si esos días siguen siendo suyos y los puede
    ceder; de un cubridor, si se le puede meter en otra plaza."""
    return all(datos.disponible(trabajador_id, fecha)
               and plan.get(trabajador_id, fecha) == datos.prescrito(trabajador_id, fecha)
               for fecha in dias)


def recortar(datos: Datos, titular: str, dias: list[date], exceso: int) -> list[date]:
    """Lo justo del bloque para quitarle `exceso` minutos sin quitarle de más.

    Si la racha entera le quitaría más de lo que le sobra, se queda con las ÚLTIMAS jornadas de
    la racha que hagan falta para no pasarse de su jornada: el titular acorta la racha y el
    cubridor hace ese final. Sin descansos: los del patrón compensan la racha, que sigue siendo
    del titular. Si hace falta la racha entera, el bloque entero, descansos incluidos."""
    trabajo = [fecha for fecha in dias if trabaja(datos, titular, fecha)]

    def minutos(fecha):
        return datos.turnos[datos.prescrito(titular, fecha)].minutos_computo

    if sum(minutos(fecha) for fecha in trabajo) <= exceso:
        return dias
    cedidos, suma = [], 0
    for fecha in reversed(trabajo):
        cedidos.append(fecha)
        suma += minutos(fecha)
        if suma >= exceso:
            break
    return sorted(cedidos)


def heredar(datos: Datos, plan: Plan, titular: str, cubridor: str, dias: list[date]) -> None:
    """El cubridor hace esos días lo que la rotación le marca al titular: turnos y descansos."""
    for fecha in dias:
        plan.quitar(cubridor, fecha)
        valor = datos.prescrito(titular, fecha)
        if valor is not None:
            plan.poner(cubridor, fecha, valor)


def cubridores_de(datos: Datos, titular: str) -> list[str]:
    """Los cubridores de las líneas de su patrón, en su orden de preferencia."""
    cubridores = []
    for turno_id in sorted(datos.patrones[datos.trabajadores[titular].patron].turnos):
        for cubridor in datos.turnos[turno_id].cubridores:
            if cubridor not in cubridores:
                cubridores.append(cubridor)
    return cubridores


# --------------------------------------------------------------------------- #
#  Criterios de Valladolid
# --------------------------------------------------------------------------- #
def comprometidos(datos: Datos, plan: Plan, cubridor: str) -> list[date]:
    """Los días del año en que el cubridor ya no está libre."""
    return [fecha for fecha in datos.lista_dias_calendario
            if not intactos(datos, plan, cubridor, [fecha])]


def distancia(dias: list[date], ocupados: list[date]) -> int:
    """A cuántos días queda el bloque de lo más cercano que ya tiene ocupado."""
    if not ocupados:
        return 10_000
    return min(abs((fecha - ocupado).days) for fecha in dias for ocupado in ocupados)


def elegir_cubridor(datos: Datos, plan: Plan, cubridores: list[str], dias: list[date]) -> str | None:
    """El primero que pueda todos esos días y no venga de cubrir algo cerca. Si todos vienen de
    cubrir algo cerca, el que lo tenga más lejos: antes amontonar que dejar la plaza sin cubrir."""
    libres = [c for c in cubridores if intactos(datos, plan, c, dias)]
    if not libres:
        return None
    lejos = {c: distancia(dias, comprometidos(datos, plan, c)) for c in libres}
    for cubridor in libres:
        if lejos[cubridor] > SEPARACION_MINIMA:
            return cubridor
    return max(libres, key=lambda c: lejos[c])


def pool_de(datos: Datos, base: str) -> list[str]:
    """Quien absorbe lo que deja un cubridor al irse a cubrir: fijos y correturnos de su base."""
    return [trabajador_id for trabajador_id, t in datos.trabajadores.items()
            if t.tipo in (TipoTrabajador.FIJO, TipoTrabajador.CORRETURNO) and t.base == base]


def demanda_del_pool(datos: Datos, base: str) -> dict[date, int]:
    """Personas por día que tiene que cubrir ese pool: las líneas de su base sin cubridores
    (esas solo las hacen sus designados) y fuera de los patrones (esas las pinta el esqueleto)."""
    de_patron = {turno_id for patron in datos.patrones.values() for turno_id in patron.turnos}
    lineas = [turno_id for turno_id, turno in datos.turnos.items()
              if turno.base == base and not turno.cubridores and turno_id not in de_patron]
    return {fecha: sum(datos.turnos[s].personas for s in lineas if datos.opera(s, fecha))
            for fecha in datos.lista_dias_calendario}


def holgura(datos: Datos, plan: Plan, fecha: date, pool: list[str], demanda: dict[date, int]) -> float:
    """Cuánto sitio le queda al pool ese día: lo que da la gente disponible y libre, menos lo que
    tiene que cubrir. Cada persona da de media jornada/(8*365) días de trabajo por día natural
    (unos 0,6): nadie trabaja los siete. Solo sirve para comparar días."""
    ritmo = datos.zona.convenio.horas_anuales / (8 * 365)
    libres = sum(1 for w in pool if datos.disponible(w, fecha) and plan.hueco(w, fecha))
    return libres * ritmo - demanda[fecha]


# --------------------------------------------------------------------------- #
#  Los dos procesos
# --------------------------------------------------------------------------- #
def cubrir_ausencia(datos: Datos, plan: Plan, titular: str, inicio: date, fin: date) -> None:
    """Mete al mejor cubridor en el puesto del titular esos días. Si ninguno puede todos, los
    reparte en tramos: cada uno sigue mientras pueda."""
    cubridores = cubridores_de(datos, titular)
    dias = [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]

    cubridor = elegir_cubridor(datos, plan, cubridores, dias)
    if cubridor is not None:
        tramos = [(cubridor, dias)]
    else:
        tramos = []
        actual, suyos = None, []
        for fecha in dias:
            if actual is not None and intactos(datos, plan, actual, [fecha]):
                suyos.append(fecha)
                continue
            if actual is not None:
                tramos.append((actual, suyos))
            actual = elegir_cubridor(datos, plan, cubridores, [fecha])
            suyos = [fecha] if actual is not None else []
        if actual is not None:
            tramos.append((actual, suyos))

    for cubridor, dias_tramo in tramos:
        heredar(datos, plan, titular, cubridor, dias_tramo)


def cubrir_vacaciones(datos: Datos, plan: Plan) -> None:
    """Las vacaciones de cada titular de patrón, en orden de fecha, las cubre su cubridor."""
    ausencias = sorted((inicio, fin, trabajador_id)
                       for trabajador_id, t in datos.trabajadores.items()
                       if t.tipo == TipoTrabajador.PATRON and cubridores_de(datos, trabajador_id)
                       for inicio, fin in t.vacaciones)
    for inicio, fin, titular in ausencias:
        cubrir_ausencia(datos, plan, titular, inicio, fin)


def ceder_un_bloque(datos: Datos, plan: Plan, titular: str, pool: list[str],
                    demanda: dict[date, int]) -> bool:
    """Cede UN bloque del titular a un cubridor. Devuelve si ha podido.

    El bloque se elige primero por si cae donde el pool tiene sitio para absorber al cubridor
    que se va, y a igualdad por lo lejos que quede de lo que el cubridor ya tiene encima."""
    cubridores = cubridores_de(datos, titular)
    mejor = None
    for fecha in datos.lista_dias_calendario:
        dias = bloque_desde(datos, titular, fecha)
        if dias is None or not intactos(datos, plan, titular, dias):
            continue
        cubridor = elegir_cubridor(datos, plan, cubridores, dias)
        if cubridor is None:
            continue
        sitio = sum(holgura(datos, plan, f, pool, demanda) for f in dias) / len(dias)
        puntos = (sitio >= 0, distancia(dias, comprometidos(datos, plan, cubridor)))
        if mejor is None or puntos > mejor[0]:
            mejor = (puntos, dias, cubridor)
    if mejor is None:
        return False

    _, dias, cubridor = mejor
    dias = recortar(datos, titular, dias, plan.minutos(titular) - datos.minutos_objetivo(titular))
    for fecha in dias:
        if not isinstance(plan.get(titular, fecha), TipoDescanso):
            plan.quitar(titular, fecha)        # sus descansos se quedan: no son libranza
    heredar(datos, plan, titular, cubridor, dias)
    return True


def ceder(datos: Datos, plan: Plan) -> None:
    """Cada titular de patrón cede bloques a sus cubridores mientras pase de su jornada."""
    pools, demandas = {}, {}
    for titular, t in datos.trabajadores.items():
        if t.tipo != TipoTrabajador.PATRON or not cubridores_de(datos, titular):
            continue
        if t.base not in pools:
            pools[t.base] = pool_de(datos, t.base)
            demandas[t.base] = demanda_del_pool(datos, t.base)
        while plan.minutos(titular) > datos.minutos_objetivo(titular):
            if not ceder_un_bloque(datos, plan, titular, pools[t.base], demandas[t.base]):
                break

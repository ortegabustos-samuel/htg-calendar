"""PASO 2 — cubre las líneas críticas, con el calendario aún casi vacío.

Son las que no tienen grados de libertad: cada línea crítica tiene un puñado de cubridores
designados (2 a 4 en este dataset) y ese puñado se agota rápido en cuanto la regla de adopción de
plaza entra en juego. Decidirlas antes que nada es lo único que evita llegar al final y descubrir
que la única persona que podía hacerlas ya está ocupada — que es de donde salió toda la
infactibilidad de la rama base.

Regla dura pactada: LA COBERTURA CRÍTICA GANA AL PATRÓN PROPIO DEL CUBRIDOR. Si tu línea crítica se
queda sin nadie, dejas tu patrón. En la rama base esto era un precio que había que calibrar entre
dos constantes; aquí es una prelación explícita que se dice en una frase.

Dos reglas que gobiernan CÓMO se cubre (ronda de corrección 1, dictadas por la empresa):

  · Si al titular le falta la fila ENTERA esa semana, quien cubre ADOPTA la plaza: hace todos los
    días que le faltan al titular y HEREDA sus días LIBRE (los cede, `Plan.ceder`). Nada más esa
    semana — ni su propio patrón, ni otra cobertura suelta. El veto semanal es de la ADOPCIÓN, no
    de "tocar la línea": confundir ambas cosas fue el bug de la primera versión (72 de 74 huecos
    eran artefacto de vetar toda la semana por cubrir un solo día).
  · Si falta solo PARTE de la fila, o un día suelto, es un turno normal: se cubre y el cubridor
    sigue su propia rotación el resto de la semana. Sin veto ninguno — puede cubrir varios días,
    de la misma línea o de otra, la misma semana.

Esta distinción es exactamente `AusenciaCritica.entera` en `modelo.py` (línea ~1512): se porta
aquí adaptada al `Plan` determinista (la fuente de "no puede" es `datos.disponible` + `plan.cedido`,
no el set de cesiones por quincena de la rama CP-SAT) en vez de reinventarla.

Las líneas SIN titular de patrón (H, en este dataset: la cubren dos FIJOS) no tienen fila que
adoptar — un fijo no tiene días LIBRE de rotación que heredar, solo trabaja o está de vacaciones —
así que su hueco se trata como demanda suelta, día a día, contando cuántas plazas faltan de verdad
(línea con `dem` > 1 incluida: H pide 2 personas por día, y el hueco real solo aparece si sobran
titulares Y cubridores)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from calendario import fila_patron, semana, turno_prescrito
from cargar_datos import DIAS, LIBRE, Datos
from legal import Legal
from plan import Plan

PASO = "criticos"


def principales(datos: Datos) -> dict[str, list[str]]:
    """{línea → cubridores designados, del principal al último suplente}.

    El gestor designa un principal (v=1) y suplentes (v≥2). Los cubridores pueden estar CRUZADOS
    —cada uno principal de una línea y suplente de la otra—, así que el orden es por LÍNEA, nunca
    por persona."""
    orden: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (w, s), cap in sorted(datos.capacidades.items()):
        if cap.v >= 1:
            orden[s].append((cap.v, w))
    return {s: [w for _, w in sorted(pares)] for s, pares in sorted(orden.items())}


def lineas_criticas(datos: Datos) -> list[str]:
    """Líneas de prioridad ≥ 2, de más crítica a menos. Desempate por id para determinismo."""
    return sorted((s for s, t in datos.turnos.items() if t.prioridad >= 2),
                  key=lambda s: (-datos.turnos[s].prioridad, s))


def _hace_otra_critica(datos: Datos, plan: Plan, w: str, f: date, prio: int) -> bool:
    """Escape: ese día ya hace otra crítica de prioridad MAYOR O IGUAL. No se le puede pedir más."""
    s = plan.turno_de(w, f)
    return s is not None and datos.turnos[s].prioridad >= prio


def _semana_completa(sem: tuple[int, int]) -> list[date]:
    """Los 7 días naturales (lunes a domingo) de una semana ISO."""
    lunes = date.fromisocalendar(sem[0], sem[1], 1)
    return [lunes + timedelta(days=i) for i in range(7)]


# --------------------------------------------------------------------------- #
#  Ausencias de titular en líneas de PATRÓN — de dónde sale la fila a adoptar
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AusenciaCritica:
    """Días de una línea crítica que su titular (de PATRÓN) no puede hacer en una semana ISO.

    `prescritos` son los días que la fila de la rotación le asigna esa semana; `faltan`, el
    subconjunto que no puede hacer (vacaciones o cesión del paso 1); `libres`, los días de la
    semana que su fila deja LIBRE — el descanso que viene con la plaza.

    La distinción que gobierna las dos reglas: si faltan TODOS los días prescritos, quien cubra
    ADOPTA la plaza y hereda `libres`; si falta solo parte, son turnos normales. Réplica de
    `modelo.AusenciaCritica`, adaptada al `Plan` determinista."""
    linea: str
    titular: str
    semana: tuple[int, int]
    prescritos: frozenset[date]
    faltan: frozenset[date]
    libres: frozenset[date]

    @property
    def entera(self) -> bool:
        return bool(self.faltan) and self.faltan == self.prescritos


def ausencias_criticas(datos: Datos, plan: Plan) -> list[AusenciaCritica]:
    """Recorre el año y agrupa, por titular de PATRÓN y semana ISO, qué días de su línea crítica se
    quedan sin él. Es dato conocido tras el paso 1: las vacaciones vienen en `trabajadores.csv` y
    las cesiones por exceso ya están en `plan` (`libranzas.repartir`).

    Solo titulares de PATRÓN: un FIJO (como los de `H`) no tiene fila que adoptar —su plaza es
    L-V fija, sin días LIBRE de rotación que heredar—, así que su ausencia no genera
    `AusenciaCritica`; la cubre `_cubrir_demanda` como plaza suelta, contando la demanda real."""
    criticas = {s for s, t in datos.turnos.items() if t.prioridad >= 2}
    if not criticas:
        return []

    acum: dict[tuple[str, tuple[int, int], str], dict[str, set]] = {}
    for w, t in datos.trabajadores.items():
        if t.tipo != "patron" or not t.patron:
            continue
        for f in datos.fechas:
            s = turno_prescrito(datos, w, f)
            if s not in criticas:
                continue
            reg = acum.setdefault((w, semana(f), s),
                                  {"prescritos": set(), "faltan": set(), "libres": set()})
            reg["prescritos"].add(f)
            if not datos.disponible(w, f) or plan.cedido(w, f):
                reg["faltan"].add(f)

    # Los LIBRE de la fila: días de esa semana ISO en que la rotación no le asigna nada.
    for (w, _sem, _s), reg in acum.items():
        filas = datos.patrones[datos.trabajadores[w].patron]
        alguno = min(reg["prescritos"])
        lunes = alguno - timedelta(days=alguno.weekday())
        for i in range(7):
            f = lunes + timedelta(days=i)
            if f < datos.inicio or f > datos.fin:
                continue
            fila = fila_patron(datos, w, f)
            if fila is not None and filas[fila][DIAS[f.weekday()]] == LIBRE:
                reg["libres"].add(f)

    return [AusenciaCritica(linea=s, titular=w, semana=sem,
                            prescritos=frozenset(reg["prescritos"]),
                            faltan=frozenset(reg["faltan"]),
                            libres=frozenset(reg["libres"]))
            for (w, sem, s), reg in sorted(acum.items()) if reg["faltan"]]


# --------------------------------------------------------------------------- #
#  Cobertura
# --------------------------------------------------------------------------- #

def _adoptar(datos: Datos, plan: Plan, ley: Legal, cubridores: list[str], a: AusenciaCritica,
             con_carga: set[tuple[str, tuple[int, int]]],
             adoptada: set[tuple[str, tuple[int, int]]]) -> None:
    """Fila ENTERA: el primer cubridor designado que pueda con TODOS los días la adopta completa,
    hereda los días LIBRE de la fila (los cede) y no hace nada más esa semana — ni su propio
    patrón, ni otra crítica, ni una suelta. Es DURA, como en `modelo._adopcion_plaza`."""
    dias_faltan = sorted(a.faltan)
    prio = datos.turnos[a.linea].prioridad
    descartados: list[str] = []
    for w in cubridores:
        if (w, a.semana) in con_carga:
            descartados.append(f"{w}: ya tiene algo esa semana")
            continue
        chequeos = [ley.puede(plan, w, f, a.linea) for f in dias_faltan]
        if not all(ok for ok, _ in chequeos):
            motivo = next(m for ok, m in chequeos if not ok)
            descartados.append(f"{w}: {motivo}")
            continue

        for f in dias_faltan:
            plan.asignar(w, f, a.linea, PASO,
                         f"adopta plaza {a.linea} (prio {prio}), semana {a.semana[1]}",
                         tuple(descartados))
        # Hereda los descansos de la fila y, por si la fila tuviera algún día que no sea ni
        # prescrito ni LIBRE, cede también el resto de la semana: adoptar es "nada más esa semana".
        for f in _semana_completa(a.semana):
            if datos.inicio <= f <= datos.fin and not plan.ocupado(w, f):
                plan.ceder(w, f, PASO,
                           f"hereda descanso de la plaza {a.linea}, semana {a.semana[1]}")
        con_carga.add((w, a.semana))
        adoptada.add((w, a.semana))
        return

    motivo = ("ningun cubridor designado puede adoptar la fila entera: " + "; ".join(descartados)
              if descartados else "sin cubridores designados")
    for f in dias_faltan:
        plan.hueco(f, a.linea, motivo)


def _cubrir_sueltos(datos: Datos, plan: Plan, ley: Legal, cubridores: list[str],
                     a: AusenciaCritica, con_carga: set[tuple[str, tuple[int, int]]],
                     adoptada: set[tuple[str, tuple[int, int]]]) -> None:
    """Falta solo PARTE de la fila (o un día suelto): turno normal, día a día. Sin veto semanal —el
    cubridor sigue su propia rotación el resto de la semana—; el único bloqueo es una semana que
    YA adoptó una fila entera, porque esa sí es excluyente."""
    prio = datos.turnos[a.linea].prioridad
    for f in sorted(a.faltan):
        if plan.cubierto(f, a.linea) >= datos.turnos[a.linea].dem:
            continue
        descartados: list[str] = []
        for w in cubridores:
            if (w, a.semana) in adoptada:
                descartados.append(f"{w}: adopto una fila entera esta semana")
                continue
            if _hace_otra_critica(datos, plan, w, f, prio):
                descartados.append(f"{w}: ya hace {plan.turno_de(w, f)}, igual o mas critica")
                continue
            ok, motivo = ley.puede(plan, w, f, a.linea)
            if not ok:
                descartados.append(f"{w}: {motivo}")
                continue
            plan.asignar(w, f, a.linea, PASO, f"cobertura critica suelta (prio {prio})",
                         tuple(descartados))
            con_carga.add((w, a.semana))
            break
        else:
            motivo = ("ningun cubridor designado puede: " + "; ".join(descartados)
                      if descartados else "sin cubridores designados")
            plan.hueco(f, a.linea, motivo)


def _cubrir_demanda(datos: Datos, plan: Plan, ley: Legal, orden: dict[str, list[str]], linea: str,
                     adoptada: set[tuple[str, tuple[int, int]]]) -> None:
    """Líneas sin fila que adoptar (titular no es de patrón, o `dem` > 1: H, en este dataset).

    Cada plaza es independiente: no hay fila ni descansos que heredar. Se cuenta cuánto falta DE
    VERDAD —`dem` menos los titulares presentes menos lo ya cubierto— y se asignan cubridores
    designados, por orden, hasta cerrar esa demanda o agotar la lista. Antes de esta corrección el
    escape solo miraba si había ALGÚN titular, así que un día con 1 titular de 2 (`H`, `dem`=2) se
    daba por bueno sin llamar a los cubridores que sí existían."""
    cubridores = orden.get(linea, [])
    if not cubridores:
        return
    prio = datos.turnos[linea].prioridad
    dem = datos.turnos[linea].dem
    for f in datos.fechas:
        if not datos.opera(linea, f):
            continue
        titulares = [w for w in sorted(datos.trabajadores)
                     if turno_prescrito(datos, w, f) == linea
                     and datos.disponible(w, f) and not plan.cedido(w, f)]
        faltan = dem - len(titulares) - plan.cubierto(f, linea)
        asignados_hoy: set[str] = set()
        while faltan > 0:
            descartados: list[str] = []
            elegido = None
            for w in cubridores:
                if w in asignados_hoy:
                    continue
                if (w, semana(f)) in adoptada:
                    descartados.append(f"{w}: adopto una fila entera esta semana")
                    continue
                if _hace_otra_critica(datos, plan, w, f, prio):
                    descartados.append(f"{w}: ya hace {plan.turno_de(w, f)}, igual o mas critica")
                    continue
                ok, motivo = ley.puede(plan, w, f, linea)
                if not ok:
                    descartados.append(f"{w}: {motivo}")
                    continue
                elegido = w
                break
            if elegido is None:
                motivo = ("ningun cubridor designado puede: " + "; ".join(descartados)
                          if descartados else "sin cubridores designados")
                plan.hueco(f, linea, motivo)
                faltan -= 1
                continue
            plan.asignar(elegido, f, linea, PASO, f"cobertura critica (prio {prio}), plaza extra",
                         tuple(descartados))
            asignados_hoy.add(elegido)
            faltan -= 1


def cubrir(datos: Datos, plan: Plan, ley: Legal) -> None:
    """Asigna cubridor a cada plaza de línea crítica que su titular no puede hacer."""
    orden = principales(datos)

    # (cubridor, semana ISO): con_carga = tiene ALGO asignado esta semana por este paso (adopción o
    # suelta); adoptada = adoptó una fila ENTERA. Solo la segunda veta el resto de la semana — el
    # bug de la primera versión vetaba con la primera, y confundía "tocar la línea" con "adoptar
    # la plaza": 72 de los 74 huecos que dejaba eran artefacto de ese veto de más.
    con_carga: set[tuple[str, tuple[int, int]]] = set()
    adoptada: set[tuple[str, tuple[int, int]]] = set()

    # Las adopciones (fila entera) primero, y entre ellas la más crítica primero: reclaman la
    # semana completa, así que deciden antes de que una cobertura suelta ocupe a quien luego
    # tendría que adoptar. El resto de campos son solo para el determinismo del desempate.
    ausencias = sorted(
        ausencias_criticas(datos, plan),
        key=lambda a: (not a.entera, -datos.turnos[a.linea].prioridad, a.linea, a.titular, a.semana),
    )
    for a in ausencias:
        cubridores = orden.get(a.linea, [])
        if not cubridores:
            for f in sorted(a.faltan):
                plan.hueco(f, a.linea, "sin cubridores designados")
            continue
        if a.entera:
            _adoptar(datos, plan, ley, cubridores, a, con_carga, adoptada)
        else:
            _cubrir_sueltos(datos, plan, ley, cubridores, a, con_carga, adoptada)

    # Líneas sin titular de patrón (H) o con dem > 1: nada que adoptar, demanda día a día. Corre
    # DESPUÉS a propósito: para las líneas de patrón con dem=1 ya está todo cubierto arriba y esto
    # no encuentra nada pendiente (cubierto=dem), así que no hay huecos duplicados.
    lineas_patron = {s for filas in datos.patrones.values() for fila in filas
                      for s in fila.values() if s and s != LIBRE and s in datos.turnos}
    for linea in lineas_criticas(datos):
        if linea in lineas_patron and datos.turnos[linea].dem <= 1:
            continue
        _cubrir_demanda(datos, plan, ley, orden, linea, adoptada)

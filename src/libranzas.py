from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import base
from cargar_datos import Datos
from horas import EPS, LibroHoras

Plan = dict[tuple[str, date], str]
RADIO_SEPARACION = 7   # días de margen alrededor de cada cesión de un titular antes de dejarle
                       # ceder otra: sin esto, sus propias cesiones sueltas se apilan en la misma
                       # semana porque la disponibilidad de un día a otro apenas cambia y nada
                       # penaliza coger el día de al lado (el hueco solo se mira día a día).


def _designados(datos: Datos) -> dict[str, list[str]]:
    """Turno -> cubridores declarados en capacidades.csv (`v>=1`), por orden de preferencia
    (v=1 principal, 2, 3... suplentes)."""
    por_turno: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (trab, turno), cap in datos.capacidades.items():
        if cap.v >= 1:
            por_turno[turno].append((cap.v, trab))
    return {turno: [w for _, w in sorted(gente)] for turno, gente in por_turno.items()}


def _titulares_de(datos: Datos, turno_id: str) -> list[str]:
    """Quién hace este turno por capacidad normal (`v==0`) — puede ser más de uno."""
    return sorted(w for (w, s), cap in datos.capacidades.items()
                  if s == turno_id and cap.v == 0)


def _elegibles_por_turno(datos: Datos, designados: dict[str, list[str]]) -> dict[str, set[str]]:
    """Quién podría cubrir cada turno si se queda libre: sus cubridores designados si los
    tiene; si no, cualquiera con capacidad normal declarada para él — el pool real que
    correturno usaría."""
    turnos = {s for (_, s) in datos.capacidades}
    elegibles: dict[str, set[str]] = {}
    for turno in turnos:
        cubridores = designados.get(turno)
        elegibles[turno] = set(cubridores) if cubridores else set(_titulares_de(datos, turno))
    return elegibles


def _libre_para_cubrir(datos: Datos, plan: Plan, trab: str, inicio: date, fin: date) -> bool:
    """Libre de verdad: sin vacaciones y sin que ya se le haya tocado el día por otra cesión ya
    asignada (si no, un mismo cubridor podría acabar reservado dos veces el mismo día)."""
    dias = [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]
    return all(datos.disponible(trab, f) and plan.get((trab, f)) == base.prescrito(datos, trab, f)
               for f in dias)


def _ocupado(plan: Plan) -> set[tuple[str, date]]:
    """(turno, fecha) que tienen a alguien asignado ahora mismo. Cada plaza es de una sola
    persona, así que basta con saber si está ocupada, no cuántos hay."""
    return {(turno, f) for (_, f), turno in plan.items()}


def _asignar(plan: Plan, libro: LibroHoras, ocupado: set[tuple[str, date]],
             trab: str, fecha: date, turno: str) -> None:
    plan[(trab, fecha)] = turno
    libro.apunta(trab, turno)
    ocupado.add((turno, fecha))


def _soltar(plan: Plan, libro: LibroHoras, ocupado: set[tuple[str, date]],
            trab: str, fecha: date) -> None:
    """Quita lo que `trab` tuviera asignado ese día, si tenía algo, y lo refleja en `ocupado`:
    si nadie vuelve a ocupar esa plaza, la próxima búsqueda ya la verá como un hueco."""
    turno = plan.pop((trab, fecha), None)
    if turno is not None:
        libro.borra(trab, turno)
        ocupado.discard((turno, fecha))


def _cubrir_vacaciones(datos: Datos, plan: Plan, libro: LibroHoras, ocupado: set[tuple[str, date]],
                       titular: str, cubridores: list[str]) -> None:
    """Durante las vacaciones del titular, hereda lo que prescriba su rotación o línea cada día,
    trabajo Y descanso. Si un único cubridor no está libre la quincena entera, se reparte en
    tramos contiguos —cada uno con un solo cubridor, para no inventarle a nadie una secuencia
    mezclada— en vez de desestimarla entera: solo se queda sin cubrir el día que de verdad no
    tenga a nadie libre."""
    for inicio, fin in datos.trabajadores[titular].vacaciones:
        dias = [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)
                if datos.inicio <= inicio + timedelta(days=i) <= datos.fin]
        if not dias:
            continue

        cubridor_total = next((c for c in cubridores
                              if _libre_para_cubrir(datos, plan, c, dias[0], dias[-1])), None)
        sin_cubrir: list[date] = []
        if cubridor_total is not None:
            tramos = [(cubridor_total, dias)]
        else:
            tramos = []
            actual: str | None = None
            for f in dias:
                if actual is not None and _libre_para_cubrir(datos, plan, actual, f, f):
                    tramos[-1][1].append(f)
                    continue
                actual = next((c for c in cubridores if _libre_para_cubrir(datos, plan, c, f, f)),
                             None)
                if actual is None:
                    sin_cubrir.append(f)
                else:
                    tramos.append((actual, [f]))

        for cubridor, tramo_dias in tramos:
            for f in tramo_dias:
                turno_titular = base.prescrito(datos, titular, f)
                _soltar(plan, libro, ocupado, cubridor, f)
                if turno_titular is not None:
                    _asignar(plan, libro, ocupado, cubridor, f, turno_titular)

        if sin_cubrir:
            print(f"  aviso  quincena del {inicio:%d/%m} al {fin:%d/%m}: {titular} de vacaciones, "
                  f"sin cubridor libre {len(sin_cubrir)}/{len(dias)} días")


def _mejor_periodo(datos: Datos, plan: Plan, titular: str, pool: set[str], cubridores: list[str],
                   tam: int, alinear_lunes: bool, usados: set[date],
                   turnos_del_dia: dict[date, list[str]], ocupado: set[tuple[str, date]],
                   elegibles_por_turno: dict[str, set[str]]) -> tuple[date, date, str] | None:
    """(inicio, fin, cubridor) del período con más margen para asumir la cesión: gente de `pool`
    (quien podría cubrir esto) que está libre en la ventana, menos los huecos que ya haya
    abiertos ahí en turnos que comparten ese mismo pool de cobertura — para que cesiones
    independientes no coincidan en el mismo momento salvo que sobre gente capaz para todas."""
    candidatos = []
    for inicio in datos.lista_dias_calendario:
        if alinear_lunes and inicio.weekday() != 0:
            continue
        fin = inicio + timedelta(days=tam - 1)
        if fin > datos.fin:
            continue
        ventana = [inicio + timedelta(days=i) for i in range(tam)]
        if any(d in usados for d in ventana):
            continue                                    # día ya cedido antes a este titular
        if not any(base.prescrito(datos, titular, d) is not None for d in ventana):
            continue                                    # nada real que ceder aquí
        cubridor = next((c for c in cubridores if _libre_para_cubrir(datos, plan, c, inicio, fin)),
                        None)
        if cubridor is None:
            continue                                    # ningún cubridor libre la ventana entera
        disponibles = sum(1 for w in pool if all(datos.disponible(w, d) for d in ventana))
        huecos = sum(1 for f in ventana for turno in turnos_del_dia.get(f, [])
                    if (turno, f) not in ocupado and elegibles_por_turno.get(turno, set()) & pool)
        candidatos.append((disponibles - huecos, inicio, fin, cubridor))
    if not candidatos:
        return None
    _, inicio, fin, cubridor = max(candidatos)
    return inicio, fin, cubridor


def _mejor_periodo_libre(datos: Datos, titular: str, pool: set[str], tam: int, alinear_lunes: bool,
                         usados: set[date], turnos_del_dia: dict[date, list[str]],
                         ocupado: set[tuple[str, date]],
                         elegibles_por_turno: dict[str, set[str]]) -> tuple[date, date] | None:
    """Como `_mejor_periodo`, pero sin cubridor propio a quien traspasar: solo hace falta que
    exista margen real (gente de `pool` libre, con hueco de sobra) para dejar la plaza vacía —
    la cubrirá correturno más adelante en el paso D."""
    candidatos = []
    for inicio in datos.lista_dias_calendario:
        if alinear_lunes and inicio.weekday() != 0:
            continue
        fin = inicio + timedelta(days=tam - 1)
        if fin > datos.fin:
            continue
        ventana = [inicio + timedelta(days=i) for i in range(tam)]
        if any(d in usados for d in ventana):
            continue                                    # día ya cedido antes a este titular
        if not any(base.prescrito(datos, titular, d) is not None for d in ventana):
            continue                                    # nada real que ceder aquí
        disponibles = sum(1 for w in pool if all(datos.disponible(w, d) for d in ventana))
        if disponibles == 0:
            continue                                    # nadie que pudiera llegar a cubrirlo
        huecos = sum(1 for f in ventana for turno in turnos_del_dia.get(f, [])
                    if (turno, f) not in ocupado and elegibles_por_turno.get(turno, set()) & pool)
        candidatos.append((disponibles - huecos, inicio, fin))
    if not candidatos:
        return None
    _, inicio, fin = max(candidatos)
    return inicio, fin


def _cubrir_exceso_pool(datos: Datos, plan: Plan, libro: LibroHoras, ocupado: set[tuple[str, date]],
                        turnos_del_dia: dict[date, list[str]], elegibles_por_turno: dict[str, set[str]],
                        pool: set[str], titular: str) -> None:
    """Como `_cubrir_exceso`, pero para titulares sin cubridor propio: la plaza se deja vacía en
    vez de traspasarla a alguien ahora, para no adelantarle al paso D una decisión que es suya."""
    t = datos.trabajadores[titular]
    grupo = t.patron if t.tipo == "patron" and t.patron else t.tipo
    rigido = grupo in datos.config.grupos_rigidos
    tam = len(datos.patrones[t.patron]) * 7 if rigido else 1
    usados: set[date] = set()
    while libro.exceso(titular) > EPS:
        periodo = _mejor_periodo_libre(datos, titular, pool, tam, rigido, usados,
                                       turnos_del_dia, ocupado, elegibles_por_turno)
        if periodo is None:
            print(f"  aviso  {titular} se queda en {libro.horas(titular):.0f} h "
                  f"({libro.exceso(titular):+.0f}): no queda período con margen para dejarlo vacío")
            break
        inicio, fin = periodo
        for i in range((fin - inicio).days + 1):
            f = inicio + timedelta(days=i)
            _soltar(plan, libro, ocupado, titular, f)
        for i in range(-RADIO_SEPARACION, (fin - inicio).days + 1 + RADIO_SEPARACION):
            usados.add(inicio + timedelta(days=i))


def _cubrir_exceso(datos: Datos, plan: Plan, libro: LibroHoras, ocupado: set[tuple[str, date]],
                   turnos_del_dia: dict[date, list[str]], elegibles_por_turno: dict[str, set[str]],
                   pool: set[str], titular: str, cubridores: list[str]) -> None:
    """Cesión electiva: día suelto por defecto. Si el grupo del titular es rígido
    (`config.grupos_rigidos`), se cede el ciclo completo del patrón (tantas semanas como filas
    tenga, alineado a lunes) tal cual lo prescribe, trabajo y descanso."""
    t = datos.trabajadores[titular]
    grupo = t.patron if t.tipo == "patron" and t.patron else t.tipo
    rigido = grupo in datos.config.grupos_rigidos
    tam = len(datos.patrones[t.patron]) * 7 if rigido else 1
    usados: set[date] = set()
    while libro.exceso(titular) > EPS:
        periodo = _mejor_periodo(datos, plan, titular, pool, cubridores, tam, rigido, usados,
                                 turnos_del_dia, ocupado, elegibles_por_turno)
        if periodo is None:
            print(f"  aviso  {titular} se queda en {libro.horas(titular):.0f} h "
                  f"({libro.exceso(titular):+.0f}): no queda período con cubridor libre")
            break
        inicio, fin, cubridor = periodo
        for i in range(tam):
            f = inicio + timedelta(days=i)
            turno_titular = base.prescrito(datos, titular, f)
            _soltar(plan, libro, ocupado, titular, f)
            _soltar(plan, libro, ocupado, cubridor, f)
            if turno_titular is not None:
                _asignar(plan, libro, ocupado, cubridor, f, turno_titular)
        for i in range(-RADIO_SEPARACION, tam + RADIO_SEPARACION):
            usados.add(inicio + timedelta(days=i))


def ceder(datos: Datos, plan: Plan, libro: LibroHoras) -> None:
    """Paso B: cede días de trabajo hasta que nadie quede por encima de su jornada anual.
    Modifica `plan` y `libro` in place."""
    designados = _designados(datos)
    elegibles_por_turno = _elegibles_por_turno(datos, designados)
    ocupado = _ocupado(plan)
    turnos_del_dia: dict[date, list[str]] = defaultdict(list)
    for turno, f in ocupado:
        turnos_del_dia[f].append(turno)

    tocados: set[str] = set()
    for turno_id, cubridores in designados.items():
        for titular in _titulares_de(datos, turno_id):
            tocados.add(titular)
            _cubrir_vacaciones(datos, plan, libro, ocupado, titular, cubridores)
            _cubrir_exceso(datos, plan, libro, ocupado, turnos_del_dia, elegibles_por_turno,
                          elegibles_por_turno.get(turno_id, set(cubridores)), titular, cubridores)

    # El resto de la plantilla no tiene un cubridor propio: se deja la plaza vacía cuando hay
    # margen en el pool de correturno, y la cubre paso D — aquí no se le asigna nadie todavía.
    pool_correturno = {w for w, t in datos.trabajadores.items() if t.tipo == "correturno"}
    for titular in sorted(datos.trabajadores):
        if titular in tocados or datos.trabajadores[titular].tipo == "correturno":
            continue
        _cubrir_exceso_pool(datos, plan, libro, ocupado, turnos_del_dia, elegibles_por_turno,
                            pool_correturno, titular)

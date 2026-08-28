"""
Las restricciones básicas del convenio, definidas UNA sola vez.

Las consumen los dos lados del pipeline: las reglas, como test ("¿puedo darle este turno a esta
persona este día?"), y el CP-SAT del paso D, como restricción del modelo. De momento solo existe
la primera forma — la segunda se añadirá sobre estas mismas definiciones, no sobre una copia.

No es el catálogo completo y eso es deliberado: solo las tres que se decidieron
básicas, más las dos estructurales que salen gratis.

  C4  descanso mínimo entre turnos de días consecutivos    (config.descanso_minimo, 12 h)
  C5  máx. días trabajados por SEMANA ISO                  (config.dias_max_semana, 6)
  C6  máx. horas trabajadas por SEMANA ISO                 (config.horas_max_semana, 48)

También vive aquí `domingo_ok` — nunca domingo suelto sin el sábado de ese fin de semana. No es
del convenio (no lleva número de artículo, es una regla de reparto), pero la consumen los mismos
sitios que C4/C5/C6, así que se define en el mismo lugar en vez de en uno propio.

También vive aquí `descanso_finde_ok` — si sábado y domingo se trabajan la misma semana, exige un
par de días consecutivos libres entre semana. Tampoco es del convenio, y a diferencia de
domingo_ok no se pliega en permite(): depende de la semana completa, no de un día contra el
anterior, así que se evalúa una vez decidida la semana (ver equidad.py, libranzas.py).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

import ritmo as ritmo_mod
from cargar_datos import Datos, DIAS_LV

Plan = dict[tuple[str, date], str]

def descanso_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str,
                exento_localizado: bool = False) -> bool:
    """C4 — entre el fin de un turno y el inicio del siguiente median al menos
    `descanso_minimo` horas.

    `exento_localizado` levanta la regla cuando uno de los dos es una guardia de localización: no
    es presencia, así que ni exige descanso después ni lo consume antes. Va en FALSO por defecto a
    propósito — el descanso debe respetarse salvo que sea la única forma de cubrir una demanda que
    de otro modo quedaría vacía, y esa excepción se paga en el objetivo del paso D, no aquí.
    """
    minimo = timedelta(hours=datos.config.descanso_minimo)
    exento = exento_localizado and datos.localizado(turno_id)
    inicio, fin = datos.intervalo(turno_id, fecha)

    ayer = fecha - timedelta(days=1)
    previo = plan.get((trabajador_id, ayer))
    if (previo is not None and not (exento or (exento_localizado and datos.localizado(previo)))
            and inicio - datos.intervalo(previo, ayer)[1] < minimo):
        return False

    manana = fecha + timedelta(days=1)
    posterior = plan.get((trabajador_id, manana))
    if (posterior is not None and not (exento or (exento_localizado and datos.localizado(posterior)))
            and datos.intervalo(posterior, manana)[0] - fin < minimo):
        return False
    return True


def dias_semana_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date) -> bool:
    """C5 — como mucho `dias_max_semana` días trabajados en la semana ISO en que cae `fecha`."""
    lunes = fecha - timedelta(days=fecha.weekday())
    trabajados = sum(1 for i in range(7) if (trabajador_id, lunes + timedelta(days=i)) in plan)
    return trabajados + 1 <= datos.config.dias_max_semana


def horas_semana_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno: str) -> bool:
    """C6 — como mucho `horas_max_semana` horas trabajadas en la semana ISO en que cae `fecha`."""
    lunes = fecha - timedelta(days=fecha.weekday())
    horas = sum(datos.turnos[plan[(trabajador_id, lunes + timedelta(days=i))]].horas
                for i in range(7) if (trabajador_id, lunes + timedelta(days=i)) in plan)
    return horas + datos.turnos[turno].horas <= datos.config.horas_max_semana


def domingo_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """El domingo solo se trabaja si también se trabaja el sábado de ese mismo fin de semana.
    No es del convenio"""
    if datos.tipo_dia(fecha, datos.turnos[turno_id].municipio) != "DOM":
        return True
    return (trabajador_id, fecha - timedelta(days=1)) in plan


def descanso_finde_ok(datos: Datos, plan: Plan, trabajador_id: str, lunes: date) -> bool:
    """Si esa semana ISO (`lunes`..`lunes+6`) se trabajan sábado Y domingo, exige un par de días
    consecutivos libres entre semana: el que fije config.dias_descanso_finde si está fijado, o
    cualquier par adyacente si no. No es del convenio: es una regla de reparto, como domingo_ok.
    A diferencia de domingo_ok, no hace falta excepción de festivos: un festivo trabajado entre
    semana ocupa el día igual que un laborable."""
    dias = [lunes + timedelta(days=i) for i in range(7)]
    if (trabajador_id, dias[5]) not in plan or (trabajador_id, dias[6]) not in plan:
        return True
    libres = {d for d in dias[:5] if (trabajador_id, d) not in plan}
    fijos = datos.config.dias_descanso_finde
    if fijos:
        idx = {nombre: i for i, nombre in enumerate(DIAS_LV)}
        return all(dias[idx[nombre]] in libres for nombre in fijos)
    return any(dias[i] in libres and dias[i + 1] in libres for i in range(4))


def permite(datos: Datos, plan: Plan, trabjador_id: str, fecha: date, turno_id: str,
            exento_localizado: bool = False) -> bool:
    """¿Es legal añadir este turno a este trabajador este día, sobre el plan tal y como está?"""
    if (trabjador_id, fecha) in plan:                                       # C2
        return False
    return (descanso_ok(datos, plan, trabjador_id, fecha, turno_id, exento_localizado)
            and dias_semana_ok(datos, plan, trabjador_id, fecha)
            and horas_semana_ok(datos, plan, trabjador_id, fecha, turno_id)
            and domingo_ok(datos, plan, trabjador_id, fecha, turno_id))


def pactadas(datos: Datos, plan: Plan) -> set:
    """Las formas de incumplimiento que el base produce por sí mismo: son las que vienen
    pactadas con los trabajadores y las que, por tanto, se pueden mover de una persona a otra."""
    salida: set = set()
    for trab in {trabajador_id for (trabajador_id, _) in plan}:
        salida |= set(formas(datos, plan, trab, datos.inicio, datos.fin))
    return salida


def formas(datos: Datos, plan: Plan, trabajador_id: str, desde: date, hasta: date) -> Counter:
    """(tipo, forma) -> nº de casos de ese trabajador en el tramo. La FORMA es la secuencia de
    turnos que provoca el incumplimiento, no la persona ni la fecha: es lo que permite decir si
    algo ya lo produce el patrón o se lo ha inventado el pipeline."""
    dias = {}
    f = desde - timedelta(days=8)
    fin = hasta + timedelta(days=8)
    while f <= fin:
        s = plan.get((trabajador_id, f))
        if s is not None:
            dias[f] = s
        f += timedelta(days=1)

    minimo = timedelta(hours=datos.config.descanso_minimo)
    salida: Counter = Counter()
    for f, s in dias.items():
        g = f + timedelta(days=1)
        if g in dias and datos.intervalo(dias[g], g)[0] - datos.intervalo(s, f)[1] < minimo:
            # Igual que en `infracciones`: se separa cuando hay una guardia de LOCALIZACIÓN de por
            # medio, que es disponibilidad y no presencia. Sigue contándose, pero no es lo mismo.
            loc = datos.localizado(s) or datos.localizado(dias[g])
            salida[("C4-loc" if loc else "C4", (s, dias[g]))] += 1

    semanas: dict[date, list[str]] = defaultdict(list)
    for f, s in dias.items():
        semanas[f - timedelta(days=f.weekday())].append(s)
    for turnos in semanas.values():
        if len(turnos) > datos.config.dias_max_semana:
            salida[("C5", len(turnos))] += 1
        if sum(datos.turnos[x].horas for x in turnos) > datos.config.horas_max_semana:
            salida[("C6", tuple(sorted(turnos)))] += 1
    return salida


def infracciones(datos: Datos, plan: Plan) -> list[str]:
    """Lo que incumple un plan ya terminado. No decide nada: es la comprobación que se imprime al
    cerrar cada paso, para no dar por bueno un cuadrante ilegal porque la cobertura salga bien."""
    fallos: list[str] = []
    por_trab: dict[str, list[date]] = {}
    for (w, f) in plan:
        por_trab.setdefault(w, []).append(f)

    minimo = timedelta(hours=datos.config.descanso_minimo)
    for w, fechas in sorted(por_trab.items()):
        fechas.sort()
        horas = {f: datos.turnos[plan[(w, f)]].horas for f in fechas}

        for f in fechas:                                        # C4
            sig = f + timedelta(days=1)
            if (w, sig) in plan:
                hueco = datos.intervalo(plan[(w, sig)], sig)[0] - datos.intervalo(plan[(w, f)], f)[1]
                if hueco < minimo:
                    # Se etiqueta aparte cuando hay una guardia de LOCALIZACIÓN de por medio: no es
                    # presencia sino disponibilidad, así que ni exige descanso después ni lo
                    # consume antes. Sigue apareciendo en el informe —el descanso debe respetarse
                    # salvo que sea la única forma de cubrir— pero no es el mismo incumplimiento.
                    loc = (datos.localizado(plan[(w, f)]) or datos.localizado(plan[(w, sig)]))
                    fallos.append(f"{'C4-loc' if loc else 'C4'} {w} {f:%d/%m}->{sig:%d/%m}: "
                                  f"{hueco.total_seconds() / 3600:.1f} h de descanso")

        semanas: dict[date, list[date]] = {}                    # C5 + C6, misma semana ISO
        for f in fechas:
            lunes = f - timedelta(days=f.weekday())
            semanas.setdefault(lunes, []).append(f)
        for lunes, dias_semana in sorted(semanas.items()):
            if len(dias_semana) > datos.config.dias_max_semana:
                fallos.append(f"C5 {w} semana del {lunes:%d/%m}: {len(dias_semana)} días trabajados")
            total = sum(horas[f] for f in dias_semana)
            if total > datos.config.horas_max_semana:
                fallos.append(f"C6 {w} semana del {lunes:%d/%m}: {total:.0f} h")
    return fallos


def integridad(datos: Datos, plan: Plan, ritmos: dict[str, "ritmo_mod.Ritmo"] | None = None) -> list[str]:
    """Lo que haría el cuadrante inejecutable, al margen del convenio: alguien asignado estando de
    vacaciones, un turno en un día en que su línea no opera, alguien sin capacidad para la línea que
    hace, o más gente asignada que demanda tiene la plaza. Aquí nunca debería haber nada."""
    cuenta: dict[tuple[str, date], int] = {}
    fallos: list[str] = []
    for (w, f), s in plan.items():
        cuenta[(s, f)] = cuenta.get((s, f), 0) + 1
        if not datos.disponible(w, f):
            fallos.append(f"{w} asignado a {s} el {f:%d/%m} estando de vacaciones")
        elif not datos.opera(s, f):
            fallos.append(f"{w} hace {s} el {f:%d/%m}, día en que esa línea no opera")
        elif not datos.elegible(w, s, f)[0]:
            fallos.append(f"{w} hace {s} el {f:%d/%m} sin capacidad declarada")
        elif not domingo_ok(datos, plan, w, f, s):
            fallos.append(f"{w} hace {s} el {f:%d/%m} (domingo) sin el sábado de ese fin de semana")
    for (s, f), n in cuenta.items():
        if datos.turnos[s].dem and n > datos.turnos[s].dem:
            fallos.append(f"{s} el {f:%d/%m}: {n} asignados para {datos.turnos[s].dem} de demanda")

    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, plan)
    vistas: set[tuple[str, date]] = set()
    for (w, f) in plan:
        lunes = f - timedelta(days=f.weekday())
        if (w, lunes) in vistas or ritmo_mod.es_rigido(datos, ritmos, w):
            continue
        vistas.add((w, lunes))
        if not descanso_finde_ok(datos, plan, w, lunes):
            fallos.append(f"{w} semana del {lunes:%d/%m}: sábado y domingo sin un par de días "
                          f"consecutivos libres entre semana")
    return fallos


def auditar(datos: Datos, plan: Plan, pactadas_esqueleto: set,
            domingos_esqueleto: set[tuple[str, date]] | None = None,
            descansos_esqueleto: set[tuple[str, date]] | None = None,
            ritmos: dict[str, "ritmo_mod.Ritmo"] | None = None) -> None:
    """El repaso legal que se imprime al cerrar cada ejecución.

    La legalidad NO se mide contando: se mide por FORMAS. Los patrones incumplen el convenio por
    acuerdo con los trabajadores, así que el cuadrante nace con más de mil incumplimientos que hay
    que respetar. Lo que importa no es el total, sino cuántos tienen una forma —un par de turnos
    seguidos, una semana ISO— que el esqueleto NO produce por su cuenta: esos se los ha inventado
    el pipeline, y son los únicos que hay que mirar.

    domingos_esqueleto y descansos_esqueleto son lo que ya trae el esqueleto puro (Paso A) para
    cada regla — se toleran igual que las formas pactadas de los patrones y no cuentan como
    FALLOS nuevos.
    """
    domingos_esqueleto = domingos_esqueleto or set()
    descansos_esqueleto = descansos_esqueleto or set()
    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, plan)
    rotos = integridad(datos, plan, ritmos)

    domingo_actual = {(w, f) for (w, f), s in plan.items() if not domingo_ok(datos, plan, w, f, s)}
    heredados_dom = domingo_actual & domingos_esqueleto
    nuevos_domingo = domingo_actual - domingos_esqueleto

    descanso_actual: set[tuple[str, date]] = set()
    vistas: set[tuple[str, date]] = set()
    for (w, f) in plan:
        lunes = f - timedelta(days=f.weekday())
        if (w, lunes) in vistas or ritmo_mod.es_rigido(datos, ritmos, w):
            continue
        vistas.add((w, lunes))
        if not descanso_finde_ok(datos, plan, w, lunes):
            descanso_actual.add((w, lunes))
    heredados_desc = descanso_actual & descansos_esqueleto
    nuevos_descanso = descanso_actual - descansos_esqueleto

    otros = [r for r in rotos if "sin el sábado" not in r and "consecutivos libres" not in r]
    graves = len(otros) + len(nuevos_domingo) + len(nuevos_descanso)
    if graves == 0:
        extra_bits = []
        if heredados_dom:
            extra_bits.append(f"{len(heredados_dom)} domingo(s) heredado(s) del esqueleto")
        if heredados_desc:
            extra_bits.append(f"{len(heredados_desc)} semana(s) sin descanso consecutivo "
                              f"heredada(s) del esqueleto")
        extra = f" ({', '.join(extra_bits)}, tolerados)" if extra_bits else ""
        print(f"\nAUDITORÍA — integridad: correcta{extra}")
    else:
        if otros:
            ejemplo = otros[0]
        elif nuevos_domingo:
            w, f = next(iter(nuevos_domingo))
            ejemplo = f"{w} el {f:%d/%m}: domingo NUEVO sin el sábado de ese fin de semana"
        else:
            w, lunes = next(iter(nuevos_descanso))
            ejemplo = f"{w} semana del {lunes:%d/%m}: NUEVA sin par de días consecutivos libres"
        print(f"\nAUDITORÍA — integridad: *** {graves} FALLOS: {ejemplo} ***")

    total: Counter = Counter()
    for trab in {w for (w, _) in plan}:
        total += formas(datos, plan, trab, datos.inicio, datos.fin)
    inventadas = Counter({k: n for k, n in total.items() if k not in pactadas_esqueleto})
    casos = sum(total.values())
    print(f"  incumplimientos: {casos} en total · {casos - sum(inventadas.values())} pactados "
          f"(los produce el propio patrón) · {sum(inventadas.values())} introducidos por el pipeline")
    if inventadas:
        print("    por tipo: " + " · ".join(
            f"{t}: {n}" for t, n in sorted(Counter(k[0] for k in inventadas.elements()).items())))

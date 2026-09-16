"""
residuo.py — Paso D: el ÚNICO CP-SAT del pipeline. Un solo modelo anual.

Los pasos A y B dejan el cuadrante casi entero decidido: patrones rotados, fijos en su línea L-V,
plazas traspasadas a su cubridor designado y el exceso de horas de todo el mundo ya cedido. Lo que
queda abierto son exactamente DOS decisiones, y las toma este modelo:

  * **El turno real de cada CORRETURNO.** Llegan aquí con CERO asignaciones: el paso B ya no les
    pone nada. Cada hueco real (`dem>0` sin cubrir) es candidato para quien tenga capacidad.
  * **El fin de semana de los EX-MIXTOS.** Son `tipo == "fijo"` que además declaran capacidad de
    sábado/domingo/festivo en `capacidades.csv`, aparte de su línea L-V. Trabajar un finde les
    cuesta SOLTAR un día L-V de esa misma semana — su línea deja de estar cubierta ese día, y ese
    hueco entra en el reparto como cualquier otro (lo puede absorber un correturno).

Todo lo demás del plan es FIJO: el modelo no lo mira más que como contexto (para el descanso entre
turnos, los días y las horas de la semana ISO, y como constante en los contadores de equidad).

**La franja semanal ya no se precalcula.** El antiguo paso C (`forma.py`) le fijaba a cada
correturno una franja y una ZONA por semana con una heurística, y el paso D solo pagaba por
salirse. Ahora eso es un NIVEL del objetivo dentro de este mismo modelo (nivel 7), la zona se ha
descartado como criterio, y el paso C ha desaparecido.

El objetivo es LEXICOGRÁFICO, no una suma ponderada: se resuelve un nivel, se clava su valor como
restricción y se pasa al siguiente. Así un nivel no puede comprarle nada al de encima.

  1. COBERTURA    — plazas reales que quedan sin cubrir. Manda sobre todo lo demás.
  2. HORAS MIXTOS — acercar a los ex-mixtos lo máximo posible a su jornada anual (C9 ya impide
                    pasarse). Va justo después de cobertura porque es lo que decide cuánto cupo de
                    finde usan de verdad — y eso es lo que alimenta la equidad de los niveles 3-5.
                    Se clava POR PERSONA (no solo el agregado), pero eso no le quita margen a la
                    equidad: si los turnos candidatos valen las mismas horas, cualquier combinación
                    que sume igual para cada uno ya cumple la restricción, gratis, sin tolerancia.
  3-5. FINDES     — sábado, domingo y festivo, CADA UNO SU PROPIO NIVEL, en ese orden, sobre un
                    pool FUSIONADO de tres grupos: PAT_GRANDE_VALL + ex-mixtos + correturnos. Los
                    del patrón entran como CONSTANTE (su cuadrante ya está decidido): son la
                    referencia a la que hay que llegar. Van encadenados y no sumados en un único
                    nivel para que mejorar festivo nunca pueda costarle nada a domingo, ni domingo
                    a sábado.
  6. HORAS REALES — que la demanda real se reparta por igual entre los correturnos. Sin esto, uno
                    se lleva el año entero y a otro hay que completarle la jornada con refuerzos de
                    calendario, que no cubren nada.
  7. FORMA        — dentro de cada semana ISO, el mismo turno todos los días; y si no puede ser, al
                    menos la misma franja. Preferencia, no restricción.
  8. LOCALIZADO   — el uso de la exención de C4. Una guardia de 24 h es localización, no presencia,
                    así que no ocupa el día siguiente; pero eso solo debe gastarse cuando es la
                    única forma de cubrir algo que si no quedaría vacío.

Cada nivel arranca SEMBRADO con la solución del anterior (`AddHint`). No es un detalle de
rendimiento: con la cobertura clavada en su óptimo la región factible es durísima de encontrar
desde cero, y la solución del nivel previo ya está dentro de ella.

Por DEBAJO de todos los niveles, como restricción DURA: C4 (descanso entre jornadas), C5 (días por
semana ISO), C6 (horas por semana ISO) y `domingo_ok` — las mismas de `legal.py`, aquí en forma de
restricción del modelo en vez de test. Se aplican SIEMPRE, porque todo lo que decide este paso es
una secuencia INVENTADA; nunca se auditan los ciclos de patrón heredados, que no se tocan.

El relleno con REF CAL (`rellenar_refuerzos`) y el canje de refuerzos (`canjear_refuerzos`,
`canjear_en_cadena`) siguen siendo post-proceso FUERA del modelo: un refuerzo declara demanda 0, no
es cobertura, y no tiene nada que hacer dentro de un objetivo que optimiza cobertura.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from ortools.sat.python import cp_model

import legal
from cargar_datos import Datos
from horas import EPS, LibroHoras

Plan = dict[tuple[str, date], str]

DECIMAS = 10                    # las horas son floats; el modelo trabaja en décimas de hora
FINDE = ("SAB", "DOM", "FEST")
PATRON_GRANDE = "PAT_GRANDE_VALL"   # el grupo con el que se equipara todo el que hace findes
PESO_FRANJA = 10                    # nivel 4: salirse de franja pesa 10 veces salirse de turno


def _decimas(h: float) -> int:
    return int(round(h * DECIMAS))


def lunes_de(f: date) -> date:
    """Lunes de la semana ISO en que cae `f`. La unidad de C5, C6 y de la forma semanal."""
    return f - timedelta(days=f.weekday())


def huecos(datos: Datos, plan: Plan) -> list[tuple[str, date]]:
    """Plazas REALES que operan y no tiene nadie, una entrada por plaza sin cubrir. Las líneas de
    refuerzo (`dem == 0`) nunca son hueco: no las echa de menos nadie."""
    cubiertas: Counter = Counter()
    for (_, f), s in plan.items():
        cubiertas[(s, f)] += 1
    sueltas: list[tuple[str, date]] = []
    for s, t in datos.turnos.items():
        for f in datos.lista_dias_calendario:
            if datos.opera(s, f):
                sueltas += [(s, f)] * max(0, t.dem - cubiertas[(s, f)])
    return sueltas


def pool_correturno(datos: Datos) -> list[str]:
    """El pool rodante de verdad: los correturnos. Llegan al paso D sin ninguna asignación."""
    return sorted(w for w, t in datos.trabajadores.items() if t.tipo == "correturno")


def mixtos_con_finde(datos: Datos) -> list[str]:
    """
    Aquellos trabajadores que tienen fijo una linea lv pero ademas hacen findes
    """
    salida = []
    for w, t in datos.trabajadores.items():
        if t.tipo != "fijo":
            continue
        if any(cap.v == 0 and (cap.sab or cap.dom or cap.fest)
               for (ww, s), cap in datos.capacidades.items()
               if ww == w and s in datos.turnos):
            salida.append(w)
    return sorted(salida)


def _pares_incompatibles(datos: Datos, lineas: list[str]) -> tuple[set, set]:
    """Pares (línea de hoy, línea de mañana) que no respetan el descanso mínimo, separados en los
    que se PROHÍBEN y los que solo se PENALIZAN por haber una guardia de localización de por medio.
    Se calculan una vez: dependen solo del reloj de cada turno, así que son los mismos todos los
    días. Es la misma regla que `legal.descanso_ok`, precompilada para el modelo."""
    minimo = timedelta(hours=datos.config.descanso_minimo)
    ancla = date(2001, 1, 1)
    duros, exentos = set(), set()
    for a in lineas:
        fin = datos.intervalo(a, ancla)[1]
        for b in lineas:
            if datos.intervalo(b, ancla + timedelta(days=1))[0] - fin < minimo:
                if datos.localizado(a) or datos.localizado(b):
                    exentos.add((a, b))
                else:
                    duros.add((a, b))
    return duros, exentos


# --------------------------------------------------------------------------- #
#  Los ex-mixtos: soltar la semana para poder hacer el finde
# --------------------------------------------------------------------------- #
def _soltar_semanas(datos: Datos, plan: Plan, libro: LibroHoras, mixtos: list[str],
                    faltan: Counter) -> dict[tuple[str, date], int]:
    """Libera del plan los días L-V de cada ex-mixto en las semanas en que tiene a su alcance algún
    finde con demanda sin cubrir, y devuelve el CUPO de días que puede volver a trabajar esa semana.

    Es la mecánica que el planificador hace a mano: darle un día de fin de semana es quitarle uno
    entre semana. Aquí no se elige cuál — se sueltan todos los de esa semana y el modelo decide
    cuántos recupera (como mucho `cupo`, o sea los mismos que tenía) y cuáles. El día que no
    recupera queda como hueco de su línea, y lo puede tapar un correturno como cualquier otro.

    Solo se toca la semana si hay finde REAL que ofrecerle: si esa semana no hay ningún hueco de
    sábado/domingo/festivo que él pueda hacer, su línea se queda tal cual estaba.
    """
    cupo: dict[tuple[str, date], int] = {}
    for w in mixtos:
        semanas = {lunes_de(f) for (s, f) in faltan
                   if datos.tipo_dia(f, datos.turnos[s].municipio) in FINDE
                   and (w, f) not in plan and datos.elegible(w, s, f)[0]}
        for lunes in sorted(semanas):
            liberados = 0
            for i in range(5):
                turno = plan.pop((w, lunes + timedelta(days=i)), None)
                if turno is not None:
                    libro.borra(w, turno)
                    liberados += 1
            cupo[(w, lunes)] = liberados
    return cupo


# --------------------------------------------------------------------------- #
#  El modelo
# --------------------------------------------------------------------------- #
def resolver(datos: Datos, plan: Plan, libro: LibroHoras, segundos: int = 300, hilos: int = 8,
             log: bool = False, descanso_finde: bool = True) -> dict:
    """Resuelve el paso D sobre `plan`/`libro`, que modifica in place."""
    correturnos = pool_correturno(datos)
    mixtos = mixtos_con_finde(datos)
    pool = sorted(set(correturnos) | set(mixtos))
    es_mixto = set(mixtos)

    cupo = _soltar_semanas(datos, plan, libro, mixtos, Counter(huecos(datos, plan)))
    faltan = Counter(huecos(datos, plan))
    lineas = sorted({s for s, _ in faltan})
    print(f"  huecos a repartir: {sum(faltan.values())} · pool: {len(correturnos)} correturnos "
          f"+ {len(mixtos)} fijos con finde · {len(cupo)} semanas soltadas")

    modelo = cp_model.CpModel()

    # -- Variables: (trabajador, día, línea) --------------------------------- #
    # Se crean solo las que ya son legales contra lo YA FIJADO del propio trabajador. `laxas` son
    # las que solo pasan gracias a la exención de localizado: existen, pero se pagan en el nivel 5.
    x: dict[tuple[str, date, str], cp_model.IntVar] = {}
    laxas: list[tuple[str, date, str]] = []
    for (s, f) in faltan:
        for w in pool:
            if (w, f) in plan or not datos.elegible(w, s, f)[0]:
                continue
            if not legal.descanso_ok(datos, plan, w, f, s, True):
                continue                    # choca con un día fijo suyo ni con la exención
            if not (legal.dias_semana_ok(datos, plan, w, f)
                    and legal.horas_semana_ok(datos, plan, w, f, s)):
                continue                    # su parte fija de la semana ya no deja sitio
            x[(w, f, s)] = modelo.NewBoolVar(f"x_{w}_{f:%m%d}_{s}")
            if not legal.descanso_ok(datos, plan, w, f, s, False):
                laxas.append((w, f, s))

    por_dia: dict[tuple[str, date], list] = defaultdict(list)
    por_plaza: dict[tuple[str, date], list] = defaultdict(list)
    por_trab: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for (w, f, s) in x:
        por_dia[(w, f)].append(x[(w, f, s)])
        por_plaza[(s, f)].append(x[(w, f, s)])
        por_trab[w].append((f, s))
    for w in por_trab:
        por_trab[w].sort()

    for vs in por_dia.values():                      # C2 — un turno por persona y día
        modelo.AddAtMostOne(vs)
    for clave, vs in por_plaza.items():              # no se cubre más de lo que falta
        modelo.Add(sum(vs) <= faltan[clave])

    # -- C4: descanso entre jornadas ----------------------------------------- #
    duros, exentos = _pares_incompatibles(datos, lineas)
    exenciones: list = [x[k] for k in laxas]
    for w in pool:
        suyos: dict[date, list[str]] = defaultdict(list)
        for (f, s) in por_trab[w]:
            suyos[f].append(s)
        for f in sorted(suyos):
            for a in suyos[f]:
                for b in suyos.get(f + timedelta(days=1), ()):
                    va, vb = x[(w, f, a)], x[(w, f + timedelta(days=1), b)]
                    if (a, b) in duros:
                        modelo.AddAtMostOne([va, vb])
                    elif (a, b) in exentos:
                        # Permitido, pero contado: `e` se enciende si se usan los dos, y el nivel 5
                        # lo minimiza. No hace falta forzar e=0 cuando no: se está minimizando.
                        e = modelo.NewBoolVar(f"loc_{w}_{f:%m%d}")
                        modelo.Add(va + vb - 1 <= e)
                        exenciones.append(e)

    # -- Semana ISO: domingo_ok, C5, C6, cupo del mixto, jornada anual -------- #
    semanas_de: dict[str, dict[date, list[tuple[date, str]]]] = defaultdict(lambda: defaultdict(list))
    for w in pool:
        vars_fecha: dict[date, list] = defaultdict(list)
        for (f, s) in por_trab[w]:
            vars_fecha[f].append(x[(w, f, s)])
            semanas_de[w][lunes_de(f)].append((f, s))

        # domingo_ok — nunca un domingo suelto sin el sábado de ese fin de semana. Dura, no se paga.
        for f in sorted(vars_fecha):
            doms = [x[(w, f, s)] for (g, s) in por_trab[w]
                    if g == f and datos.tipo_dia(f, datos.turnos[s].municipio) == "DOM"]
            if not doms:
                continue
            ayer = f - timedelta(days=1)
            fijo_ayer = 1 if (w, ayer) in plan else 0
            modelo.Add(sum(doms) <= sum(vars_fecha.get(ayer, [])) + fijo_ayer)

        for lunes, dias in sorted(semanas_de[w].items()):
            fechas = [lunes + timedelta(days=i) for i in range(7)]
            fijos = [plan[(w, g)] for g in fechas if (w, g) in plan]
            mios = [x[(w, f, s)] for f, s in dias]

            modelo.Add(sum(mios) <= datos.config.dias_max_semana - len(fijos))   # C5
            modelo.Add(sum(_decimas(datos.turnos[s].horas) * x[(w, f, s)] for f, s in dias)   # C6
                       <= _decimas(datos.config.horas_max_semana
                                   - sum(datos.turnos[s].horas for s in fijos)))

            if w in es_mixto:
                # Cada finde que haga le cuesta un día L-V de la misma semana: puede recuperar como
                # mucho tantos días como se le soltaron. Si esta semana no se le soltó ninguno
                # (`cupo` no la tiene), no puede trabajarla — su línea sigue fijada en el plan.
                modelo.Add(sum(mios) <= cupo.get((w, lunes), 0))

            if descanso_finde:
                _descanso_finde(modelo, datos, plan, x, w, lunes, vars_fecha)

        # C9 — jornada anual. Lo que le queda de presupuesto tras el paso A+B.
        modelo.Add(sum(_decimas(datos.turnos[s].horas) * x[(w, f, s)] for f, s in por_trab[w])
                   <= _decimas(libro.objetivo(w) - libro.horas(w)))

    # -- Nivel 1: cobertura --------------------------------------------------- #
    cubiertas = sum(x.values())
    mejor, solucion = _optimizar(modelo, cubiertas, True, segundos, hilos, log, "1 cobertura")
    if mejor is None:
        print("  *** el modelo del paso D no encontró solución ***")
        return {}
    modelo.Add(cubiertas >= mejor)

    # -- Nivel 2: horas de los ex-mixtos, lo más cerca posible de su jornada --- #
    obj2, horas_mixtos_w = _horas_mixtos(modelo, datos, x, por_trab, mixtos, libro)
    if obj2 is not None:
        _sembrar(modelo, x, solucion)
        valor, sol = _optimizar(modelo, obj2, True, segundos, hilos, log, "2 horas de mixtos")
        if valor is not None:
            # Clavado POR PERSONA, no solo el agregado: si no, la equidad del nivel 3 podría
            # quitarle horas a uno para dárselas a otro sin que se note en la suma total.
            for w, h in horas_mixtos_w.items():
                modelo.Add(h >= sol.Value(h))
            solucion = sol

    # -- Niveles 3-5: equidad de sábado, domingo y festivo, EN ESE ORDEN ------ #
    for i, clase in enumerate(FINDE):
        obj = _equidad_finde_clase(modelo, datos, plan, x, por_trab, correturnos, mixtos, clase)
        if obj is None:
            continue
        _sembrar(modelo, x, solucion)
        valor, sol = _optimizar(modelo, obj, False, segundos, hilos, log,
                                f"{3 + i} equidad {clase.lower()}")
        if valor is not None:
            modelo.Add(obj <= valor)
            solucion = sol

    # -- Nivel 6: equidad de horas REALES entre correturnos ------------------- #
    obj6 = _equidad_horas(modelo, datos, x, por_trab, correturnos, libro)
    if obj6 is not None:
        _sembrar(modelo, x, solucion)
        valor, sol = _optimizar(modelo, obj6, False, segundos, hilos, log, "6 equidad de horas")
        if valor is not None:
            modelo.Add(obj6 <= valor)
            solucion = sol

    # -- Nivel 7: forma semanal ----------------------------------------------- #
    obj7 = _forma_semanal(modelo, datos, x, semanas_de, pool)
    if obj7 is not None:
        _sembrar(modelo, x, solucion)
        valor, sol = _optimizar(modelo, obj7, False, segundos, hilos, log, "7 forma semanal")
        if valor is not None:
            modelo.Add(obj7 <= valor)
            solucion = sol

    # -- Nivel 8: apoyarse lo menos posible en el localizado ------------------- #
    if exenciones:
        total_loc = sum(exenciones)
        _sembrar(modelo, x, solucion)
        valor, sol = _optimizar(modelo, total_loc, False, segundos, hilos, log,
                                "8 apoyos en localizado")
        if valor is not None:
            solucion = sol

    return _volcar(datos, plan, libro, x, solucion)


def _descanso_finde(modelo, datos: Datos, plan: Plan, x: dict, w: str, lunes: date,
                    vars_fecha: dict) -> None:
    """Si esa semana ISO se trabajan sábado Y domingo, exige un par de días consecutivos libres
    entre semana (`legal.descanso_finde_ok`, aquí como restricción del modelo). No es del convenio:
    es una regla de reparto, la misma que comprueban el paso B y el paso E.

    Los días del trabajador que están FIJADOS fuera del modelo entran como constante — para un
    correturno no hay ninguno, para un ex-mixto son las semanas que no se le han soltado."""
    dias = [lunes + timedelta(days=i) for i in range(7)]

    def ocupa(d: date):
        """1 / 0 / expresión: ¿trabaja ese día?"""
        if (w, d) in plan:
            return 1
        return sum(vars_fecha.get(d, [])) if vars_fecha.get(d) else 0

    sab, dom = ocupa(dias[5]), ocupa(dias[6])
    if (isinstance(sab, int) and sab == 0) or (isinstance(dom, int) and dom == 0):
        return                                            # esta semana no puede tener los dos
    ambos = modelo.NewBoolVar(f"ambosfinde_{w}_{lunes:%m%d}")
    modelo.Add(ambos >= sab + dom - 1)

    libres = []
    for d in dias[:5]:
        if (w, d) in plan:
            libres.append(0)                              # día fijo: ocupado, nunca libre
        elif vars_fecha.get(d):
            libre = modelo.NewBoolVar(f"librefinde_{w}_{d:%m%d}")
            modelo.Add(sum(vars_fecha[d]) + libre == 1)   # C2 ya garantiza como mucho 1 turno/día
            libres.append(libre)
        else:
            libres.append(1)                              # ni fijo ni candidato: libre seguro
    pares = []
    for i in range(4):
        a, b = libres[i], libres[i + 1]
        if isinstance(a, int) and isinstance(b, int):
            pares.append(a * b)
            continue
        par = modelo.NewBoolVar(f"parfinde_{w}_{dias[i]:%m%d}")
        modelo.Add(par <= a)
        modelo.Add(par <= b)
        pares.append(par)
    modelo.Add(sum(pares) >= ambos)


# --------------------------------------------------------------------------- #
#  Los niveles del objetivo
# --------------------------------------------------------------------------- #
def _reparto(modelo, cuentas: list, techo: int, etiqueta: str) -> list:
    """Términos que miden lo DESIGUAL que es un reparto, sobre una lista de contadores que pueden
    ser variables o constantes:

      * la desviación de cada uno respecto a la media, escalada por N para no usar racionales
        (`N*n_w - S` con `S = sum(n_w)`, que es exactamente `N*|n_w - media|`);
      * el RANGO (máximo menos mínimo), también escalado por N.

    Con la desviación sola la métrica es ciega al reparto del exceso —le da igual dos personas muy
    por encima que cuatro un poco por encima—; con el rango solo, es ciega a todo lo que no sean
    los dos extremos. Juntas dicen lo que se quiere decir.
    """
    n = len(cuentas)
    if n < 2:
        return []
    total = sum(cuentas)
    terminos = []
    for i, c in enumerate(cuentas):
        d = modelo.NewIntVar(0, n * techo, f"dev_{etiqueta}_{i}")
        modelo.Add(d >= n * c - total)
        modelo.Add(d >= total - n * c)
        terminos.append(d)
    expr = [c if not isinstance(c, int) else modelo.NewConstant(c) for c in cuentas]
    alto = modelo.NewIntVar(0, techo, f"max_{etiqueta}")
    bajo = modelo.NewIntVar(0, techo, f"min_{etiqueta}")
    modelo.AddMaxEquality(alto, expr)
    modelo.AddMinEquality(bajo, expr)
    terminos.append(n * (alto - bajo))
    return terminos


def _equidad_horas(modelo, datos: Datos, x: dict, por_trab: dict, correturnos: list[str],
                   libro: LibroHoras):
    """Nivel 6 — que la demanda REAL se reparta por igual entre los correturnos.

    Todo lo que hay en el modelo es demanda real (`huecos` ignora las líneas de refuerzo), así que
    basta con sumar las horas de sus variables. Lo que se persigue es que nadie termine el año con
    poca cobertura real y haya que completarle la jornada con refuerzos de calendario, que no
    cubren nada, mientras otro se lleva toda la demanda.
    """
    if len(correturnos) < 2:
        return None
    techo = _decimas(max(libro.objetivo(w) for w in correturnos))
    cuentas = []
    for w in correturnos:
        h = modelo.NewIntVar(0, techo, f"h_{w}")
        modelo.Add(h == sum(_decimas(datos.turnos[s].horas) * x[(w, f, s)]
                            for f, s in por_trab.get(w, ())))
        cuentas.append(h)
    terminos = _reparto(modelo, cuentas, techo, "horas")
    return sum(terminos) if terminos else None


def _horas_mixtos(modelo, datos: Datos, x: dict, por_trab: dict, mixtos: list[str],
                  libro: LibroHoras):
    """Nivel 2 — acercar a cada ex-mixto lo máximo posible a su jornada anual.

    No es equidad entre ellos, es simple maximización: C9 ya impide pasarse de objetivo, así que
    maximizar la suma es exactamente "lo más cerca posible sin pasarse". Va justo después de
    cobertura y ANTES de la equidad de findes (nivel 3) a propósito: cuánto finde hace cada mixto
    se decide aquí, y la equidad de después tiene que trabajar con esa referencia ya fijada.

    Devuelve la suma a maximizar y, por persona, su variable de horas — así, tras resolver, cada
    mixto se puede clavar en SU propio óptimo (no solo el agregado), y la equidad de después puede
    recomponer libremente QUÉ turno concreto hace cada día (un festivo por un domingo de horas
    parecidas, por ejemplo) sin que eso cuente como perder horas: si los turnos candidatos valen
    lo mismo, cualquier combinación que sume igual ya cumple la restricción, gratis, sin tolerancia.
    """
    if not mixtos:
        return None, {}
    techo = _decimas(max(libro.objetivo(w) for w in mixtos))
    horas_w: dict[str, cp_model.IntVar] = {}
    for w in mixtos:
        h = modelo.NewIntVar(0, techo, f"hmix_{w}")
        modelo.Add(h == sum(_decimas(datos.turnos[s].horas) * x[(w, f, s)]
                            for f, s in por_trab.get(w, ())))
        horas_w[w] = h
    return sum(horas_w.values()), horas_w


def _equidad_finde_clase(modelo, datos: Datos, plan: Plan, x: dict, por_trab: dict,
                         correturnos: list[str], mixtos: list[str], clase: str):
    """Niveles 3-5 — sábado, domingo y festivo, cada uno IGUALADO POR SEPARADO (una llamada por
    clase, en orden de prioridad) en un pool FUSIONADO de tres grupos: el patrón grande de
    Valladolid, los ex-mixtos y los correturnos.

    Antes las tres clases se sumaban en un único nivel, y el solver podía mejorar festivo a costa
    de empeorar un poco domingo si la suma total salía mejor — no eran independientes de verdad.
    Ahora cada clase es su propio nivel lexicográfico: el resultado de sábado queda clavado antes
    de tocar domingo, y el de domingo antes de tocar festivo, igual que el resto del modelo.

    Los del patrón no tienen ni una variable en el modelo —su cuadrante lo decidió el paso A y no
    se toca—, así que entran como CONSTANTE: son la referencia que fija la media a la que los
    otros dos grupos tienen que llegar. Sin ellos dentro, los correturnos se igualarían entre sí
    en cualquier valor, por bajo que fuese.
    """
    grandes = [w for w, t in datos.trabajadores.items() if t.patron == PATRON_GRANDE]
    fusion = sorted(set(correturnos) | set(mixtos) | set(grandes))
    if len(fusion) < 2:
        return None

    fijas: Counter = Counter()
    for (w, f), s in plan.items():
        if datos.tipo_dia(f, datos.turnos[s].municipio) == clase:
            fijas[w] += 1

    cuentas = []
    for w in fusion:
        suyos = [x[(w, f, s)] for f, s in por_trab.get(w, ())
                 if datos.tipo_dia(f, datos.turnos[s].municipio) == clase]
        if not suyos:
            cuentas.append(fijas[w])                      # constante: nada que decidir
            continue
        n = modelo.NewIntVar(0, 400, f"n_{w}_{clase}")
        modelo.Add(n == fijas[w] + sum(suyos))
        cuentas.append(n)
    terminos = _reparto(modelo, cuentas, 400, clase)
    return sum(terminos) if terminos else None


def _forma_semanal(modelo, datos: Datos, x: dict, semanas_de: dict, pool: list[str]):
    """Nivel 7 — dentro de cada semana ISO, el MISMO turno todos los días que trabaje; y si no
    puede ser, al menos la misma FRANJA (mañana / tarde / noche).

    Se mide contando, por persona y semana, cuántas franjas y cuántos turnos distintos usa por
    encima del primero: cero si toda la semana es el mismo turno. La franja pesa `PESO_FRANJA`
    veces más que el turno, que es lo que la ordena por delante sin encadenar otro nivel.

    Es preferencia, no restricción: si romperla tapa un hueco, los niveles de encima ya se lo han
    llevado. La ZONA (el municipio) NO entra: se descartó como criterio.
    """
    coste = []
    for w in pool:
        for lunes, dias in sorted(semanas_de.get(w, {}).items()):
            if len(dias) < 2:
                continue                     # un solo candidato: no hay forma que romper
            mios = [x[(w, f, s)] for f, s in dias]
            trabaja = modelo.NewBoolVar(f"tra_{w}_{lunes:%m%d}")
            modelo.AddMaxEquality(trabaja, mios)

            franjas: dict[str, list] = defaultdict(list)
            turnos: dict[str, list] = defaultdict(list)
            for f, s in dias:
                franjas[datos.franja(s)].append(x[(w, f, s)])
                turnos[s].append(x[(w, f, s)])
            if len(franjas) > 1:
                usa = []
                for fr, vs in sorted(franjas.items()):
                    u = modelo.NewBoolVar(f"fr_{w}_{lunes:%m%d}_{fr}")
                    modelo.AddMaxEquality(u, vs)
                    usa.append(u)
                coste.append(PESO_FRANJA * (sum(usa) - trabaja))
            if len(turnos) > 1:
                usa = []
                for s, vs in sorted(turnos.items()):
                    u = modelo.NewBoolVar(f"tn_{w}_{lunes:%m%d}_{s}")
                    modelo.AddMaxEquality(u, vs)
                    usa.append(u)
                coste.append(sum(usa) - trabaja)
    return sum(coste) if coste else None


# --------------------------------------------------------------------------- #
#  Motor
# --------------------------------------------------------------------------- #
def _sembrar(modelo, variables: dict, solver) -> None:
    """Le pasa al modelo la solución del nivel anterior como punto de partida. Imprescindible: con
    la cobertura clavada en su óptimo, encontrar una solución factible desde cero se le atraganta
    al solver, y esta ya lo es."""
    if solver is None:
        return
    if hasattr(modelo, "ClearHints"):
        modelo.ClearHints()
    for var in variables.values():
        modelo.AddHint(var, solver.Value(var))


def _optimizar(modelo, expresion, maximizar: bool, segundos: int, hilos: int, log: bool,
               etiqueta: str):
    """Resuelve un nivel del objetivo. Devuelve (valor óptimo, solver) o (None, None)."""
    if maximizar:
        modelo.Maximize(expresion)
    else:
        modelo.Minimize(expresion)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = segundos
    solver.parameters.num_search_workers = hilos
    solver.parameters.log_search_progress = log
    estado = solver.Solve(modelo)
    if estado not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"  nivel {etiqueta}: {solver.StatusName(estado)}")
        return None, None
    valor = int(round(solver.ObjectiveValue()))
    print(f"  nivel {etiqueta}: {valor}  ({solver.StatusName(estado)}, {solver.WallTime():.0f}s)")
    return valor, solver


def _volcar(datos: Datos, plan: Plan, libro: LibroHoras, x: dict, solver) -> dict:
    if solver is None:
        return {}
    puestas = findes = 0
    for (w, f, s), var in sorted(x.items()):
        if solver.Value(var):
            plan[(w, f)] = s
            libro.apunta(w, s)
            puestas += 1
            if datos.tipo_dia(f, datos.turnos[s].municipio) in FINDE:
                findes += 1
    print(f"  asignaciones nuevas: {puestas} (de ellas {findes} en sábado/domingo/festivo)")
    return {"asignadas": puestas, "findes": findes}


# --------------------------------------------------------------------------- #
#  Canje de refuerzos por cobertura real  (post-proceso, FUERA del modelo)
# --------------------------------------------------------------------------- #
def canjear_refuerzos(datos: Datos, plan: Plan, libro: LibroHoras,
                      protegidos: dict[str, set[date]] | None = None) -> int:
    """Cierra huecos soltando un REF CAL del propio candidato en OTRA fecha del año.

    El caso típico: el 1 de enero falta `VADN006` y hay 31 personas libres, capacitadas y legales
    para hacerlo — pero todas están en 1776 h y se pasarían de jornada. No es un problema de
    plantilla ni de legalidad, es de presupuesto. Y una de ellas tiene, en marzo, un REF CAL: un
    turno de apoyo con demanda 0 que no cubre nada y que nadie echa de menos. Se le quita ese, y con
    esas horas cubre el turno real de enero.

    Sale gratis en todos los frentes: sus horas quedan igual, la cobertura sube en uno, y hay un
    refuerzo menos. Hace falta una pasada aparte porque el CP-SAT del paso D no puede verlo — solo
    tiene variables para el pool — y porque el REF CAL entra después del modelo, así que el
    intercambio "menos relleno por más cobertura" nunca llega a plantearse dentro.

    `protegidos` son días que ya son de otra persona por diseño: nunca se le asigna un turno ahí,
    aunque le sobren horas y sea elegible.
    """
    protegidos = protegidos or {}
    refuerzos_de: dict[str, list[date]] = defaultdict(list)
    for (w, f), s in plan.items():
        if datos.turnos[s].dem == 0:
            refuerzos_de[w].append(f)
    if not refuerzos_de:
        return 0

    # Los correturnos primero: su refuerzo es relleno nuestro, el del patrón es rotación suya.
    candidatos = sorted(datos.trabajadores,
                        key=lambda w: (datos.trabajadores[w].tipo != "correturno", w))
    cerrados = 0
    for (s, f) in huecos(datos, plan):
        for w in candidatos:
            if (w, f) in plan or f in protegidos.get(w, set()) or not datos.elegible(w, s, f)[0]:
                continue
            falta = datos.turnos[s].horas - (libro.objetivo(w) - libro.horas(w))
            if falta > 0:
                # Se sueltan refuerzos suyos, empezando por los más lejanos del hueco para no
                # remover la misma semana. Solo hacen falta los justos para que quepa el turno.
                sueltos = sorted(refuerzos_de.get(w, []), key=lambda g: -abs((g - f).days))
                usados: list[tuple[date, str]] = []
                for g in sueltos:
                    if falta <= 0:
                        break
                    turno_g = plan[(w, g)]
                    falta -= datos.turnos[turno_g].horas
                    usados.append((g, turno_g))
                if falta > 0:
                    continue                              # no le llega ni soltándolos todos
            else:
                usados = []
            if not legal.permite(datos, plan, w, f, s):
                continue
            # Se suelta el REF CAL de otro día (g) para pagar la cobertura de f. Seguro frente al
            # domingo-sin-sábado: todo turno con dem==0 es lv=1 sin fin de semana, así que soltarlo
            # nunca deja huérfano un domingo. NO es seguro frente al descanso consecutivo: soltar o
            # añadir un día entre semana es justo lo que forma o rompe el par, así que se comprueba
            # tras aplicar el cambio y se deshace este candidato si lo rompe.
            for g, _ in usados:
                del plan[(w, g)]
            plan[(w, f)] = s
            t_w = datos.trabajadores[w]
            grupo_w = t_w.patron if t_w.tipo == "patron" and t_w.patron else t_w.tipo
            if (grupo_w not in datos.config.grupos_rigidos
                    and not legal.descanso_finde_ok(datos, plan, w, lunes_de(f))):
                del plan[(w, f)]
                for g, turno_g in usados:
                    plan[(w, g)] = turno_g
                continue
            for g, turno_g in usados:
                libro.borra(w, turno_g)
                refuerzos_de[w].remove(g)
            libro.apunta(w, s)
            cerrados += 1
            break
    return cerrados


def canjear_en_cadena(datos: Datos, plan: Plan, libro: LibroHoras,
                      protegidos: dict[str, set[date]] | None = None) -> int:
    """Cierra huecos con un canje A TRES BANDAS, sin gastar el refuerzo del propio candidato.

    El escalón simple funciona, pero paga con el REF CAL del candidato — y cuando el candidato es un
    trabajador de patrón, ese refuerzo se lo prescribe `patrones.csv` y forma parte de su rotación.
    Medido: los correturnos, aun yendo primeros, no gastan ni uno, porque no pueden cubrir esos
    huecos ni por capacidad ni por legalidad. La única forma de que pague el relleno del correturno
    es encadenar:

        hueco el día F, línea S      ->  lo cubre W, que no tiene horas
        W trabaja S' el día G        ->  lo asume C, un correturno que ese día estaba de relleno
        C suelta su REF CAL del G    ->  con eso W tiene las horas para el día F

    La cobertura del día G no cambia (sale W, entra C), la del día F sube en uno, y desaparece un
    refuerzo sin demanda. Las horas de W y de C quedan dentro de su objetivo, y la rotación del
    patrón se conserva intacta.
    """
    refcal_dia: dict[date, list[str]] = defaultdict(list)
    for (w, f), s in plan.items():
        if datos.turnos[s].dem == 0 and datos.trabajadores[w].tipo == "correturno":
            refcal_dia[f].append(w)
    if not refcal_dia:
        return 0
    dias_de: dict[str, list[date]] = defaultdict(list)
    for (w, f) in plan:
        dias_de[w].append(f)

    cerrados = 0
    for (s, f) in huecos(datos, plan):
        if _cadena(datos, plan, libro, refcal_dia, dias_de, s, f, protegidos or {}):
            cerrados += 1
    return cerrados


def _cadena(datos: Datos, plan: Plan, libro: LibroHoras, refcal_dia: dict, dias_de: dict,
            s: str, f: date, protegidos: dict[str, set[date]]) -> bool:
    for w in datos.trabajadores:
        if (w, f) in plan or f in protegidos.get(w, set()) or not datos.elegible(w, s, f)[0]:
            continue
        falta = datos.turnos[s].horas - (libro.objetivo(w) - libro.horas(w))
        if falta <= 0 or not legal.permite(datos, plan, w, f, s):
            continue
        for g in dias_de.get(w, ()):
            if g == f or not refcal_dia.get(g):
                continue
            propio = plan[(w, g)]
            # Un REF CAL suyo no vale aquí: de eso se encarga el escalón simple, y además no
            # libera a nadie de nada.
            if datos.turnos[propio].dem == 0 or datos.turnos[propio].horas < falta:
                continue
            for c in list(refcal_dia[g]):
                if c == w or not datos.elegible(c, propio, g)[0]:
                    continue
                relleno = datos.turnos[plan[(c, g)]].horas
                if libro.horas(c) - relleno + datos.turnos[propio].horas > libro.objetivo(c) + EPS:
                    continue
                # Se vacía el día G de los dos para juzgar la secuencia que le queda a C. `ref_c`
                # es el REF CAL que C suelta para pagar la cobertura del hueco en F (día distinto):
                # seguro frente al domingo-sin-sábado por la misma razón que en canjear_refuerzos
                # — todo turno con dem==0 es lv=1 sin fin de semana, así que soltarlo nunca deja
                # huérfano un domingo.
                ref_c, turno_w = plan.pop((c, g)), plan.pop((w, g))
                if not legal.permite(datos, plan, c, g, propio):
                    plan[(c, g)], plan[(w, g)] = ref_c, turno_w
                    continue
                plan[(c, g)] = propio
                plan[(w, f)] = s
                t_w = datos.trabajadores[w]
                grupo_w = t_w.patron if t_w.tipo == "patron" and t_w.patron else t_w.tipo
                if (grupo_w not in datos.config.grupos_rigidos
                        and not legal.descanso_finde_ok(datos, plan, w, lunes_de(f))):
                    del plan[(c, g)], plan[(w, f)]
                    plan[(c, g)], plan[(w, g)] = ref_c, turno_w
                    continue
                libro.borra(c, ref_c)
                libro.apunta(c, propio)
                libro.borra(w, turno_w)
                libro.apunta(w, s)
                refcal_dia[g].remove(c)
                dias_de[w].remove(g)
                dias_de[w].append(f)
                dias_de[c].append(g) if g not in dias_de[c] else None
                return True
    return False


def optimizar_canje(datos: Datos, plan: Plan, libro: LibroHoras, saltos: int = 2,
                    segundos: int = 60, hilos: int = 8) -> int:
    """Cierra huecos reasignando turnos y soltando refuerzos, decidido TODO A LA VEZ con un
    CP-SAT — sin patrones de intercambio prefijados: el modelo explora por sí mismo cadenas de
    cualquier forma, acotadas a `saltos` niveles de gente liberándose entre sí.

    LA IDEA: para cada hueco, cualquier candidato con capacidad. Si ya está libre ese día, o solo
    tiene un REF CAL (relleno sin valor real), puede cubrirlo directamente. Si está ocupado con un
    turno REAL, ese turno se convierte también en una "celda" a re-decidir — con la opción de
    quedarse como estaba, o de que otro candidato lo asuma (que si a su vez está ocupado, abre un
    nivel más de celdas) — hasta el tope de `saltos`. El mismo mecanismo (una celda, sus
    candidatos, decide el CP-SAT) sirve tanto para el canje directo como para una cadena de la
    longitud que haga falta, sin programar cada forma por separado.

    No basta con liberar el DÍA del hueco: alguien puede estar libre ese día y aun así no tener
    horas para asumirlo. Por eso, para cada candidato al que le falten horas, también se ofrecen
    como celdas (opcionales, nadie obliga a tocarlas) hasta `TOPE_DIAS_A_CEDER` de sus OTROS días
    reales del año — cederle uno a un tercero libera presupuesto exactamente igual que soltar un
    refuerzo, solo que en vez de relleno sin valor es un turno real que otro pasa a cubrir.

    RESTRICCIONES: cada celda real mantiene EXACTAMENTE un ocupante (se puede cambiar quién, nunca
    descubrirla); cada hueco, como mucho uno (se maximiza cuántos lo consiguen); nadie hace dos
    cosas el mismo día; nadie se pasa de su jornada anual.

    LEGALIDAD: cada asignación NUEVA se comprueba con `legal.permite()` contra el plan de partida.
    Dos asignaciones nuevas de la MISMA persona se prohíben entre sí si caen en la misma semana ISO
    o en fechas consecutivas — los únicos casos donde podrían interactuar (C4 es entre días
    consecutivos, C5/C6 son por semana ISO); fuera de eso, cada comprobación contra el plan de
    partida sigue siendo válida aunque se apliquen varias a la vez, porque soltar algo (una celda
    propia, un refuerzo) solo puede RELAJAR esas reglas en el resto de la semana de quien lo
    suelta, nunca romperlas. `descanso_finde_ok` no lo cubre `permite()` —depende de la semana
    entera, no de un día contra el siguiente— pero como cada persona tiene como mucho UNA
    asignación nueva por semana (por la regla de arriba) y las bajas solo añaden días libres nunca
    los quitan, comprobarlo contra "el plan de partida + solo esta alta" ya es el peor caso posible
    — si pasa ahí, sigue pasando pase lo que pase alrededor. Por eso se comprueba AL CREAR la
    variable, no después: no hace falta deshacer nada, lo que el modelo elige ya es legal.
    """
    pendientes = huecos(datos, plan)
    if not pendientes:
        return 0

    def es_refuerzo(w: str, f: date) -> bool:
        t = plan.get((w, f))
        return t is not None and datos.turnos[t].dem == 0

    def libre_o_refuerzo(w: str, f: date) -> bool:
        t = plan.get((w, f))
        return t is None or datos.turnos[t].dem == 0

    def legal_para(w: str, f: date, s: str) -> bool:
        """`permite()` contra el plan actual, más `descanso_finde_ok` en el peor caso posible."""
        if not legal.permite(datos, plan, w, f, s):
            return False
        viejo = plan.get((w, f))
        plan[(w, f)] = s
        ok = legal.descanso_finde_ok(datos, plan, w, lunes_de(f))
        if viejo is None:
            del plan[(w, f)]
        else:
            plan[(w, f)] = viejo
        return ok

    TOPE_DIAS_A_CEDER = 15   # por candidato con falta de horas: no hace falta ofrecer TODO su año

    dias_reales_de: dict[str, list[date]] = defaultdict(list)
    for (w, g), s in plan.items():
        if datos.turnos[s].dem > 0:
            dias_reales_de[w].append(g)

    # -- 1. Descubrir las celdas reales que entran en juego, por niveles ------ #
    celdas: dict[tuple[str, date], str] = {}          # (titular, fecha) -> turno actual
    frontera: set[tuple[str, date]] = set()

    def agregar(w: str, f: date) -> None:
        clave = (w, f)
        if clave not in celdas:
            celdas[clave] = plan[clave]
            frontera.add(clave)

    for (s, f) in pendientes:
        for w in datos.trabajadores:
            if not datos.elegible(w, s, f)[0]:
                continue
            if not libre_o_refuerzo(w, f):                       # conflicto de día: obligatorio
                agregar(w, f)
            falta = datos.turnos[s].horas - (libro.objetivo(w) - libro.horas(w))
            if falta > EPS:                                      # falta de horas: opcional
                for g in dias_reales_de.get(w, [])[:TOPE_DIAS_A_CEDER]:
                    if g != f:
                        agregar(w, g)

    nivel = 1
    while frontera and nivel < saltos:
        siguiente: set[tuple[str, date]] = set()
        for (w, f) in frontera:
            s0 = celdas[(w, f)]
            for c in datos.trabajadores:
                if c == w or not datos.elegible(c, s0, f)[0] or libre_o_refuerzo(c, f):
                    continue
                clave = (c, f)
                if clave not in celdas:
                    celdas[clave] = plan[clave]
                    siguiente.add(clave)
        frontera = siguiente
        nivel += 1

    # -- 2. Variables: primero las celdas, luego los huecos -------------------- #
    modelo = cp_model.CpModel()
    occ: dict[tuple[tuple[str, date], str], cp_model.IntVar] = {}
    z: dict[tuple[str, date], cp_model.IntVar] = {}
    por_dia: dict[tuple[str, date], list] = defaultdict(list)
    nuevas_de: dict[str, list[tuple[date, cp_model.IntVar]]] = defaultdict(list)

    def z_var(w: str, f: date) -> cp_model.IntVar:
        return z.setdefault((w, f), modelo.NewBoolVar(f"z_{w}_{f:%m%d}"))

    for (w0, f), s0 in celdas.items():
        quedarse = modelo.NewBoolVar(f"occ_{w0}_{f:%m%d}_{w0}")
        occ[((w0, f), w0)] = quedarse
        opciones = [quedarse]
        por_dia[(w0, f)].append(quedarse)
        for c in datos.trabajadores:
            if c == w0 or not datos.elegible(c, s0, f)[0]:
                continue
            if libre_o_refuerzo(c, f) and legal_para(c, f, s0):
                var = modelo.NewBoolVar(f"occ_{w0}_{f:%m%d}_{c}")
                occ[((w0, f), c)] = var
                opciones.append(var)
                por_dia[(c, f)].append(var)
                nuevas_de[c].append((f, var))
                if es_refuerzo(c, f):
                    modelo.Add(z_var(c, f) >= var)
        modelo.AddExactlyOne(opciones)

    y: dict[tuple[int, str], cp_model.IntVar] = {}
    candidatos_de: dict[int, list[str]] = defaultdict(list)
    for h_idx, (s, f) in enumerate(pendientes):
        for w in datos.trabajadores:
            if not datos.elegible(w, s, f)[0]:
                continue
            en_celda = (w, f) in celdas
            if not (libre_o_refuerzo(w, f) or en_celda):
                continue
            if not legal_para(w, f, s):
                continue
            var = modelo.NewBoolVar(f"y_{h_idx}_{w}")
            y[(h_idx, w)] = var
            candidatos_de[h_idx].append(w)
            por_dia[(w, f)].append(var)
            nuevas_de[w].append((f, var))
            if en_celda:
                modelo.Add(occ[((w, f), w)] + var <= 1)     # si cubre el hueco, no se queda
            elif es_refuerzo(w, f):
                modelo.Add(z_var(w, f) >= var)

    if not candidatos_de:
        return 0

    for h_idx, ws in candidatos_de.items():                # como mucho un cubridor por hueco
        modelo.AddAtMostOne(y[(h_idx, w)] for w in ws)

    for vs in por_dia.values():                            # nadie hace dos cosas el mismo día
        if len(vs) > 1:
            modelo.AddAtMostOne(vs)

    for w, entradas in nuevas_de.items():                  # interacción legal entre altas nuevas
        for i in range(len(entradas)):
            f1, v1 = entradas[i]
            for f2, v2 in entradas[i + 1:]:
                if lunes_de(f1) == lunes_de(f2) or abs((f1 - f2).days) <= 1:
                    modelo.AddAtMostOne([v1, v2])

    personas = {w for (w, _) in por_dia}
    for w in personas:                                     # presupuesto de jornada anual
        entra = [_decimas(datos.turnos[pendientes[h_idx][0]].horas) * var
                for (h_idx, ww), var in y.items() if ww == w]
        sale = []
        for (celda, cand), var in occ.items():
            w0, _ = celda
            if cand == w and cand == w0:
                sale.append(_decimas(datos.turnos[celdas[celda]].horas) * (1 - var))
            elif cand == w:
                entra.append(_decimas(datos.turnos[celdas[celda]].horas) * var)
        for (ww, g), var in z.items():
            if ww == w:
                sale.append(_decimas(datos.turnos[plan[(ww, g)]].horas) * var)
        modelo.Add(_decimas(libro.horas(w)) + sum(entra) - sum(sale)
                  <= _decimas(libro.objetivo(w)))

    # -- 3. Resolver: primero maximizar cobertura, luego minimizar el gasto --- #
    modelo.Maximize(sum(y.values()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = segundos
    solver.parameters.num_search_workers = hilos
    estado = solver.Solve(modelo)
    print(f"  canje nivel 1 (cobertura): {solver.ObjectiveValue():.0f}/{len(pendientes)}  "
          f"({solver.StatusName(estado)}, {solver.WallTime():.0f}s)")
    if estado not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return 0
    cerrados = int(round(solver.ObjectiveValue()))
    if cerrados == 0:
        return 0

    modelo.Add(sum(y.values()) >= cerrados)
    gasto = sum(z.values()) + sum(var for (celda, cand), var in occ.items() if cand != celda[0])
    modelo.Minimize(gasto)
    for var in list(y.values()) + list(z.values()) + list(occ.values()):
        modelo.AddHint(var, solver.Value(var))
    solver2 = cp_model.CpSolver()
    solver2.parameters.max_time_in_seconds = segundos
    solver2.parameters.num_search_workers = hilos
    estado2 = solver2.Solve(modelo)
    print(f"  canje nivel 2 (gasto): {solver2.ObjectiveValue():.0f}  "
          f"({solver2.StatusName(estado2)}, {solver2.WallTime():.0f}s)")
    sol = solver2 if estado2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else solver

    # -- 4. Aplicar: celdas primero, luego huecos, luego refuerzos sueltos ---- #
    procesados: set[tuple[str, date]] = set()

    for (w0, f), s0 in celdas.items():
        ganador = w0
        for c in datos.trabajadores:
            var = occ.get(((w0, f), c))
            if var is not None and sol.Value(var):
                ganador = c
                break
        if ganador == w0:
            continue
        libro.borra(w0, plan.pop((w0, f)))
        procesados.add((w0, f))
        if (ganador, f) in plan:
            libro.borra(ganador, plan.pop((ganador, f)))
        plan[(ganador, f)] = s0
        libro.apunta(ganador, s0)
        procesados.add((ganador, f))

    aplicados = 0
    for (h_idx, w), var in y.items():
        if not sol.Value(var):
            continue
        s, f = pendientes[h_idx]
        if (w, f) in plan:
            libro.borra(w, plan.pop((w, f)))
        plan[(w, f)] = s
        libro.apunta(w, s)
        procesados.add((w, f))
        aplicados += 1

    for (w, g), var in z.items():
        if (w, g) not in procesados and sol.Value(var) and (w, g) in plan:
            libro.borra(w, plan.pop((w, g)))
            procesados.add((w, g))

    return aplicados


# --------------------------------------------------------------------------- #
#  Relleno de horas con REF CAL  (post-proceso, FUERA del modelo)
# --------------------------------------------------------------------------- #
def _uniformes(dias: list[date], cuantos: int) -> list[date]:
    """`cuantos` días repartidos lo más uniformemente posible a lo largo de la lista. Es lo que
    hace que el relleno de refuerzos caiga espaciado por el año y no en bloque."""
    if cuantos <= 0 or not dias:
        return []
    if cuantos >= len(dias):
        return list(dias)
    paso = len(dias) / cuantos
    return [dias[min(len(dias) - 1, int(i * paso + paso / 2))] for i in range(cuantos)]


def rellenar_refuerzos(datos: Datos, plan: Plan, libro: LibroHoras) -> int:
    """Acerca a cada correturno a su jornada anual con las líneas de refuerzo, REPARTIDAS.

    REF CAL no es cobertura: declara demanda 0, así que nunca es un hueco y nadie la echa de menos.
    Es una herramienta para asignar horas de apoyo a quien, después de repartir todo lo que había
    que cubrir, se queda por debajo de su jornada. Por eso va al final y fuera del modelo.

    El reparto por el año no es cosmético. Recorriendo las fechas en orden, todo el relleno caía en
    enero y febrero y el pool llegaba a octubre sin presupuesto. Las horas son las mismas, pero
    repartidas dejan una carga pareja todo el año, que es como se entrega un cuadrante.
    """
    refuerzos = sorted(s for s, t in datos.turnos.items() if t.dem == 0)
    if not refuerzos:
        return 0

    puestas = 0
    for w in pool_correturno(datos):
        # Se reparte solo entre los días en que un refuerzo EXISTE: REF CAL opera de lunes a
        # viernes, así que repartir sobre todos los días libres tiraba la mitad de las elecciones
        # a fines de semana y el resto acababa completándose desde enero en orden de fecha.
        while libro.exceso(w) < 0:
            libres = [f for f in datos.lista_dias_calendario
                      if (w, f) not in plan and datos.disponible(w, f)
                      and any(datos.elegible(w, s, f)[0] for s in refuerzos)]
            if not libres:
                break
            faltan = int(-libro.exceso(w) // min(datos.turnos[s].horas for s in refuerzos)) + 1
            colocado = False
            for f in _uniformes(libres, min(faltan, len(libres))):
                if libro.exceso(w) >= 0:
                    break
                for s in refuerzos:
                    # `legal.permite` es obligatorio: esto NO hereda ningún patrón, se inventa un
                    # día de trabajo donde no había nada, así que la secuencia que crea con lo de
                    # alrededor hay que comprobarla entera.
                    if (datos.elegible(w, s, f)[0] and libro.cabe(w, s)
                            and legal.permite(datos, plan, w, f, s)):
                        plan[(w, f)] = s
                        if not legal.descanso_finde_ok(datos, plan, w, lunes_de(f)):
                            del plan[(w, f)]
                            break                       # falla igual para cualquier otro s ese f
                        libro.apunta(w, s)
                        puestas += 1
                        colocado = True
                        break
            if not colocado:
                break                                   # ninguno de los repartidos cabe: se deja
    return puestas


# --------------------------------------------------------------------------- #
#  Informe
# --------------------------------------------------------------------------- #
def resumen(datos: Datos, plan: Plan, libro: LibroHoras) -> None:
    pendientes = huecos(datos, plan)
    demanda = sum(t.dem for s, t in datos.turnos.items() for f in datos.lista_dias_calendario
                  if datos.opera(s, f) and t.dem > 0)
    print(f"\nPASO D — cobertura final: {demanda - len(pendientes)}/{demanda} "
          f"({(demanda - len(pendientes)) / demanda:.1%}), {len(pendientes)} huecos")
    por_linea = Counter(s for s, _ in pendientes)
    if por_linea:
        print("  líneas con más huecos: "
              + " · ".join(f"{s}:{n}" for s, n in por_linea.most_common(6)))

    dias: dict[str, Counter] = defaultdict(Counter)
    for (w, f), s in plan.items():
        dias[w][datos.tipo_dia(f, datos.turnos[s].municipio)] += 1

    for etiqueta, gente in (("correturno", pool_correturno(datos)),
                            ("fijo c/finde", mixtos_con_finde(datos))):
        if not gente:
            continue
        print(f"\n{etiqueta:<13} {'dias':>5} {'horas':>7} {'sab':>5} {'dom':>5} {'fest':>5}")
        print("-" * 48)
        for w in gente:
            print(f"{w:<13} {sum(dias[w].values()):>5} {libro.horas(w):>7.0f} "
                  f"{dias[w]['SAB']:>5} {dias[w]['DOM']:>5} {dias[w]['FEST']:>5}")
        print("-" * 48)

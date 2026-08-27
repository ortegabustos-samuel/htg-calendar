"""
residuo.py — Paso D: un solo CP-SAT anual que reparte los huecos entre los correturnos.

Los pasos A, A2, B y C dejan el cuadrante casi entero decidido: patrones rotados, mixtos en su
línea con su cuota de findes, plazas designadas traspasadas y la forma semanal del pool fijada.
Lo que queda son ~2.355 plazas sin cubrir y 11 correturnos con el año entero por delante.

Eso es un problema pequeño y limpio, y por dos razones se resuelve de una vez para el año completo
en vez de por ventanas:

  * Los correturnos llegan aquí SIN NINGUNA asignación. No hay que arrastrar el estado de lo ya
    resuelto: no interactúan con celdas fijas en el descanso entre turnos, ni en el tope de días
    por semana, ni en las horas. Su subproblema es independiente.
  * Son ~23.600 variables booleanas para el año completo, que CP-SAT digiere sin dificultad. Ver
    enero y agosto a la vez es justo lo que el horizonte rodante del motor anterior nunca pudo.

El objetivo es LEXICOGRÁFICO, no una suma ponderada: se resuelve un nivel, se clava el resultado
como restricción (`obj <= mejor`) y se pasa al siguiente. Así un nivel no puede comprarle nada al
de encima por muchos puntos que gane.

Cada nivel arranca SEMBRADO con la solución del anterior (`AddHint`). No es un detalle de
rendimiento: con la cobertura clavada en su óptimo, la región factible es durísima de encontrar
desde cero — el nivel de equidad se quedaba en UNKNOWN una y otra vez — y la solución del nivel
previo ya está dentro de ella, así que el solver empieza con un incumbente válido en vez de
buscarlo.

  1. COBERTURA      — cuántas plazas quedan sin cubrir. Manda sobre todo lo demás.
  2. LOCALIZADOS    — cuántas veces se aprovecha que una guardia de localización no ocupa el día
                      siguiente. Es legal (no es presencia, es disponibilidad) pero el descanso
                      debe respetarse salvo que sea la única manera de cubrir algo que si no
                      quedaría vacío. Por eso va como COSTE justo debajo de la cobertura: entre dos
                      soluciones que cubren lo mismo, siempre gana la que respeta el descanso.
  3. EQUIDAD        — sábados, domingos y festivos de cada correturno contra la referencia del
                      grupo grande de su municipio (la misma que se les aplicó a los mixtos), MÁS
                      el rango dentro del pool. Sin el rango la métrica es ciega al reparto del
                      exceso —le da igual dos personas con 19 domingos que cuatro con 12— y además
                      deja al nivel 3 reordenar libremente dentro del empate.
  4. FORMA SEMANAL  — el precio de sacar a alguien de la franja y zona que le fijó el paso C. Es
                      preferencia, no restricción: si con ello se tapa un hueco que si no quedaría
                      vacío, el nivel 1 ya se lo ha llevado.

**Canje de refuerzos.** Además de repartir huecos entre correturnos, el modelo puede cambiarle a
cualquiera un REF CAL que ya tenga asignado por una plaza real sin cubrir de ese mismo día. Los
patrones prescriben 668 refuerzos al año, y un refuerzo declara demanda 0: no lo echa de menos
nadie. El canje es neutro en horas y no hace trabajar a quien no iba a trabajar — solo cambia el
qué. Entra como variable y no como regla posterior para que sea el solver quien elija el reparto,
respetando descanso, días por semana y jornada.

**El día que suelta el mixto.** Para hacer un fin de semana, el paso A2 le quita a cada mixto un
día de esa semana entre semana. Cuál se le quita no lo puede decidir A2 —allí los correturnos
todavía no están colocados—, así que llega marcado como flexible y se decide aquí: se liberan todos
los días L-V de esa semana y se obliga a que trabaje todos menos uno. Así el modelo puede soltar el
martes en vez del lunes porque ve que el martes hay un correturno en refuerzo que tapará el hueco,
y el lunes no. Es neutro en horas y en días trabajados.

El relleno con REF CAL va después y aparte (`rellenar_refuerzos`): no es cobertura —esas dos líneas
declaran demanda 0— sino horas de apoyo para acercar a cada uno a su jornada anual, así que no
tiene nada que hacer dentro de un objetivo que optimiza cobertura.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from ortools.sat.python import cp_model

import base, forma, legal
import ritmo as ritmo_mod
from cargar_datos import DIAS_LV, Datos
from horas import EPS, LibroHoras
from ritmo import Ritmo

Plan = dict[tuple[str, date], str]
DECIMAS = 10                    # las horas son floats; el modelo trabaja en décimas de hora


def _decimas(h: float) -> int:
    return int(round(h * DECIMAS))


def _pares_incompatibles(datos: Datos, lineas: list[str]) -> tuple[set, set]:
    """Pares (línea de hoy, línea de mañana) que no respetan el descanso mínimo, separados en los
    que se prohíben y los que solo se PENALIZAN por haber un localizado de por medio. Se calculan
    una vez: dependen solo del reloj de cada turno, así que son los mismos todos los días."""
    minimo = timedelta(hours=datos.config.descanso_minimo)
    base = date(2001, 1, 1)
    duros, exentos = set(), set()
    for a in lineas:
        fin = datos.intervalo(a, base)[1]
        for b in lineas:
            if datos.intervalo(b, base + timedelta(days=1))[0] - fin < minimo:
                if datos.localizado(a) or datos.localizado(b):
                    exentos.add((a, b))
                else:
                    duros.add((a, b))
    return duros, exentos


def resolver(datos: Datos, plan: Plan, libro: LibroHoras, rep: forma.Reparto,
             flexibles: list[tuple[str, date, date, str]] | None = None,
             segundos: int = 120, hilos: int = 8, log: bool = False,
             nivel2: bool = True) -> dict:
    pool = forma.pool_de(datos)

    # Las semanas flexibles de los mixtos: se sueltan TODOS sus días L-V de esa semana para que el
    # modelo elija cuál se queda sin cubrir. Se hace antes de contar huecos, así que esos días
    # entran en el reparto como cualquier otra plaza.
    # Se agrupan por semana ANTES de tocar nada: un mixto puede tener sábado Y domingo en la misma
    # semana ISO, y entonces A2 le suelta DOS días. Tratar cada suelta por separado dejaría fuera
    # los días que la primera ya había liberado, y el mixto no volvería a recuperarlos.
    sueltos: dict[tuple[str, date], list[tuple[date, str]]] = defaultdict(list)
    for trab, lunes, dia_out, linea_out in (flexibles or ()):
        sueltos[(trab, lunes)].append((dia_out, linea_out))

    flex: dict[tuple[str, date], tuple[list[tuple[date, str]], int]] = {}
    for (trab, lunes), fuera_ya in sueltos.items():
        candidatos = list(fuera_ya)
        ocupados = {g for g, _ in fuera_ya}
        for i in range(5):
            g = lunes + timedelta(days=i)
            if g not in ocupados and (trab, g) in plan:
                candidatos.append((g, plan[(trab, g)]))
        for g, s in candidatos:
            if (trab, g) in plan:
                libro.borra(trab, plan.pop((trab, g)))
        # Debe seguir soltando tantos días como findes hizo esa semana, ni uno más.
        flex[(trab, lunes)] = (sorted(candidatos), len(candidatos) - len(fuera_ya))

    faltan = Counter(forma.huecos(datos, plan))
    lineas = sorted({s for s, _ in faltan})

    modelo = cp_model.CpModel()
    x: dict[tuple[str, date, str], cp_model.IntVar] = {}
    for (s, f) in faltan:
        for w in pool:
            if (w, f) not in plan and datos.elegible(w, s, f)[0]:
                x[(w, f, s)] = modelo.NewBoolVar(f"x_{w}_{f:%m%d}_{s}")

    # Canje: un REF CAL ya asignado puede convertirse en una plaza real del mismo día.
    lineas_dia: dict[date, list[str]] = defaultdict(list)
    for (s, f) in faltan:
        lineas_dia[f].append(s)
    laxas: list = []                                              # solo posibles por la exención
    y: dict[tuple[str, date, str], cp_model.IntVar] = {}
    refuerzos_en_plan = {(w, f): s for (w, f), s in plan.items() if datos.turnos[s].dem == 0}
    for (w, f), previo in refuerzos_en_plan.items():
        plan.pop((w, f))                                          # se evalúa el día ya libre
        for s in lineas_dia.get(f, ()):
            if datos.elegible(w, s, f)[0] and legal.permite(datos, plan, w, f, s, True):
                y[(w, f, s)] = modelo.NewBoolVar(f"y_{w}_{f:%m%d}_{s}")
                if not legal.permite(datos, plan, w, f, s):
                    laxas.append((w, f, s, "y"))
        plan[(w, f)] = previo

    # El mixto vuelve a coger todos sus días de la semana flexible menos uno.
    z: dict[tuple[str, date, str], cp_model.IntVar] = {}
    for (trab, lunes), (candidatos, cuantos) in flex.items():
        # Solo los días que siguen siendo legales contra lo que le rodea. Hace falta comprobarlo
        # aquí: el modelo puede DEVOLVERLE un día que el paso A2 le había quitado, y ese día queda
        # pegado a un finde de cuota de la semana anterior — un par que nadie había mirado.
        suyas = []
        for g, s in candidatos:
            if not legal.permite(datos, plan, trab, g, s, True):
                continue
            z[(trab, g, s)] = modelo.NewBoolVar(f"z_{trab}_{g:%m%d}")
            if not legal.permite(datos, plan, trab, g, s):
                laxas.append((trab, g, s, "z"))
            suyas.append(z[(trab, g, s)])
        if suyas:
            modelo.Add(sum(suyas) == min(cuantos, len(suyas)))
        # C4 entre dos días consecutivos que el modelo puede devolverle a la vez.
        for g, a in candidatos:
            for k, b in candidatos:
                if ((k - g).days == 1 and (trab, g, a) in z and (trab, k, b) in z
                        and (a, b) in _pares_incompatibles(datos, [a, b])[0]):
                    modelo.AddAtMostOne([z[(trab, g, a)], z[(trab, k, b)]])

        dias_semana_trab = [lunes + timedelta(days=i) for i in range(7)]
        sab_t, dom_t = dias_semana_trab[5], dias_semana_trab[6]
        if (trab, sab_t) in plan and (trab, dom_t) in plan:
            # Sábado y domingo ya están fijados en plan (decididos en el paso A2, invariables
            # aquí) — igual que en el bloque de la Task 3, exige un par consecutivo libre entre
            # semana. z solo cubre los días candidatos; los demás días L-V de esta semana están
            # fijados fuera del modelo, así que su libre/trabajado es una constante, no una
            # variable.
            candidatos_por_dia = {g: s for g, s in candidatos}
            libres_l_v = []
            for i in range(5):
                dia = dias_semana_trab[i]
                s_dia = candidatos_por_dia.get(dia)
                if s_dia is not None and (trab, dia, s_dia) in z:
                    libre = modelo.NewBoolVar(f"librefinde_z_{trab}_{dia:%m%d}")
                    modelo.Add(z[(trab, dia, s_dia)] + libre == 1)
                else:
                    libre = 0 if (trab, dia) in plan else 1
                libres_l_v.append(libre)

            fijos = datos.config.dias_descanso_finde
            if fijos:
                idx = {nombre: i for i, nombre in enumerate(DIAS_LV)}
                i1, i2 = sorted(idx[n] for n in fijos)
                modelo.Add(libres_l_v[i1] + libres_l_v[i2] >= 2)
            else:
                pares_z = []
                for i in range(4):
                    a, b = libres_l_v[i], libres_l_v[i + 1]
                    if isinstance(a, int) and isinstance(b, int):
                        pares_z.append(1 if a and b else 0)
                        continue
                    par = modelo.NewBoolVar(f"parfinde_z_{trab}_{dias_semana_trab[i]:%m%d}")
                    modelo.Add(par <= a)
                    modelo.Add(par <= b)
                    pares_z.append(par)
                modelo.Add(sum(pares_z) >= 1)

    por_dia: dict[tuple[str, date], list] = defaultdict(list)
    por_plaza: dict[tuple[str, date], list] = defaultdict(list)
    por_trab: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for (w, f, s) in x:
        por_dia[(w, f)].append(x[(w, f, s)])
        por_plaza[(s, f)].append(x[(w, f, s)])
        por_trab[w].append((f, s))

    for (trab, g, s) in z:                                        # cuentan como cobertura real
        por_plaza[(s, g)].append(z[(trab, g, s)])

    dias_z: dict[str, dict[date, list]] = defaultdict(lambda: defaultdict(list))
    for (trab, g, s) in z:
        dias_z[trab][g].append((datos.turnos[s].horas, z[(trab, g, s)]))
    for trab, porf in dias_z.items():
        modelo.Add(sum(_decimas(h) * v for dia in porf.values() for h, v in dia)
                   <= _decimas(libro.objetivo(trab) - libro.horas(trab)))
        # C5 y C6 sobre los días que el modelo puede devolverle. Comprobar la legalidad al crear
        # cada variable no basta: entonces la semana estaba vacía y todas pasaban una a una, pero
        # el modelo puede encenderlas VARIAS a la vez y plantarlo en 56 h. Lo ya fijado del propio
        # trabajador entra como constante.
        for g in sorted(porf):
            lunes = forma.lunes_de(g)
            fijos_semana = sum(1 for i in range(7) if (trab, lunes + timedelta(days=i)) in plan)
            libres_semana = [v for k, dia in porf.items() if forma.lunes_de(k) == lunes
                             for _, v in dia]
            modelo.Add(sum(libres_semana) <= datos.config.dias_max_semana - fijos_semana)

            for arranque in range(-6, 1):
                ini = g + timedelta(days=arranque)
                fijas = sum(datos.turnos[plan[(trab, ini + timedelta(days=i))]].horas
                            for i in range(7) if (trab, ini + timedelta(days=i)) in plan)
                ventana = [_decimas(h) * v for k, dia in porf.items()
                           if 0 <= (k - ini).days < 7 for h, v in dia]
                if ventana:
                    modelo.Add(sum(ventana)
                               <= _decimas(datos.config.horas_max_semana - fijas))

    canje_dia: dict[tuple[str, date], list] = defaultdict(list)
    canje_trab: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for (w, f, s) in y:
        por_plaza[(s, f)].append(y[(w, f, s)])
        canje_dia[(w, f)].append(y[(w, f, s)])
        canje_trab[w].append((f, s))

    for vs in por_dia.values():                                   # C2 — un turno por día
        modelo.AddAtMostOne(vs)
    for vs in canje_dia.values():                                 # un refuerzo se canjea una vez
        modelo.AddAtMostOne(vs)
    for clave, vs in por_plaza.items():                           # no cubrir más de lo que falta
        modelo.Add(sum(vs) <= faltan[clave])

    for w, dias in canje_trab.items():
        # El canje cambia las horas del día: el refuerzo son 8 h y la plaza real puede ser 7 o 9.
        previo = {f: datos.turnos[plan[(w, f)]].horas for f, _ in dias}
        modelo.Add(sum(_decimas(datos.turnos[s].horas - previo[f]) * y[(w, f, s)] for f, s in dias)
                   <= _decimas(libro.objetivo(w) - libro.horas(w)))
        # C4 entre dos canjes de días consecutivos: la comprobación previa se hizo contra el
        # refuerzo del vecino, que puede haber cambiado también.
        for f, a in dias:
            for g, b in dias:
                if (g - f).days == 1 and (a, b) in _pares_incompatibles(datos, [a, b])[0]:
                    modelo.AddAtMostOne([y[(w, f, a)], y[(w, g, b)]])

    duros, exentos = _pares_incompatibles(datos, lineas)
    exenciones: list = []                                         # se cuentan en el nivel 2
    for w in pool:                                                # C4 — descanso entre jornadas
        suyos: dict[date, list[str]] = defaultdict(list)
        for f, s in por_trab[w]:
            suyos[f].append(s)
        for f in sorted(suyos):
            for a in suyos[f]:
                for b in suyos.get(f + timedelta(days=1), ()):
                    va, vb = x[(w, f, a)], x[(w, f + timedelta(days=1), b)]
                    if (a, b) in duros:
                        modelo.AddAtMostOne([va, vb])
                    elif (a, b) in exentos:
                        # Permitido, pero contado: `e` se activa si se usan los dos, y el nivel 2
                        # lo minimiza. No hace falta forzar e=0 cuando no: se está minimizando.
                        e = modelo.NewBoolVar(f"loc_{w}_{f:%m%d}")
                        modelo.Add(va + vb - 1 <= e)
                        exenciones.append(e)

    for w in pool:                          # domingo sin sábado: restricción dura, nunca se paga
        # "sábado trabajado" se mide solo con variables `x` (de decisión), nunca mirando
        # `(w, f-1) in plan` directamente. Es seguro hoy porque ningún correturno llega al paso D
        # con un fin de semana ya fijado en el plan (no hay correturno cubridor designado, y
        # `forma.repartir` no asigna turnos antes de este punto) — si esa invariante cambia, esta
        # restricción tendría que contar también los sábados fijados fuera del modelo.
        vars_por_fecha: dict[date, list] = defaultdict(list)
        for f, s in por_trab[w]:
            vars_por_fecha[f].append(x[(w, f, s)])
        dias_domingo = {f for f, s in por_trab[w]
                        if datos.tipo_dia(f, datos.turnos[s].municipio) == "DOM"}
        for f in dias_domingo:
            dia_anterior = vars_por_fecha.get(f - timedelta(days=1), [])
            modelo.Add(sum(vars_por_fecha[f]) <= sum(dia_anterior))

        # descanso consecutivo tras un finde completo: restricción dura, mismo nivel que arriba
        for lunes in {forma.lunes_de(f) for f, s in por_trab[w]}:
            dias_semana = [lunes + timedelta(days=i) for i in range(7)]
            sab_vars = vars_por_fecha.get(dias_semana[5], [])
            dom_vars = vars_por_fecha.get(dias_semana[6], [])
            if not sab_vars or not dom_vars:
                continue                                    # esta semana no tiene ambos en juego
            ambos_finde = modelo.NewBoolVar(f"ambosfinde_{w}_{lunes:%m%d}")
            modelo.Add(ambos_finde >= sum(sab_vars) + sum(dom_vars) - 1)

            libres = []
            for i in range(5):
                dia_vars = vars_por_fecha.get(dias_semana[i], [])
                libre = modelo.NewBoolVar(f"librefinde_{w}_{dias_semana[i]:%m%d}")
                modelo.Add(sum(dia_vars) + libre == 1)       # C2 ya garantiza como mucho 1 turno/día
                libres.append(libre)

            fijos = datos.config.dias_descanso_finde
            if fijos:
                idx = {nombre: i for i, nombre in enumerate(DIAS_LV)}
                i1, i2 = sorted(idx[n] for n in fijos)
                modelo.Add(libres[i1] + libres[i2] >= 2 * ambos_finde)
            else:
                pares = []
                for i in range(4):
                    par = modelo.NewBoolVar(f"parfinde_{w}_{dias_semana[i]:%m%d}")
                    modelo.Add(par <= libres[i])
                    modelo.Add(par <= libres[i + 1])
                    pares.append(par)
                modelo.Add(sum(pares) >= ambos_finde)

    for w in pool:
        # C9 — jornada anual. Los correturnos llegan a cero, así que su presupuesto es entero.
        modelo.Add(sum(_decimas(datos.turnos[s].horas) * x[(w, f, s)] for f, s in por_trab[w])
                   <= _decimas(libro.objetivo(w) - libro.horas(w)))

        semanas: dict[date, list] = defaultdict(list)
        horas_semana: dict[date, list] = defaultdict(list)
        for f, s in por_trab[w]:
            lunes = forma.lunes_de(f)
            semanas[lunes].append(x[(w, f, s)])
            horas_semana[lunes].append(_decimas(datos.turnos[s].horas) * x[(w, f, s)])
        for vs in semanas.values():                               # C5 — días por semana ISO
            modelo.Add(sum(vs) <= datos.config.dias_max_semana)
        for vs in horas_semana.values():                          # C6 — horas por semana ISO
            modelo.Add(sum(vs) <= _decimas(datos.config.horas_max_semana))

    # -- Nivel 1: cobertura --------------------------------------------------- #
    cubiertas = sum(x.values()) + sum(y.values()) + sum(z.values())
    mejor, solucion = _optimizar(modelo, cubiertas, True, segundos, hilos, log, "1 cobertura")
    if mejor is None:
        print("  *** el modelo del paso D no encontró solución ***")
        return {}
    modelo.Add(cubiertas >= mejor)

    # -- Nivel 2: apoyarse lo menos posible en el localizado -------------------- #
    exenciones += [v for (w, f, s, cual) in laxas for v in [(y if cual == "y" else z)[(w, f, s)]]]
    if exenciones:
        total_loc = sum(exenciones)
        _sembrar(modelo, {**x, **y, **z}, solucion)
        mejor_loc, sl = _optimizar(modelo, total_loc, False, segundos, hilos, log,
                                   "2 apoyos en localizado")
        if mejor_loc is not None:
            modelo.Add(total_loc <= mejor_loc)
            solucion = sl

    # -- Nivel 3: equidad de sábados, domingos y festivos --------------------- #
    refs = base.referencia_finde(datos, plan)
    desvios = []
    for clase in base.FINDE:
        cuentas = []
        for w in pool:
            muni = Counter(datos.turnos[s].municipio for _, s in por_trab[w]).most_common(1)
            objetivo = (refs.get(muni[0][0], Counter()) if muni else Counter()).get(clase, 0)
            suyos = [x[(w, f, s)] for f, s in por_trab[w]
                     if datos.tipo_dia(f, datos.turnos[s].municipio) == clase]
            if not suyos:
                continue
            n = modelo.NewIntVar(0, 400, f"n_{w}_{clase}")
            modelo.Add(n == sum(suyos))
            cuentas.append(n)
            d = modelo.NewIntVar(0, 400, f"dev_{w}_{clase}")
            modelo.Add(d >= n - objetivo)
            modelo.Add(d >= objetivo - n)
            desvios.append(d)
        if len(cuentas) > 1:
            # El RANGO dentro del pool, además de la desviación contra la referencia.
            alto = modelo.NewIntVar(0, 400, f"max_{clase}")
            bajo = modelo.NewIntVar(0, 400, f"min_{clase}")
            modelo.AddMaxEquality(alto, cuentas)
            modelo.AddMinEquality(bajo, cuentas)
            desvios.append(alto - bajo)
    if desvios and nivel2:
        total_desvio = sum(desvios)
        _sembrar(modelo, {**x, **y, **z}, solucion)
        mejor2, s2 = _optimizar(modelo, total_desvio, False, segundos, hilos, log,
                                "3 equidad de findes")
        if mejor2 is not None:
            modelo.Add(total_desvio <= mejor2)
            solucion = s2

    # -- Nivel 4: respetar la forma semanal ----------------------------------- #
    fuera = [x[(w, f, s)] for (w, f, s) in x
             if rep.forma.get((w, forma.lunes_de(f))) != forma.bloque_de(datos, rep.zonas, s)]
    if fuera:
        _sembrar(modelo, {**x, **y, **z}, solucion)
        _, s3 = _optimizar(modelo, sum(fuera), False, segundos, hilos, log, "4 forma semanal")
        if s3 is not None:
            solucion = s3

    return _volcar(datos, plan, libro, x, y, z, solucion)


def _sembrar(modelo, variables: dict, solver) -> None:
    """Le pasa al modelo la solución del nivel anterior como punto de partida."""
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


def _volcar(datos: Datos, plan: Plan, libro: LibroHoras, x: dict, y: dict, z: dict,
            solver) -> dict:
    if solver is None:
        return {}
    puestas = canjes = 0
    for (w, f, s), var in x.items():
        if solver.Value(var):
            plan[(w, f)] = s
            libro.apunta(w, s)
            puestas += 1
    for (w, f, s), var in z.items():
        if solver.Value(var):
            plan[(w, f)] = s
            libro.apunta(w, s)
    for (w, f, s), var in y.items():
        if solver.Value(var):
            libro.borra(w, plan[(w, f)])                          # sale el refuerzo
            plan[(w, f)] = s
            libro.apunta(w, s)
            canjes += 1
    print(f"  asignaciones nuevas: {puestas} · refuerzos canjeados por cobertura real: {canjes}")
    return {"asignadas": puestas, "canjes": canjes}


# --------------------------------------------------------------------------- #
#  Canje de refuerzos por cobertura real
# --------------------------------------------------------------------------- #
def canjear_refuerzos(datos: Datos, plan: Plan, libro: LibroHoras, ritmos: dict[str, Ritmo]) -> int:
    """Cierra huecos soltando un REF CAL del propio candidato en OTRA fecha del año.

    El caso típico: el 1 de enero falta `VADN006` y hay 31 personas libres, capacitadas y legales
    para hacerlo — pero todas están en 1776 h y se pasarían de jornada. No es un problema de
    plantilla ni de legalidad, es de presupuesto. Y una de ellas tiene, en marzo, un REF CAL: un
    turno de apoyo con demanda 0 que no cubre nada y que nadie echa de menos. Se le quita ese, y con
    esas horas cubre el turno real de enero.

    Sale gratis en todos los frentes: sus horas quedan igual, la cobertura sube en uno, y hay un
    refuerzo menos. Hace falta una pasada aparte porque el CP-SAT del paso D no puede verlo — solo
    tiene variables para los correturnos, y estos candidatos son trabajadores de patrón — y porque
    el REF CAL entra después del modelo, así que el intercambio "menos relleno por más cobertura"
    nunca llega a plantearse dentro.

    Corre DESPUÉS del relleno, y no es indiferente: no todos los refuerzos valen lo mismo. Los de un
    correturno los hemos puesto nosotros para completarle la jornada, mientras que los de un
    trabajador de patrón se los prescribe `patrones.csv` y forman parte de su rotación. Se prefiere
    gastar los primeros, así que los correturnos van delante en la lista de candidatos.
    """
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
    for (s, f) in forma.huecos(datos, plan):
        for w in candidatos:
            if (w, f) in plan or not datos.elegible(w, s, f)[0]:
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
            if (not ritmo_mod.es_rigido(datos, ritmos, w)
                    and not legal.descanso_finde_ok(datos, plan, w, forma.lunes_de(f))):
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


def canjear_en_cadena(datos: Datos, plan: Plan, libro: LibroHoras, ritmos: dict[str, Ritmo]) -> int:
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
    for (s, f) in forma.huecos(datos, plan):
        if _cadena(datos, plan, libro, refcal_dia, dias_de, s, f, ritmos):
            cerrados += 1
    return cerrados


def _cadena(datos: Datos, plan: Plan, libro: LibroHoras, refcal_dia: dict, dias_de: dict,
            s: str, f: date, ritmos: dict[str, Ritmo]) -> bool:
    for w in datos.trabajadores:
        if (w, f) in plan or not datos.elegible(w, s, f)[0]:
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
                if (not ritmo_mod.es_rigido(datos, ritmos, w)
                        and not legal.descanso_finde_ok(datos, plan, w, forma.lunes_de(f))):
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


# --------------------------------------------------------------------------- #
#  Relleno de horas con REF CAL
# --------------------------------------------------------------------------- #
def rellenar_refuerzos(datos: Datos, plan: Plan, libro: LibroHoras) -> int:
    """Acerca a cada correturno a su jornada anual con las líneas de refuerzo, REPARTIDAS.

    REF CAL no es cobertura: declara demanda 0, así que nunca es un hueco y nadie la echa de menos.
    Es una herramienta para asignar horas de apoyo a quien, después de repartir todo lo que había
    que cubrir, se queda por debajo de su jornada. Por eso va al final y fuera del modelo.

    El reparto por el año no es cosmético. Recorriendo las fechas en orden, todo el relleno caía en
    enero y febrero —254 días entre los dos— y el pool llegaba a octubre sin presupuesto: 211 días
    libres en diciembre frente a 71 en enero. Las horas son las mismas, pero repartidas dejan una
    carga pareja todo el año, que es como se entrega un cuadrante.
    """
    refuerzos = sorted(s for s, t in datos.turnos.items() if t.dem == 0)
    if not refuerzos:
        return 0

    puestas = 0
    for w in forma.pool_de(datos):
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
            for f in base._uniformes(libres, min(faltan, len(libres))):
                if libro.exceso(w) >= 0:
                    break
                for s in refuerzos:
                    # `legal.permite` es obligatorio: esto NO hereda ningún patrón, se inventa un
                    # día de trabajo donde no había nada, así que la secuencia que crea con lo de
                    # alrededor hay que comprobarla entera.
                    if (datos.elegible(w, s, f)[0] and libro.cabe(w, s)
                            and legal.permite(datos, plan, w, f, s)):
                        plan[(w, f)] = s
                        if not legal.descanso_finde_ok(datos, plan, w, forma.lunes_de(f)):
                            del plan[(w, f)]
                            break                       # falla igual para cualquier otro s ese f
                        libro.apunta(w, s)
                        puestas += 1
                        colocado = True
                        break
            if not colocado:
                break                                   # ninguno de los repartidos cabe: se deja
    return puestas


def resumen(datos: Datos, plan: Plan, libro: LibroHoras) -> None:
    pendientes = forma.huecos(datos, plan)
    demanda = sum(t.dem for s, t in datos.turnos.items() for f in datos.lista_dias_calendario
                  if datos.opera(s, f) and t.dem > 0)
    print(f"\nPASO D — cobertura final: {demanda - len(pendientes)}/{demanda} "
          f"({(demanda - len(pendientes)) / demanda:.1%}), {len(pendientes)} huecos")
    por_linea = Counter(s for s, _ in pendientes)
    if por_linea:
        print("  líneas con más huecos: "
              + " · ".join(f"{s}:{n}" for s, n in por_linea.most_common(6)))

    print(f"\n{'correturno':<12} {'dias':>5} {'horas':>7} {'sab':>5} {'dom':>5} {'fest':>5}")
    print("-" * 48)
    for w in forma.pool_de(datos):
        dias: Counter = Counter()
        for (ww, f), s in plan.items():
            if ww == w:
                dias[datos.tipo_dia(f, datos.turnos[s].municipio)] += 1
        print(f"{w:<12} {sum(dias.values()):>5} {libro.horas(w):>7.0f} {dias['SAB']:>5} "
              f"{dias['DOM']:>5} {dias['FEST']:>5}")
    print("-" * 48)

"""
modelo.py — Paso D: reparte los huecos de lunes a viernes entre los correturnos.

Cuando llega aqui, A, V, F y B han decidido todo lo demas: patrones rotados, fijos
en su linea, vacaciones cubiertas, findes repartidos y el exceso de horas cedido.
Queda UNA decision: que correturno cubre cada plaza L-V vacia.

Los findes NO se tocan. Los reparte el paso F, que ademas compensa soltando dias
entre semana. Lo que F no pudiera cubrir se queda vacio y se ve en el Excel.

El objetivo es LEXICOGRAFICO: se resuelve un nivel, se clava su valor como
restriccion y se pasa al siguiente. Asi un nivel no puede comprarle nada al de
encima — la equidad no le quita ni una plaza a la cobertura.

  1. COBERTURA   plazas L-V que quedan sin cubrir. Manda sobre todo lo demas.
  2. HORAS       que la demanda real se reparta por igual entre los correturnos.
                 Sin esto uno se lleva el ano entero y a otro hay que completarle
                 la jornada con refuerzos de calendario, que no cubren nada.
  3. FORMA       dentro de cada semana ISO, el mismo turno todos los dias; y si no
                 puede ser, al menos la misma franja. Preferencia, no restriccion.

Por debajo de los tres, como restriccion DURA: un turno por persona y dia, no
sobrecubrir, C4 (descanso entre jornadas), C5 (dias por semana ISO), C6 (horas por
semana ISO), la jornada anual y el descanso de fin de semana.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from ortools.sat.python import cp_model

import legal
from cargar_datos import DESCANSOS, turno_de

DECIMAS = 10        # las horas son floats; el modelo trabaja en decimas para no usar decimales
PESO_FRANJA = 10    # nivel 3: salirse de franja pesa 10 veces mas que salirse de turno


def lunes_de(fecha):
    """Lunes de la semana ISO en que cae esa fecha. Es la unidad de C5, C6 y la forma."""
    return fecha - timedelta(days=fecha.weekday())


def decimas(horas):
    """Las horas vienen como float del CSV; el modelo solo sabe de enteros."""
    return int(round(horas * DECIMAS))


# --------------------------------------------------------------------------- #
#  Que hay que repartir, entre quien, y con que variables
# --------------------------------------------------------------------------- #
def huecos_lv(datos, plan):
    """Turnos con demanda de lunes a viernes sin ser cubierta"""
    cubiertas = Counter()
    for (_, fecha), turno_id in plan.items():
        if turno_id in DESCANSOS:       # un DO ocupa el día, pero no cubre ninguna plaza
            continue
        cubiertas[(turno_id, fecha)] += 1

    faltan = []
    for turno_id, turno in datos.turnos.items():
        for fecha in datos.lista_dias_calendario:
            if not datos.opera(turno_id, fecha):
                continue
            if datos.tipo_dia(fecha, turno.municipio) != "LV":
                continue
            faltan += [(turno_id, fecha)] * max(0, turno.dem - cubiertas[(turno_id, fecha)])
    return faltan


def pool_correturnos(datos):
    "Obtenemos los trabajadores que van a ser repartidos los turnos"
    return [trabajador_id for trabajador_id, trabajador in datos.trabajadores.items()
            if trabajador.tipo == "correturno"]


def var_modelo(modelo, datos, plan, faltan, pool):
    """Variable por (trabajador_id, fecha, turno) 1 si trabaja en ese turno 0 si no
    Solo se permiten aquellas legales bajo las condiciones que tiene ya el plan en el momento de ejecutar
    """
    x = {}
    for (turno_id, fecha) in faltan:
        for trabajador_id in pool:
            if (trabajador_id, fecha) in plan:
                continue
            elegible, _ = datos.elegible(trabajador_id, turno_id, fecha)
            if not elegible:
                continue
            if not legal.descanso_ok(datos, plan, trabajador_id, fecha, turno_id, False):
                continue
            if not legal.dias_semana_ok(datos, plan, trabajador_id, fecha):
                continue
            if not legal.horas_semana_ok(datos, plan, trabajador_id, fecha, turno_id):
                continue
            x[(trabajador_id, fecha, turno_id)] = modelo.NewBoolVar(f"x_{trabajador_id}_{fecha:%m%d}_{turno_id}")
    return x


def indices(x):
    """Agrupa las variables de las tres formas en que las piden las restricciones.

    Se construyen una vez porque cada restriccion las necesita ordenadas de otra
    manera, y recorrer las 20.000 por cada una seria tirar el tiempo.
    """
    por_dia = defaultdict(list)      # (trabajador, fecha) -> sus candidatas de ese dia
    por_plaza = defaultdict(list)    # (turno, fecha)      -> quienes podrian hacerla
    por_trab = defaultdict(list)     # trabajador          -> [(fecha, turno), ...]
    for (trabajador_id, fecha, turno_id), var in x.items():
        por_dia[(trabajador_id, fecha)].append(var)
        por_plaza[(turno_id, fecha)].append(var)
        por_trab[trabajador_id].append((fecha, turno_id))
    for lista in por_trab.values():
        lista.sort()
    return por_dia, por_plaza, por_trab


def semanas_de(por_trab):
    """Por trabajador y semana ISO, sus candidatas de esa semana."""
    semanas = defaultdict(lambda: defaultdict(list))
    for trabajador_id, dias in por_trab.items():
        for fecha, turno_id in dias:
            semanas[trabajador_id][lunes_de(fecha)].append((fecha, turno_id))
    return semanas


# --------------------------------------------------------------------------- #
#  Las restricciones duras
# --------------------------------------------------------------------------- #
def restricciones_dia(modelo, faltan, por_dia, por_plaza):
    """Un turno por persona y dia, y no cubrir mas plazas de las que se piden."""
    cuantas = Counter(faltan)
    for candidatas in por_dia.values():
        modelo.AddAtMostOne(candidatas)                     # C2
    for plaza, candidatas in por_plaza.items():
        modelo.Add(sum(candidatas) <= cuantas[plaza])       # no sobrecubrir


def pares_incompatibles(datos, lineas):
    """Pares (linea de hoy, linea de manana) que no respetan el descanso minimo.

    Dependen solo del reloj de cada turno, asi que son los mismos todos los dias del
    ano y se calculan una vez. Es la regla de legal.descanso_ok, precompilada.

    Las guardias de localizado tendrian exencion —24 h de reloj que computan 8, no
    es presencia— pero ningun correturno declara capacidad para ninguna de las tres
    lineas de localizado, asi que ese caso no se da aqui y se prohibe todo igual.
    """
    minimo = timedelta(hours=datos.config.descanso_minimo)
    ancla = date(2001, 1, 1)
    malos = set()
    for a in lineas:
        fin = datos.intervalo(a, ancla)[1]
        for b in lineas:
            if datos.intervalo(b, ancla + timedelta(days=1))[0] - fin < minimo:
                malos.add((a, b))
    return malos


def restricciones_descanso(modelo, datos, x, por_trab, faltan):
    """C4 — descanso minimo entre el turno de un dia y el del siguiente.

    Solo hay que mirar dias consecutivos: si entre dos turnos hay un dia entero de
    por medio, el descanso se cumple solo.
    """
    malos = pares_incompatibles(datos, sorted({turno_id for turno_id, _ in faltan}))
    for trabajador_id, dias in por_trab.items():
        suyos = defaultdict(list)
        for fecha, turno_id in dias:
            suyos[fecha].append(turno_id)
        for fecha in sorted(suyos):
            manana = fecha + timedelta(days=1)
            for hoy in suyos[fecha]:
                for siguiente in suyos.get(manana, ()):
                    if (hoy, siguiente) in malos:
                        modelo.AddAtMostOne([x[(trabajador_id, fecha, hoy)],
                                             x[(trabajador_id, manana, siguiente)]])


def descanso_finde(modelo, plan, x, trabajador_id, lunes, dias):
    """Si esa semana trabaja sabado Y domingo, un par de dias seguidos libres entre semana.

    No es del convenio, es una regla de reparto: la misma que aplica el paso F al dar
    findes. Aqui hace falta porque los findes ya vienen puestos por F y son constantes,
    asi que sin esto el modelo le llenaria la semana entera alrededor.
    """
    fechas = [lunes + timedelta(days=i) for i in range(7)]
    if (trabajador_id, fechas[5]) not in plan or (trabajador_id, fechas[6]) not in plan:
        return                              # no tiene los dos: no hay nada que exigir

    candidatas = defaultdict(list)
    for fecha, turno_id in dias:
        candidatas[fecha].append(x[(trabajador_id, fecha, turno_id)])

    libres = []
    for fecha in fechas[:5]:
        if (trabajador_id, fecha) in plan:
            libres.append(0)                # dia ya fijado: ocupado seguro
        elif candidatas.get(fecha):
            libre = modelo.NewBoolVar(f"libre_{trabajador_id}_{fecha:%m%d}")
            modelo.Add(sum(candidatas[fecha]) + libre == 1)   # C2 ya garantiza como mucho uno
            libres.append(libre)
        else:
            libres.append(1)                # ni fijado ni candidato: libre seguro

    pares = []
    for i in range(4):
        a, b = libres[i], libres[i + 1]
        if isinstance(a, int) and isinstance(b, int):
            pares.append(a * b)
            continue
        par = modelo.NewBoolVar(f"par_{trabajador_id}_{fechas[i]:%m%d}")
        modelo.Add(par <= a)
        modelo.Add(par <= b)
        pares.append(par)

    if all(isinstance(p, int) for p in pares):
        return                              # esa semana no depende de nadie: ni exigirlo
    modelo.Add(sum(pares) >= 1)


def restricciones_semana(modelo, datos, plan, libro, x, pool, por_trab, semanas):
    """C5, C6, el descanso de fin de semana y la jornada anual.

    Los dias que ya tiene FIJOS en el plan entran como constante y le restan cupo:
    lo que puede anadir el modelo es lo que quede.
    """
    for trabajador_id in pool:
        for lunes, dias in sorted(semanas.get(trabajador_id, {}).items()):
            fechas = [lunes + timedelta(days=i) for i in range(7)]
            # Solo los turnos de verdad: un DO ocupa el día (y el solver no lo pisa, porque mira
            # `in plan`), pero ni gasta jornada ni cuenta para el tope de días de la semana.
            fijos = [s for s in (turno_de(plan, trabajador_id, g) for g in fechas) if s is not None]
            mios = [x[(trabajador_id, fecha, turno_id)] for fecha, turno_id in dias]

            modelo.Add(sum(mios) <= datos.config.dias_max_semana - len(fijos))          # C5
            modelo.Add(sum(decimas(datos.turnos[turno_id].horas) * x[(trabajador_id, fecha, turno_id)]
                           for fecha, turno_id in dias)                                 # C6
                       <= decimas(datos.config.horas_max_semana
                                  - sum(datos.turnos[turno_id].horas for turno_id in fijos)))

            descanso_finde(modelo, plan, x, trabajador_id, lunes, dias)

        # Jornada anual: lo que le queda de presupuesto despues de A, V, F y B.
        modelo.Add(sum(decimas(datos.turnos[turno_id].horas) * x[(trabajador_id, fecha, turno_id)]
                       for fecha, turno_id in por_trab.get(trabajador_id, ()))
                   <= decimas(libro.objetivo(trabajador_id) - libro.horas(trabajador_id)))


# --------------------------------------------------------------------------- #
#  Los niveles del objetivo
# --------------------------------------------------------------------------- #
def reparto(modelo, cuentas, techo, etiqueta):
    """Terminos que miden lo DESIGUAL que es un reparto, sobre contadores del modelo.

    Dos medidas sumadas, porque cada una sola es ciega a algo:
      * la desviacion de cada uno respecto a la media, escalada por N para no usar
        racionales (N*n - S, con S la suma, que es exactamente N*|n - media|);
      * el RANGO, el mayor menos el menor, tambien escalado por N.

    Con la desviacion sola da igual dos personas muy por encima que cuatro un poco
    por encima. Con el rango solo, solo cuentan los dos extremos.
    """
    n = len(cuentas)
    if n < 2:
        return []
    total = sum(cuentas)
    terminos = []
    for i, cuenta in enumerate(cuentas):
        desviacion = modelo.NewIntVar(0, n * techo, f"dev_{etiqueta}_{i}")
        modelo.Add(desviacion >= n * cuenta - total)
        modelo.Add(desviacion >= total - n * cuenta)
        terminos.append(desviacion)
    alto = modelo.NewIntVar(0, techo, f"max_{etiqueta}")
    bajo = modelo.NewIntVar(0, techo, f"min_{etiqueta}")
    modelo.AddMaxEquality(alto, cuentas)
    modelo.AddMinEquality(bajo, cuentas)
    terminos.append(n * (alto - bajo))
    return terminos


def equidad_horas(modelo, datos, x, por_trab, pool, libro):
    """Nivel 2 — que la demanda REAL se reparta por igual entre los correturnos.

    Todo lo que hay en el modelo es demanda real (`huecos_lv` ignora las lineas de
    refuerzo), asi que basta con sumar las horas de sus variables. Lo que se busca es
    que nadie acabe el ano con poca cobertura real y haya que completarle la jornada
    con refuerzos de calendario, que no cubren nada, mientras otro se lleva todo.
    """
    if len(pool) < 2:
        return None
    techo = decimas(max(libro.objetivo(w) for w in pool))
    cuentas = []
    for trabajador_id in pool:
        horas = modelo.NewIntVar(0, techo, f"h_{trabajador_id}")
        modelo.Add(horas == sum(decimas(datos.turnos[turno_id].horas)
                                * x[(trabajador_id, fecha, turno_id)]
                                for fecha, turno_id in por_trab.get(trabajador_id, ())))
        cuentas.append(horas)
    terminos = reparto(modelo, cuentas, techo, "horas")
    return sum(terminos) if terminos else None


def forma_semanal(modelo, datos, x, semanas, pool):
    """Nivel 3 — dentro de cada semana ISO, el mismo turno todos los dias que trabaje;
    y si no puede ser, al menos la misma franja (manana / tarde / noche).

    Se cuenta, por persona y semana, cuantas franjas y cuantos turnos distintos usa
    POR ENCIMA del primero: cero si toda la semana es el mismo turno. La franja pesa
    PESO_FRANJA veces mas que el turno, que es lo que la ordena por delante sin
    encadenar otro nivel.

    Es preferencia, no restriccion: si romperla tapa un hueco, el nivel 1 ya se lo ha
    llevado. La ZONA (el municipio) no entra: se descarto como criterio.
    """
    coste = []
    for trabajador_id in pool:
        for lunes, dias in sorted(semanas.get(trabajador_id, {}).items()):
            if len(dias) < 2:
                continue                    # un solo candidato: no hay forma que romper
            mios = [x[(trabajador_id, fecha, turno_id)] for fecha, turno_id in dias]
            trabaja = modelo.NewBoolVar(f"trabaja_{trabajador_id}_{lunes:%m%d}")
            modelo.AddMaxEquality(trabaja, mios)

            for etiqueta, peso, clave in (("fr", PESO_FRANJA, lambda s: datos.franja(s)),
                                          ("tn", 1, lambda s: s)):
                grupos = defaultdict(list)
                for fecha, turno_id in dias:
                    grupos[clave(turno_id)].append(x[(trabajador_id, fecha, turno_id)])
                if len(grupos) < 2:
                    continue                # solo hay una: no se puede romper nada
                usadas = []
                for nombre, variables_grupo in sorted(grupos.items()):
                    usa = modelo.NewBoolVar(f"{etiqueta}_{trabajador_id}_{lunes:%m%d}_{nombre}")
                    modelo.AddMaxEquality(usa, variables_grupo)
                    usadas.append(usa)
                coste.append(peso * (sum(usadas) - trabaja))
    return sum(coste) if coste else None


# --------------------------------------------------------------------------- #
#  Resolver
# --------------------------------------------------------------------------- #
def optimizar(modelo, expresion, maximizar, segundos, hilos, log, etiqueta):
    """Resuelve UN nivel. Devuelve (valor, solver), o (None, None) si no hay solucion.

    Con `segundos = None` no se le pone tope: se le deja demostrar el optimo. Solo
    tiene sentido en modelos pequenos, como el del canje de refuerzos.
    """
    if maximizar:
        modelo.Maximize(expresion)
    else:
        modelo.Minimize(expresion)
    solver = cp_model.CpSolver()
    if segundos is not None:
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


def sembrar(modelo, x, solucion):
    """Arranca el siguiente nivel desde la solucion del anterior.

    No es un truco de rendimiento: con la cobertura ya clavada en su optimo, encontrar
    CUALQUIER solucion valida desde cero es durisimo, y la del nivel anterior ya esta
    dentro de la region.
    """
    if solucion is None:
        return
    modelo.ClearHints()
    for var in x.values():
        modelo.AddHint(var, solucion.Value(var))


def volcar(datos, plan, libro, x, solucion):
    """Escribe la solucion en el plan y en el libro de horas."""
    if solucion is None:
        return 0
    puestas = 0
    for (trabajador_id, fecha, turno_id), var in sorted(x.items()):
        if solucion.Value(var):
            plan[(trabajador_id, fecha)] = turno_id
            libro.apunta(trabajador_id, turno_id)
            puestas += 1
    print(f"  asignaciones nuevas: {puestas}")
    return puestas


# --------------------------------------------------------------------------- #
#  Cambiar refuerzos de calendario por plazas reales
# --------------------------------------------------------------------------- #
def casillas_refuerzo(datos, plan):
    """Cada posicion del plan ocupada por un refuerzo de calendario.

    Devuelve (trabajador, fecha) -> linea de refuerzo. Son las casillas que este
    paso puede rellenar con otra cosa: un REF CAL declara dem=0, o sea que no cubre
    nada y soltarlo no deja ningun hueco detras.
    """
    return {(trabajador_id, fecha): turno_id
            for (trabajador_id, fecha), turno_id in plan.items()
            if turno_id not in DESCANSOS and datos.turnos[turno_id].dem == 0}


def descanso_contra_lo_fijo(datos, plan, casillas, trabajador_id, fecha, turno_id):
    """Descanso contra lo que esa persona tiene FIJO de verdad.

    Antes de comprobar se le quitan sus refuerzos del dia anterior, el mismo y el
    siguiente: los tres son casillas movibles y no deben estorbar aqui. Que dos
    cambios suyos de dias seguidos encajen entre si lo decide el modelo con sus
    restricciones, no este filtro.
    """
    quitados = []
    for i in (-1, 0, 1):
        dia = fecha + timedelta(days=i)
        if (trabajador_id, dia) in casillas:
            quitados.append((dia, plan.pop((trabajador_id, dia))))
    vale = legal.descanso_ok(datos, plan, trabajador_id, fecha, turno_id, False)
    for dia, turno_viejo in quitados:
        plan[(trabajador_id, dia)] = turno_viejo
    return vale


def opciones_de_casilla(datos, plan, casillas):
    """Que plaza real podria hacer cada casilla de refuerzo.

    Devuelve (trabajador, fecha) -> [plazas posibles].

    La franja tiene que coincidir: un REF CAL de manana se cambia por una linea de
    manana y uno de tarde por una de tarde. No es una regla legal — es que ese
    refuerzo se le puso ahi para darle estabilidad al dia, y cambiarle la franja
    seria deshacerla.

    Las horas no hace falta mirarlas: todo lo que falta vale 8 h y el refuerzo
    tambien, asi que la casilla acaba valiendo lo mismo tenga lo que tenga.
    """
    por_fecha = {}
    for (trabajador_id, fecha) in casillas:
        por_fecha.setdefault(fecha, []).append(trabajador_id)

    opciones = {}
    for turno_id, fecha in huecos_lv(datos, plan):
        for trabajador_id in por_fecha.get(fecha, ()):
            refuerzo = casillas[(trabajador_id, fecha)]
            if datos.franja(turno_id) != datos.franja(refuerzo):
                continue
            elegible, _ = datos.elegible(trabajador_id, turno_id, fecha)
            if not elegible:
                continue
            if descanso_contra_lo_fijo(datos, plan, casillas, trabajador_id, fecha, turno_id):
                opciones.setdefault((trabajador_id, fecha), []).append(turno_id)
    return opciones


def encaje_semanal(datos, plan, trabajador_id, fecha, turno_id):
    """Cuanto se parece ese turno a lo que esa persona ya hace esa semana.

    2 si esa semana ya hace ese mismo turno, 1 si al menos coincide la franja,
    0 si no se parece a nada. Es el criterio del nivel 3 del modelo, aplicado
    aqui a mano porque este paso va fuera.
    """
    lunes = lunes_de(fecha)
    suyos = set()
    for i in range(7):
        otro = lunes + timedelta(days=i)
        suyo = turno_de(plan, trabajador_id, otro) if otro != fecha else None
        if suyo is not None:
            suyos.add(suyo)
    if turno_id in suyos:
        return 2
    if datos.franja(turno_id) in {datos.franja(s) for s in suyos}:
        return 1
    return 0


def cambiar_refuerzos(datos, plan, libro, segundos=None, hilos=8):
    """Cambia refuerzos de calendario por plazas reales, eligiendo el conjunto ENTERO.

    Sin tope de tiempo a proposito: son unas cien booleanas y el solver demuestra el
    optimo en milisegundos. Ponerle limite solo serviria para devolver algo peor.

    No va plaza a plaza como haria un greedy. El cambio de un dia condiciona el del
    siguiente por el descanso, asi que coger la primera que valga puede cerrarte dos
    mejores mas adelante. Se plantean todos los cambios a la vez y se resuelve.

    El statu quo siempre es factible: todas las restricciones son de la forma "si
    haces este cambio, entonces...", asi que con todo a cero se cumplen solas. Importa
    porque el plan ya puede traer pares de refuerzos que incumplen C4 de fabrica —un
    REF CAL T que sale a las 22:00 seguido de un REF CAL M que entra a las 08:00 son
    10 h— y no queremos declararnos infactibles por algo que no hemos hecho nosotros.

    Devuelve cuantas plazas se han cubierto.
    """
    casillas = casillas_refuerzo(datos, plan)
    opciones = opciones_de_casilla(datos, plan, casillas)
    if not opciones:
        return 0

    m = cp_model.CpModel()
    y = {}
    for (trabajador_id, fecha), plazas in opciones.items():
        for turno_id in plazas:
            y[(trabajador_id, fecha, turno_id)] = m.NewBoolVar(
                f"y_{trabajador_id}_{fecha:%m%d}_{turno_id}")

    # 1. Como mucho un cambio por casilla: solo tiene un refuerzo ese dia.
    for (trabajador_id, fecha), plazas in opciones.items():
        m.AddAtMostOne([y[(trabajador_id, fecha, turno_id)] for turno_id in plazas])

    # 2. Como mucho un trabajador por plaza, y nunca mas de los que faltan.
    cuantas = Counter(huecos_lv(datos, plan))
    por_plaza = defaultdict(list)
    for (trabajador_id, fecha, turno_id), var in y.items():
        por_plaza[(turno_id, fecha)].append(var)
    for plaza, candidatas in por_plaza.items():
        m.Add(sum(candidatas) <= cuantas[plaza])

    # 3. C4 entre dias consecutivos del mismo trabajador. Tres casos, porque cada
    #    casilla puede acabar con su refuerzo o con una plaza.
    malos = pares_incompatibles(datos, sorted({s for (_, _, s) in y} | set(casillas.values())))
    for (trabajador_id, fecha), refuerzo_hoy in casillas.items():
        manana = fecha + timedelta(days=1)
        if (trabajador_id, manana) not in casillas:
            continue                    # lo de manana esta fijo: ya lo miro el filtro
        refuerzo_manana = casillas[(trabajador_id, manana)]
        hoy = opciones.get((trabajador_id, fecha), [])
        dia_siguiente = opciones.get((trabajador_id, manana), [])

        for a in hoy:
            for b in dia_siguiente:
                if (a, b) in malos:     # los dos cambiados y chocan
                    m.AddAtMostOne([y[(trabajador_id, fecha, a)],
                                    y[(trabajador_id, manana, b)]])
            if (a, refuerzo_manana) in malos:
                # cambiar hoy obliga a cambiar tambien manana, o no vale
                m.Add(y[(trabajador_id, fecha, a)]
                      <= sum(y[(trabajador_id, manana, b)] for b in dia_siguiente))
        for b in dia_siguiente:
            if (refuerzo_hoy, b) in malos:
                m.Add(y[(trabajador_id, manana, b)]
                      <= sum(y[(trabajador_id, fecha, a)] for a in hoy))

    # -- Nivel 1: cuantas plazas se cubren ----------------------------------- #
    cubiertas = sum(y.values())
    mejor, solucion = optimizar(m, cubiertas, True, segundos, hilos, False, "canje 1 cobertura")
    if mejor is None:
        return 0
    m.Add(cubiertas >= mejor)

    # -- Nivel 2: que la plaza encaje en la semana que ya hace ---------------- #
    encaje = sum(encaje_semanal(datos, plan, trabajador_id, fecha, turno_id) * var
                 for (trabajador_id, fecha, turno_id), var in y.items())
    _, sol = optimizar(m, encaje, True, segundos, hilos, False, "canje 2 encaje")
    if sol is not None:
        solucion = sol

    puestas = 0
    for (trabajador_id, fecha, turno_id), var in sorted(y.items()):
        if not solucion.Value(var):
            continue
        refuerzo = plan.pop((trabajador_id, fecha))
        libro.borra(trabajador_id, refuerzo)
        plan[(trabajador_id, fecha)] = turno_id
        libro.apunta(trabajador_id, turno_id)
        puestas += 1
    return puestas


def resolver(datos, plan, libro, segundos=300, hilos=8, log=False):
    """Paso D: reparte los huecos L-V entre los correturnos. Modifica plan y libro."""
    faltan = huecos_lv(datos, plan)
    pool = pool_correturnos(datos)

    modelo = cp_model.CpModel()
    x = var_modelo(modelo, datos, plan, faltan, pool)
    por_dia, por_plaza, por_trab = indices(x)
    semanas = semanas_de(por_trab)
    print(f"  huecos L-V: {len(faltan)} · {len(pool)} correturnos · {len(x)} variables")

    restricciones_dia(modelo, faltan, por_dia, por_plaza)
    restricciones_descanso(modelo, datos, x, por_trab, faltan)
    restricciones_semana(modelo, datos, plan, libro, x, pool, por_trab, semanas)

    # -- Nivel 1: cobertura --------------------------------------------------- #
    cubiertas = sum(x.values())
    mejor, solucion = optimizar(modelo, cubiertas, True, segundos, hilos, log, "1 cobertura")
    if mejor is None:
        print("  *** el modelo del paso D no encontro solucion ***")
        return 0
    modelo.Add(cubiertas >= mejor)

    # -- Nivel 2: equidad de horas -------------------------------------------- #
    objetivo = equidad_horas(modelo, datos, x, por_trab, pool, libro)
    if objetivo is not None:
        sembrar(modelo, x, solucion)
        valor, sol = optimizar(modelo, objetivo, False, segundos, hilos, log, "2 equidad de horas")
        if valor is not None:
            modelo.Add(objetivo <= valor)
            solucion = sol

    # -- Nivel 3: forma semanal ----------------------------------------------- #
    objetivo = forma_semanal(modelo, datos, x, semanas, pool)
    if objetivo is not None:
        sembrar(modelo, x, solucion)
        valor, sol = optimizar(modelo, objetivo, False, segundos, hilos, log, "3 forma semanal")
        if valor is not None:
            solucion = sol

    return volcar(datos, plan, libro, x, solucion)


# --------------------------------------------------------------------------- #
#  Rellenar la jornada con refuerzos de calendario
# --------------------------------------------------------------------------- #
def semana_clara(datos, plan, trabajador_id, lunes):
    """La franja de esa semana si toda ella es de la misma, o None.

    Solo valen manana y tarde: no existe REF CAL de noche, asi que una semana con
    noches no tiene refuerzo que la siga. Una semana vacia tampoco vale — no hay
    ritmo que copiar.
    """
    franjas = set()
    for i in range(7):
        fecha = lunes + timedelta(days=i)
        suyo = turno_de(plan, trabajador_id, fecha)
        if suyo is not None:
            franjas.add(datos.franja(suyo))
    if len(franjas) != 1:
        return None
    franja = franjas.pop()
    return franja if franja in ("mañana", "tarde") else None


def linea_de_refuerzo(datos, franja):
    """La linea REF CAL de esa franja, si la hay."""
    for turno_id, turno in sorted(datos.turnos.items()):
        if turno.dem == 0 and datos.franja(turno_id) == franja:
            return turno_id
    return None


def cabe_refuerzo(datos, plan, libro, trabajador_id, fecha, refuerzo):
    """Si se le puede poner ese refuerzo ese dia.

    `permite` cubre C2, C4, C5 y C6. Falta el descanso de fin de semana, que no
    mira: si esa semana tiene sabado y domingo puestos por F, el refuerzo no puede
    comerse el par de dias consecutivos libres que hacen falta. Por eso se pone,
    se comprueba y se quita.
    """
    elegible, _ = datos.elegible(trabajador_id, refuerzo, fecha)
    if not elegible:
        return False
    if not libro.cabe(trabajador_id, refuerzo):
        return False
    if not legal.permite(datos, plan, trabajador_id, fecha, refuerzo):
        return False

    plan[(trabajador_id, fecha)] = refuerzo
    vale = legal.descanso_finde_ok(datos, plan, trabajador_id, lunes_de(fecha))
    plan.pop((trabajador_id, fecha))
    return vale


def semanas_con_sitio(datos, plan, trabajador_id):
    """Las semanas de franja clara donde le queda algun dia L-V libre.

    Devuelve [(lunes, franja, [dias libres])], en orden de calendario.
    """
    sitio = []
    for lunes in sorted({lunes_de(fecha) for fecha in datos.lista_dias_calendario}):
        franja = semana_clara(datos, plan, trabajador_id, lunes)
        if franja is None:
            continue
        libres = [lunes + timedelta(days=i) for i in range(5)
                  if (trabajador_id, lunes + timedelta(days=i)) not in plan]
        if libres:
            sitio.append((lunes, franja, libres))
    return sitio


def repartidas(semanas, cuantas):
    """`cuantas` semanas de la lista, repartidas por el ano en vez de seguidas.

    Si tiene 21 semanas y necesita 16, coge una de cada 21/16: se salta cuatro o
    cinco, y las salta espaciadas. Asi los refuerzos no se le amontonan en enero.
    """
    if cuantas >= len(semanas):
        return semanas
    paso = len(semanas) / cuantas
    return [semanas[min(len(semanas) - 1, int(i * paso))] for i in range(cuantas)]


def rellenar_refuerzos(datos, plan, libro):
    """Completa la jornada de los correturnos con refuerzos de calendario.

    Un REF CAL no cubre nada: esta para que el cuadrante muestre la jornada que el
    convenio les reconoce. Se le da la franja de la semana en que cae —si esa
    semana hace mananas, refuerzo de manana— para no romperle el ritmo, y se
    reparten por el ano para que no se le amontonen.

    Devuelve cuantos se han puesto.
    """
    puestos = 0
    for trabajador_id in sorted(pool_correturnos(datos)):
        for permitir_dos in (False, True):
            faltan = int((libro.objetivo(trabajador_id) - libro.horas(trabajador_id)) // 8)
            if faltan <= 0:
                break
            semanas = semanas_con_sitio(datos, plan, trabajador_id)
            elegidas = semanas if permitir_dos else repartidas(semanas, faltan)
            for lunes, franja, libres in elegidas:
                if faltan <= 0:
                    break
                refuerzo = linea_de_refuerzo(datos, franja)
                if refuerzo is None:
                    continue
                for fecha in libres:
                    if not cabe_refuerzo(datos, plan, libro, trabajador_id, fecha, refuerzo):
                        continue
                    plan[(trabajador_id, fecha)] = refuerzo
                    libro.apunta(trabajador_id, refuerzo)
                    puestos += 1
                    faltan -= 1
                    break               # uno por semana en la primera vuelta
    return puestos

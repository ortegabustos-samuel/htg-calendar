"""
findes.py — Paso F: reparto de sabados, domingos y festivos.

Va entre V y B, y antes del relleno de lunes a viernes. El motivo del orden es
que un dia de finde condiciona su propia semana —descanso, dias y horas de la
semana ISO— asi que colocarlo primero deja un esqueleto sobre el que el resto
solo tiene que encajar. Al reves habria que desmontar lo ya puesto.

Ademas cambia el reparto de horas de cada uno, y el reparto de horas lo decide
B: si F fuera despues, B habria trabajado sobre una foto que luego cambia.

La equidad se mide POR MUNICIPIO y POR CLASE de dia. Cada municipio reparte lo
suyo entre los suyos, asi que los objetivos son distintos en cada uno. Y quien
no declara ninguna linea de domingo no entra en el reparto de domingos.

Los grupos rigidos (config.toml) quedan fuera: a esos no se les toca el patron.
"""
from datetime import timedelta

import legal
import libranzas
from cargar_datos import DESCANSOS, turno_de

CLASES = ("SAB", "DOM", "FEST")     # en este orden: un sabado condiciona su domingo
DIAS_SEMANA = 5                     # dias que se busca que trabaje cada uno por semana ISO


def lunes_de(fecha):
    return fecha - timedelta(days=fecha.weekday())


# --------------------------------------------------------------------------- #
#  Quien compite con quien, y contra que numero
# --------------------------------------------------------------------------- #
def tiene_capacidad_de(datos, trabajador_id, clase):
    """Si declara alguna linea que le habilite para esa clase de dia."""
    for (otro, turno_id), cap in datos.capacidades.items():
        if otro != trabajador_id:
            continue
        if clase == "SAB" and cap.sab:
            return True
        if clase == "DOM" and cap.dom:
            return True
        if clase == "FEST" and cap.fest:
            return True
    return False


def pool_finde(datos, municipio, clase):
    """Quien se reparte los dias de esa clase en ese municipio.

    Tres filtros, y cada uno tiene su motivo:
      - del municipio, porque la equidad se mide ahi;
      - no de un grupo rigido, porque a esos no se les toca el patron;
      - con capacidad de esa clase, porque quien no declara ninguna linea de
        domingo no puede entrar en el reparto de domingos.
    """
    dentro = []
    for trabajador_id, trabajador in datos.trabajadores.items():
        if trabajador.municipio != municipio:
            continue
        if libranzas.es_rigido(datos, trabajador_id):
            continue
        if tiene_capacidad_de(datos, trabajador_id, clase):
            dentro.append(trabajador_id)
    return sorted(dentro)


def cuenta_findes(datos, plan, clase):
    """Cuantos dias de esa clase tiene ahora mismo cada trabajador."""
    cuenta = {}
    for (trabajador_id, fecha), turno_id in plan.items():
        if turno_id in DESCANSOS:       # un DO no es un finde trabajado
            continue
        if datos.tipo_dia(fecha, datos.turnos[turno_id].municipio) == clase:
            cuenta[trabajador_id] = cuenta.get(trabajador_id, 0) + 1
    return cuenta


def dias_por_trabajador(datos, plan, clase):
    """Para cada trabajador, los dias de esa clase que tiene: [(turno, fecha), ...]"""
    dias = {}
    for (trabajador_id, fecha), turno_id in plan.items():
        if turno_id in DESCANSOS:
            continue
        if datos.tipo_dia(fecha, datos.turnos[turno_id].municipio) == clase:
            dias.setdefault(trabajador_id, []).append((turno_id, fecha))
    for lista in dias.values():
        lista.sort(key=lambda par: par[1])
    return dias


def huecos_de_clase(datos, plan, municipio, clase):
    """Plazas de esa clase en ese municipio que estan sin cubrir."""
    ocupadas = set()
    for (trabajador_id, fecha), turno_id in plan.items():
        if turno_id in DESCANSOS:       # no ocupa plaza: no tapa ningun hueco
            continue
        ocupadas.add((turno_id, fecha))

    huecos = []
    for fecha in datos.lista_dias_calendario:
        for turno_id, turno in datos.turnos.items():
            if turno.municipio != municipio or turno.dem == 0:
                continue
            if not datos.opera(turno_id, fecha):
                continue
            if datos.tipo_dia(fecha, turno.municipio) != clase:
                continue
            if (turno_id, fecha) not in ocupadas:
                huecos.append((turno_id, fecha))
    return huecos


def objetivo(datos, plan, municipio, clase):
    """Cuantos dias de esa clase le tocan a cada uno del pool.

    Todo lo que hay que repartir —lo que ya tienen los del pool mas lo que
    esta sin cubrir— dividido entre cuantos son.
    """
    pool = pool_finde(datos, municipio, clase)
    if not pool:
        return 0.0
    cuenta = cuenta_findes(datos, plan, clase)
    ya = sum(cuenta.get(w, 0) for w in pool)
    return (ya + len(huecos_de_clase(datos, plan, municipio, clase))) / len(pool)


# --------------------------------------------------------------------------- #
#  Dar un dia de finde: la semana queda en 5
# --------------------------------------------------------------------------- #
def dias_de_semana(plan, trabajador_id, lunes):
    """Cuantos dias trabaja esa semana ISO, contando el finde."""
    n = 0
    for i in range(7):
        if turno_de(plan, trabajador_id, lunes + timedelta(days=i)) is not None:
            n += 1
    return n


def bloque_a_soltar(plan, trabajador_id, lunes, cuantos, libres, huecos):
    """Los `cuantos` dias L-V consecutivos que suelta esa semana.

    Van seguidos porque asi lo hace el planificador: si le das sabado y domingo
    no le quitas el lunes y el jueves, le quitas dos dias del tiron.

    Entre los bloques posibles se coge el de mas holgura —el mismo criterio de
    B—, que es una forma determinista de decidir cuando da igual cual sea.

    Devuelve [] si no hay que soltar nada, y None si hacia falta pero no hay
    ningun bloque consecutivo disponible.
    """
    if cuantos <= 0:
        return []
    lv = [lunes + timedelta(days=i) for i in range(5)]
    ya_libres = {i for i in range(5) if turno_de(plan, trabajador_id, lv[i]) is None}

    mejor = None
    mejor_holgura = None
    for inicio in range(5 - cuantos + 1):
        nuevos = set(range(inicio, inicio + cuantos))
        if any(turno_de(plan, trabajador_id, lv[i]) is None for i in nuevos):
            continue                    # algun dia de ese bloque ya no lo trabaja
        # Los libres de la semana, contando los de antes, tienen que quedar seguidos.
        # Si no, el sabado y el domingo de esa semana se quedan sin su par de dias
        # consecutivos de descanso.
        todos = sorted(ya_libres | nuevos)
        if todos != list(range(todos[0], todos[0] + len(todos))):
            continue
        h = sum(libres[lv[i]] - huecos[lv[i]] for i in nuevos)
        if mejor_holgura is None or h > mejor_holgura:
            mejor_holgura = h
            mejor = [lv[i] for i in sorted(nuevos)]
    return mejor


def puede_tomar(datos, plan, trabajador_id, turno_id, fecha):
    """Si tiene capacidad para esa plaza y no esta ya ocupado ese dia.

    La legalidad NO se mira aqui: hay que mirarla con la semana ya aligerada,
    y eso pasa dentro de `dar_finde`.
    """
    if (trabajador_id, fecha) in plan:
        return False
    elegible, _ = datos.elegible(trabajador_id, turno_id, fecha)
    return elegible


def dar_finde(datos, plan, libro, trabajador_id, turno_id, fecha, libres, huecos,
              exento_localizado=False):
    """Le da esa plaza de finde dejandole la semana en 5 dias.

    Suelta primero y comprueba despues: la legalidad se mide sobre la semana ya
    aligerada, no sobre la que tenia al empezar. Si aun asi no sale, se deshace
    todo y que lo coja otro.

    `exento_localizado` perdona el descanso entre jornadas cuando el turno es
    una guardia de 24 h (entrada = salida), que es localizacion y no presencia.
    El paso D tambien la usa, contandola en su ultimo nivel. Aqui se reserva
    para cuando no hay nadie que pueda entrar sin ella.
    """
    lunes = lunes_de(fecha)
    sobran = dias_de_semana(plan, trabajador_id, lunes) + 1 - DIAS_SEMANA
    bloque = bloque_a_soltar(plan, trabajador_id, lunes, max(0, sobran), libres, huecos)
    if bloque is None:
        return False                    # no hay bloque consecutivo que soltar

    devolver = []
    for dia in bloque:
        devolver.append((dia, plan[(trabajador_id, dia)]))
        libranzas.soltar(plan, libro, trabajador_id, dia)

    if not legal.permite(datos, plan, trabajador_id, fecha, turno_id, exento_localizado):
        for dia, turno_viejo in devolver:       # no cuela: lo dejamos como estaba
            libranzas.asignar(plan, libro, trabajador_id, dia, turno_viejo)
        return False

    libranzas.asignar(plan, libro, trabajador_id, fecha, turno_id)
    for dia, _ in devolver:
        huecos[dia] += 1                # el hueco L-V que acabamos de abrir
    return True


# --------------------------------------------------------------------------- #
#  El reparto
# --------------------------------------------------------------------------- #
def ofrecer(datos, plan, libro, turno_id, fecha, pool, meta, cuenta, libres, huecos, exento):
    """Ofrece esa plaza al del pool que menos lleve. Devuelve si la ha colocado.

    Primero solo a quien esta por debajo del objetivo. Si ninguno de esos puede,
    se le ofrece igual a los que ya llegaron: el objetivo es un reparto, no un
    techo, y dejar la plaza vacia es peor que que alguien se pase de uno.

    En las dos vueltas se va de menos a mas, asi que si hay que pasarse se pasa
    el que menos lleve.
    """
    candidatos = sorted(pool, key=lambda w: cuenta.get(w, 0))
    for permitir_exceso in (False, True):
        for trabajador_id in candidatos:
            if not permitir_exceso and cuenta.get(trabajador_id, 0) >= meta:
                break               # ordenados de menos a mas: los siguientes tambien llegaron
            if not puede_tomar(datos, plan, trabajador_id, turno_id, fecha):
                continue
            if dar_finde(datos, plan, libro, trabajador_id, turno_id, fecha,
                         libres, huecos, exento):
                cuenta[trabajador_id] = cuenta.get(trabajador_id, 0) + 1
                return True
    return False


def barrido(datos, plan, libro, municipio, clase, pool, meta, cuenta, libres, huecos, exento):
    """Una pasada por todos los huecos de esa clase. Devuelve si coloco alguno."""
    colocado = False
    for turno_id, fecha in huecos_de_clase(datos, plan, municipio, clase):
        if ofrecer(datos, plan, libro, turno_id, fecha, pool, meta, cuenta, libres, huecos, exento):
            colocado = True
    return colocado


def repartir_huecos(datos, plan, libro, municipio, clase, pool, meta, cuenta, libres, huecos):
    """Cada hueco que ya existe, al del pool que menos lleve.

    Va primero porque no le cuesta un dia a nadie: esa plaza ya estaba vacia.

    Se repite mientras se coloque algo. Una asignacion cambia la semana de quien
    la recibe —le suelta dias L-V— y eso puede abrirle la puerta a un hueco que
    en la pasada anterior estaba bloqueado. Con una sola pasada esos se perdian.

    La exencion de localizado se guarda para el final: mientras alguna pasada
    normal siga colocando, no se gasta. Solo cuando ya no se coloca nada sin
    ella se prueba con ella, y si eso desbloquea algo se vuelve a intentar sin.
    """
    while True:
        if barrido(datos, plan, libro, municipio, clase, pool, meta, cuenta,
                   libres, huecos, False):
            continue                    # algo se coloco sin gastar la exencion
        if not barrido(datos, plan, libro, municipio, clase, pool, meta, cuenta,
                       libres, huecos, True):
            return                      # ni con exencion se coloca ya nada


def buscar_prestamo(datos, plan, clase, pool, meta, cuenta, dias, receptor):
    """Un dia de esa clase que alguien sobrado le pueda pasar al receptor.

    Solo valen los dias en una linea que el receptor tenga declarada: si no
    puede hacerla, quitarsela al donante solo crearia un hueco huerfano.
    """
    for donante in sorted(pool, key=lambda w: -cuenta.get(w, 0)):
        # Tiene que sacarle al menos DOS, o el traspaso no iguala nada: con 16 y 15
        # quedarian en 15 y 16, igual de lejos pero al reves. Y repitiendo el reparto
        # se lo devolverian el uno al otro para siempre.
        if cuenta.get(donante, 0) - cuenta.get(receptor, 0) < 2:
            return None                 # van ordenados de mas a menos: los demas tampoco
        for turno_id, fecha in dias.get(donante, []):
            if (receptor, fecha) in plan:
                continue                # ese dia el receptor ya trabaja
            if clase == "SAB" and (donante, fecha + timedelta(days=1)) in plan:
                continue                # le dejariamos el domingo huerfano
            elegible, _ = datos.elegible(receptor, turno_id, fecha)
            if elegible:
                return donante, turno_id, fecha
    return None


def pedir_prestado(datos, plan, libro, clase, pool, meta, cuenta, dias, libres, huecos):
    """Lo que siga faltando, quitandoselo a quien va sobrado.

    Solo se le quita un dia a alguien cuando hay un destinatario concreto que
    puede hacer esa misma linea, asi que la plaza liberada nace ya con dueño y
    nunca queda un hueco huerfano.

    Devuelve si ha movido algun dia. Quien llama lo necesita para saber si vale
    la pena volver a repartir: un prestamo cambia la semana de dos personas y
    puede desbloquear huecos que antes no cogia nadie.
    """
    movido = False
    for receptor in sorted(pool, key=lambda w: cuenta.get(w, 0)):
        while cuenta.get(receptor, 0) < meta:
            prestamo = buscar_prestamo(datos, plan, clase, pool, meta, cuenta, dias, receptor)
            if prestamo is None:
                break                   # no hay nadie sobrado que le sirva
            donante, turno_id, fecha = prestamo
            libranzas.soltar(plan, libro, donante, fecha)
            if not dar_finde(datos, plan, libro, receptor, turno_id, fecha, libres, huecos):
                libranzas.asignar(plan, libro, donante, fecha, turno_id)     # se deshace
                break
            dias[donante].remove((turno_id, fecha))
            dias.setdefault(receptor, []).append((turno_id, fecha))
            cuenta[donante] -= 1
            cuenta[receptor] = cuenta.get(receptor, 0) + 1
            movido = True
    return movido


def repartir(datos, plan, libro):
    """Paso F: iguala sabados, domingos y festivos dentro de cada municipio.

    La clase va por fuera y el municipio por dentro: todos los sabados de todos
    los municipios antes que ningun domingo, porque un sabado condiciona el
    domingo de su mismo fin de semana.
    """
    especiales = libranzas.cubridores_especiales(datos)
    libres = libranzas.correturnos_libres_por_dia(datos)
    huecos = libranzas.huecos_por_dia(datos, plan, especiales)

    municipios = sorted({t.municipio for t in datos.trabajadores.values()})
    for clase in CLASES:
        for municipio in municipios:
            pool = pool_finde(datos, municipio, clase)
            if not pool:
                continue
            meta = objetivo(datos, plan, municipio, clase)
            cuenta = cuenta_findes(datos, plan, clase)
            dias = dias_por_trabajador(datos, plan, clase)
            # Las dos fases se afectan: un prestamo cambia la semana de dos
            # personas y puede desbloquear huecos que el reparto anterior no
            # pudo colocar. Asi que se repiten hasta que el prestamo no mueva
            # nada — el reparto sale ya agotado por su cuenta.
            while True:
                repartir_huecos(datos, plan, libro, municipio, clase,
                                pool, meta, cuenta, libres, huecos)
                if not pedir_prestado(datos, plan, libro, clase,
                                      pool, meta, cuenta, dias, libres, huecos):
                    break

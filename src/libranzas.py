from datetime import timedelta

import base
from cargar_datos import DESCANSOS
from horas import EPS

SEPARACION_MINIMA = 15   # dias de vida normal que se le deja a un cubridor entre dos
                         # coberturas: tantos como dura una ausencia. Sin esto el
                         # principal encadena quincenas y el suplente no entra nunca.


def es_rigido(datos, trabajador_id):
    """ Si su grupo cede bloques enteros o admite dias sueltos"""
    trabajador = datos.trabajadores[trabajador_id]
    return trabajador.tipo == "patron"          # solo quedan patrones reales: noches y UVI

def bloque_desde(datos, trabajador_id, fecha):
    """
    Para aquellos grupos que son rigidos se busca lo que seria una racha de trabajo
    es decir se busca una tirada desde el primer dia de trabajo hasta el siguiente
    incluyendo los descansos pertinentes
    """
    # OJO: aquí "trabaja" tiene que excluir los descansos (DS, DO). `prescrito` los devuelve
    # porque ocupan el día, pero si contaran como racha el bloque no terminaría nunca, se comería
    # el resto del año y esos trabajadores dejarían de poder ceder su exceso de horas.
    def trabaja(dia):
        turno = base.prescrito(datos, trabajador_id, dia)
        return turno is not None and turno not in DESCANSOS

    if not trabaja(fecha):
        return None                 # Ese dia no se trabaja
    anterior = fecha - timedelta(days=1)
    if trabaja(anterior):
        return None                 # Vamos en medio de una racha

    dias = []
    actual = fecha
    while actual <= datos.fin and trabaja(actual):
        dias.append(actual)
        actual += timedelta(days=1)
    while actual <= datos.fin and not trabaja(actual):
        dias.append(actual)
        actual += timedelta(days=1)

    return dias

def candidatos_de_cesion(datos, plan, trabajador_id, especiales):
    """
    Decidimos los grupos de dias que se van a poder ceder en el caso de aquellos que no
    sean indivisibles y dias sueltos en caso de ser divisibles
    """
    candidatos = []
    if es_rigido(datos, trabajador_id):
        for fecha in datos.lista_dias_calendario:
            dias = bloque_desde(datos,trabajador_id,fecha)
            if dias is None:
                continue
            if dias_intactos(datos,plan,trabajador_id,dias):
                candidatos.append(dias)
        return candidatos

    for fecha in datos.lista_dias_calendario:
        turno = base.prescrito(datos,trabajador_id,fecha)
        if turno is None or turno in DESCANSOS:
            continue                # un descanso no es cedible: ya es descanso
        if turno in especiales:
            continue
        if datos.tipo_dia(fecha,datos.turnos[turno].municipio) != "LV":
            continue
        if dias_intactos(datos, plan, trabajador_id, [fecha]):
            candidatos.append([fecha])
    return candidatos

def dias_comprometido(datos, plan, cubridor):
    """Los dias del anio que el cubridor no trabaja en su prescripcion"""
    dias = []
    for fecha in datos.lista_dias_calendario:
        if not dias_intactos(datos, plan, cubridor, [fecha]):
            dias.append(fecha)
    return dias

def distancia_bloque(dias_candidatos, comprometido):
    """A cuanto le queda el bloque cedido mas proximo"""
    if not comprometido:
        return 10000
    distancias = []
    for fecha in dias_candidatos:
        for ocupado in comprometido:
            distancias.append(abs((fecha - ocupado).days))
    return min(distancias)

def pool_de(datos, municipio):
    """La plantilla que absorbe en el modelo lo que se cede: fijos y correturnos del municipio."""
    return [trabajador_id for trabajador_id, trabajador in datos.trabajadores.items()
            if trabajador.tipo in ("fijo", "correturno") and trabajador.municipio == municipio]

def demanda_del_pool(datos, municipio, especiales):
    """Plazas por dia de las lineas que cubre el pool del municipio: con demanda, sin cubridor
    designado (esas solo las hace el cubridor) y fuera de los patrones (esas las pinta el
    esqueleto)."""
    de_patron = {celda for filas in datos.patrones.values() for fila in filas for celda in fila.values()}
    lineas = [turno_id for turno_id, turno in datos.turnos.items()
              if turno.municipio == municipio and turno.dem > 0
              and turno_id not in especiales and turno_id not in de_patron]
    return {fecha: sum(datos.turnos[s].dem for s in lineas if datos.opera(s, fecha))
            for fecha in datos.lista_dias_calendario}, set(lineas)

def holgura(datos, plan, fecha, pool, demanda):
    """Cuanto sitio le queda al pool ese dia: lo que da la gente disponible y sin nada en el plan,
    menos las plazas que tiene que cubrir. Cada persona da de media jornada/(8*365) dias de
    trabajo por dia natural (unos 0,6): nadie trabaja los siete. Solo sirve para comparar dias."""
    ritmo = datos.config.horas_objetivo / (8 * 365)
    libres = sum(1 for trabajador_id in pool
                 if datos.disponible(trabajador_id, fecha) and (trabajador_id, fecha) not in plan)
    return libres * ritmo - demanda[fecha]

def holgura_media(datos, plan, dias, pool, demanda):
    """Cuanto sitio hay de media en los dias de un bloque"""
    return sum(holgura(datos, plan, fecha, pool, demanda) for fecha in dias) / len(dias)

def soltar(plan, libro, trabajador_id, fecha):
    """le quita lo del dia y descuenta horas en el plan.

    Un DO no se suelta NUNCA: es un descanso obligatorio por las 24 h del turno, no una libranza
    negociable. Si se pudiera soltar, cualquier cesión o traspaso posterior se lo llevaría por
    delante y el cuadrante final lo perdería.
    """
    if plan.get((trabajador_id, fecha)) in DESCANSOS:
        return
    turno = plan.pop((trabajador_id, fecha), None)
    if turno is not None:
        libro.borra(trabajador_id, turno)

def asignar(plan, libro, trabajador_id, fecha, turno_id):
    """Le pone ese turno y aniade horas"""
    plan[(trabajador_id,fecha)] = turno_id
    libro.apunta(trabajador_id,turno_id)

def ceder_traspasando(datos, plan, libro, titular, cubridores, pool, demanda):
    """Cede un bloque o dia que requiera un titular especial.

    La puntuacion es una tupla: primero si el bloque cae donde el pool tiene sitio para
    absorber al cubridor que se va, y solo en caso de empate se desempata por lo lejos
    que quede de lo que el cubridor ya tiene encima. Asi no hace falta inventarse un
    peso entre dos magnitudes que no se parecen en nada.
    """
    mejor_dias = None
    mejor_cubridor = None
    mejor_puntos = None

    for dias in candidatos_de_cesion(datos,plan, titular, {}):
        cubridor = elegir_cubridor(datos, plan, cubridores,dias)
        if cubridor is None:
            continue
        puntos = (holgura_media(datos, plan, dias, pool, demanda) >= 0,
                  distancia_bloque(dias, dias_comprometido(datos, plan, cubridor)))
        if mejor_puntos is None or puntos > mejor_puntos:
            mejor_puntos = puntos
            mejor_dias = dias
            mejor_cubridor = cubridor

    if mejor_dias is None:
        return False

    # El cubridor deja el pool esos dias: lo que el habria cubierto lo absorbe el modelo con el
    # resto de la plantilla, y la holgura lo ve porque ya esta ocupado en el plan.
    for fecha in mejor_dias:
        turno = base.prescrito(datos,titular,fecha)
        soltar(plan,libro,titular,fecha)
        soltar(plan,libro,mejor_cubridor, fecha)
        if turno is not None:
            asignar(plan, libro, mejor_cubridor,fecha, turno)
    return True

def ceder_liberando(datos, plan, libro, trabajador_id, especiales, pool, demanda, lineas_pool):
    """Cede un dia suelto y deja la plaza vacia para que la recoja el modelo.

    Se elige el dia en que el pool del municipio tiene mas holgura. No se le asigna
    nadie: quien la coja lo decide el modelo, que tiene mas informacion que nosotros.
    """
    mejor_fecha = None
    mejor_holgura = None

    for dias in candidatos_de_cesion(datos, plan, trabajador_id, especiales):
        fecha = dias[0]                 # la rama flexible devuelve dias sueltos
        sitio = holgura(datos, plan, fecha, pool, demanda)
        if mejor_holgura is None or sitio > mejor_holgura:
            mejor_holgura = sitio
            mejor_fecha = fecha

    if mejor_fecha is None:
        return False

    turno = plan.get((trabajador_id, mejor_fecha))
    soltar(plan, libro, trabajador_id, mejor_fecha)
    if turno in lineas_pool:
        demanda[mejor_fecha] += 1       # plaza nueva para el pool: ese dia queda peor para el siguiente
    return True


def cubridores_especiales(datos):
    """ Cubridores para aquellos turnos que solo puede hacer
    gente concreta
    Devuelve un diccionario con key=turno -> value=lista de trabajadores
    """
    cubridores = {}
    for (trabajador_id, turno_id), cap in datos.capacidades.items():
        if cap.v >= 1:
            cubridores.setdefault(turno_id, []).append((cap.v, trabajador_id))

    resultado = {}
    for turno_id, gente in cubridores.items():
        gente.sort()
        resultado[turno_id] = [trabajador_id for _, trabajador_id in gente]

    return resultado

def titulares_de(datos, turno_id):
    """Los trabajadores que hacen el turno_id de forma regular"""
    titulares = []
    for (trabajador_id, turno), cap in datos.capacidades.items():
        if turno == turno_id and cap.v == 0:
            titulares.append(trabajador_id)
    return titulares

def dias_intactos(datos, plan, trabajador_id, dias):
    """Si estos dias siguen tal como se los prescribe su patron, sin tocar.

    Intacto quiere decir dos cosas: que esta (no son dias de vacaciones) y que
    lo que tiene en el plan coincide con su prescripcion, o sea que nadie lo ha
    movido todavia.

    Se pregunta desde los dos lados:
      - de un cubridor, para saber si se le puede meter en otra plaza;
      - de un titular, para saber si esos dias siguen siendo suyos y los puede ceder.
    """
    for fecha in dias:
        if not datos.disponible(trabajador_id,fecha):
            return False
        if plan.get((trabajador_id, fecha)) != base.prescrito(datos, trabajador_id, fecha):
            return False
    return True

def elegir_cubridor(datos, plan, cubridores, dias):
    """ El primero capaz de cubrir todos los dias del rango.

    Se sigue prefiriendo al principal, pero si acaba de cubrir algo cerca se
    pasa al suplente. Si ninguno esta lo bastante despejado se vuelve al orden
    normal: antes amontonar que dejar la plaza sin cubrir.
    """
    libres = []
    for trabajador_id in cubridores:
        if dias_intactos(datos, plan, trabajador_id, dias):
            libres.append(trabajador_id)
    if not libres:
        return None

    for trabajador_id in libres:
        if distancia_bloque(dias, dias_comprometido(datos, plan, trabajador_id)) > SEPARACION_MINIMA:
            return trabajador_id

    # Ninguno esta despejado. Amontonar hay que amontonar, pero al que tenga
    # lo suyo mas lejos, no al primero de la lista.
    mejor = libres[0]
    mejor_dist = -1
    for trabajador_id in libres:
        dist = distancia_bloque(dias, dias_comprometido(datos, plan, trabajador_id))
        if dist > mejor_dist:
            mejor_dist = dist
            mejor = trabajador_id
    return mejor

def cubrir_ausencia_titular(datos, plan, titular, cubridores, inicio, fin):
    """ Mete al mejor cubridor o mejor conjunto de cubridores en el puesto del
    trabajador titular
    """
    dias = []
    fecha = inicio
    while fecha <= fin:
        dias.append(fecha)
        fecha += timedelta(days=1)

    #La mejor solucion que solo exista un cubridor
    cubridor = elegir_cubridor(datos, plan, cubridores, dias)
    if cubridor is not None:
        tramos = [(cubridor, dias)]
    else:
        #Caso donde no haya un unico cubridor
        tramos = []
        cubridor_actual = None
        dias_actual = []

        for fecha in dias:
            #Si mantenemos un cubridor activo seguimos con el
            if cubridor_actual is not None and dias_intactos(datos, plan, cubridor_actual, [fecha]):
                dias_actual.append(fecha)
                continue
            #El cubridor que teniamos ya no puede seguir
            if cubridor_actual is not None:
                tramos.append((cubridor_actual, dias_actual))

            cubridor_actual = elegir_cubridor(datos, plan, cubridores, [fecha])
            if cubridor_actual is None:
                dias_actual = []
            else:
                dias_actual = [fecha]

        if cubridor_actual is not None:
            tramos.append((cubridor_actual, dias_actual))

    for cubridor, dias_tramo in tramos:
        for fecha in dias_tramo:
            plan.pop((cubridor, fecha), None)
            turno = base.prescrito(datos, titular, fecha)
            if turno is not None:
                plan[(cubridor, fecha)] = turno #Si descansaba heredara el descanso tambien

def cubrir_vacaciones(datos, plan):
    """
    Paso de cubrir las vacaciones especificas que requieren un cobertor especial.
    Solo de titulares de patron: el cubridor hereda su ciclo. La linea especial de un fijo
    (H) la cubre su cubridor dentro del modelo de titulares.
    """
    ausencias = []
    for turno_id, cubridores in cubridores_especiales(datos).items():
        for titular in titulares_de(datos, turno_id):
            if datos.trabajadores[titular].tipo != "patron":
                continue
            for inicio, fin in datos.trabajadores[titular].vacaciones:
                ausencias.append((inicio, fin, titular, cubridores))
    ausencias.sort()

    for inicio, fin, titular, cubridores in ausencias:
        cubrir_ausencia_titular(datos, plan, titular, cubridores, inicio, fin)

def ceder(datos, plan, libro):
    """ Paso de cesiones por exceso de horas en el calendario, modificamos plan y libro de horas.

    Solo cede quien ya tiene algo en el plan, que a estas alturas son los patrones: fijos y
    correturnos no tienen nada todavia y el techo de su jornada lo pone el modelo. Lo que se
    cede lo absorbe el pool del municipio del titular (fijos y correturnos) dentro del modelo,
    asi que es su holgura la que decide donde se cede.
    """
    especiales = cubridores_especiales(datos)
    pools, demandas = {}, {}
    def del_municipio(trabajador_id):
        municipio = datos.trabajadores[trabajador_id].municipio
        if municipio not in pools:
            pools[municipio] = pool_de(datos, municipio)
            demandas[municipio] = demanda_del_pool(datos, municipio, especiales)
        return pools[municipio], demandas[municipio]

    # Primero las lineas con un cubridor designado: el cubridor hereda el bloque
    titulares_especiales = []
    for turno_id, cubridores in especiales.items():
        for titular in titulares_de(datos, turno_id):
            if datos.trabajadores[titular].tipo != "patron":
                continue            # un fijo no tiene nada en el plan que ceder
            titulares_especiales.append(titular)
            pool, (demanda, _) = del_municipio(titular)
            while libro.exceso(titular) > EPS:
                if not ceder_traspasando(datos, plan, libro, titular, cubridores, pool, demanda):
                    break

    # Ahora el resto de patrones: sueltan dias y la plaza queda para el modelo
    resto = [trabajador_id for trabajador_id, trabajador in datos.trabajadores.items()
             if trabajador.tipo == "patron" and trabajador_id not in titulares_especiales]
    while True:
        pendientes = [trabajador_id for trabajador_id in resto if libro.exceso(trabajador_id) > EPS]
        if not pendientes:
            break
        cedido = False
        for trabajador_id in pendientes:
            pool, (demanda, lineas_pool) = del_municipio(trabajador_id)
            cedido |= ceder_liberando(datos, plan, libro, trabajador_id, especiales,
                                      pool, demanda, lineas_pool)
        if not cedido:
            break                   # nadie puede ceder ya nada: sin esto el bucle no acaba

from datetime import timedelta

import base
from cargar_datos import DESCANSOS, DO
from horas import EPS

SEPARACION_MINIMA = 15   # dias de vida normal que se le deja a un cubridor entre dos
                         # coberturas: tantos como dura una ausencia. Sin esto el
                         # principal encadena quincenas y el suplente no entra nunca.


def es_rigido(datos, trabajador_id):
    """ Si su grupo cede bloques enteros o admite dias sueltos"""
    trabajador = datos.trabajadores[trabajador_id]
    grupo = trabajador.patron if trabajador.tipo == "patron" else trabajador.tipo
    return grupo in datos.config.grupos_rigidos

def bloque_desde(datos, trabajador_id, fecha):
    """
    Para aquellos grupos que son rigidos se busca lo que seria una racha de trabajo
    es decir se busca una tirada desde el primer dia de trabajo hasta el siguiente
    incluyendo los descansos pertinentes
    """
    # OJO: aquí "trabaja" tiene que excluir el DO. `prescrito` lo devuelve porque el DO ocupa el
    # día, pero es descanso: si contara como racha, un patrón que solo tiene turnos y DO —el de
    # UVI, que no lleva ni un LIBRE— no terminaría la racha nunca, el bloque se comería el resto
    # del año y esos trabajadores dejarían de poder ceder su exceso de horas.
    def trabaja(dia):
        return base.prescrito(datos, trabajador_id, dia) not in (None, DO)

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
        if turno is None or turno == DO:
            continue                # un descanso obligatorio no es cedible: ya es descanso
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

def holgura_media(dias, libres, huecos):
    """Cuanto sitio hay de media en los dias de un bloque"""
    total = 0
    for fecha in dias:
        total += libres[fecha] - huecos[fecha]
    return total / len(dias)

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

def ceder_traspasando(datos, plan, libro, titular, cubridores, libres, huecos):
    """Cede un bloque o dia que requiera un titular especial.

    La puntuacion es una tupla: primero si el bloque cae donde hay sitio, y
    solo en caso de empate se desempata por lo lejos que quede de lo que el
    cubridor ya tiene encima. Asi no hace falta inventarse un peso entre dos
    magnitudes que no se parecen en nada.
    """
    mejor_dias = None
    mejor_cubridor = None
    mejor_puntos = None

    for dias in candidatos_de_cesion(datos,plan, titular, {}):
        cubridor = elegir_cubridor(datos, plan, cubridores,dias)
        if cubridor is None:
            continue
        puntos = (holgura_media(dias, libres, huecos) >= 0,
                  distancia_bloque(dias, dias_comprometido(datos, plan, cubridor)))
        if mejor_puntos is None or puntos > mejor_puntos:
            mejor_puntos = puntos
            mejor_dias = dias
            mejor_cubridor = cubridor

    if mejor_dias is None:
        return False

    for fecha in mejor_dias:
        turno = base.prescrito(datos,titular,fecha)
        tenia = plan.get((mejor_cubridor, fecha))   # lo que el cubridor iba a hacer ese dia
        soltar(plan,libro,titular,fecha)
        soltar(plan,libro,mejor_cubridor, fecha)
        if turno is not None:
            asignar(plan, libro, mejor_cubridor,fecha, turno)
        if tenia is not None:
            huecos[fecha] += 1                      # la plaza que el cubridor acaba de dejar
    return True

def ceder_liberando(datos, plan, libro, trabajador_id, especiales, libres, huecos):
    """Cede un dia suelto y deja la plaza vacia para que la recoja el modelo.

    Se elige el dia con mas holgura: correturnos que hay menos plazas que ya
    estan sin cubrir. No se le asigna nadie: quien la coja lo decide el paso
    siguiente, que tiene mas informacion que nosotros.
    """
    mejor_fecha = None
    mejor_holgura = None

    for dias in candidatos_de_cesion(datos, plan, trabajador_id, especiales):
        fecha = dias[0]                 # la rama flexible devuelve dias sueltos
        holgura = libres[fecha] - huecos[fecha]
        if mejor_holgura is None or holgura > mejor_holgura:
            mejor_holgura = holgura
            mejor_fecha = fecha

    if mejor_fecha is None:
        return False

    soltar(plan, libro, trabajador_id, mejor_fecha)
    huecos[mejor_fecha] += 1            # ese dia queda un poco peor para el siguiente
    return True


def correturnos_libres_por_dia(datos):
    """Cuantos correturnos hay cada dia para saber la holgura
    de una cesion
    """
    libres = {}
    for fecha in datos.lista_dias_calendario:
        n = 0
        for trabajador_id, trabajador in datos.trabajadores.items():
            if trabajador.tipo != "correturno":
                continue
            if datos.disponible(trabajador_id, fecha):
                n += 1
        libres[fecha] = n
    return libres

def huecos_por_dia(datos, plan, especiales):
    """
    Con este metodo medimos cuantas plazas estan sin cubrir ahora, para ello
    no cuentan ni las que no tienen demanda ni las que son especiales y requieren
    alguien especifico paara cubrir
    """
    ocupadas = set()
    for (trabajador_id, fecha), turno_id in plan.items():
        ocupadas.add((turno_id, fecha))

    huecos = {}
    for fecha in datos.lista_dias_calendario:
        n = 0
        for turno_id in datos.turnos:
            if turno_id in especiales:
                continue
            if datos.turnos[turno_id].dem == 0:
                continue
            if not datos.opera(turno_id, fecha):
                continue
            if (turno_id, fecha) not in ocupadas:
                n += 1
        huecos[fecha] = n
    return huecos

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
                plan[(cubridor, fecha)] = turno #Si descansaba heredara None tambien

def cubrir_vacaciones(datos, plan):
    """
    Paso de cubrir las vacaciones especificas que requieren un cobertor especial
    """
    ausencias = []
    for turno_id, cubridores in cubridores_especiales(datos).items():
        for titular in titulares_de(datos, turno_id):
            for inicio, fin in datos.trabajadores[titular].vacaciones:
                ausencias.append((inicio, fin, titular, cubridores))
    ausencias.sort()

    for inicio, fin, titular, cubridores in ausencias:
        cubrir_ausencia_titular(datos, plan, titular, cubridores, inicio, fin)

def ceder(datos, plan, libro):
    """ Paso de cesiones por exceso de horas en el calendario, modificamos plan y libro de horas"""
    especiales = cubridores_especiales(datos)
    libres = correturnos_libres_por_dia(datos)
    huecos = huecos_por_dia(datos, plan, especiales)

    # Primero vamos a solucionar aquellas lineas con un cubridor especifico
    titulares_especiales = []
    for turno_id, cubridores in especiales.items():
        for titular in titulares_de(datos, turno_id):
            titulares_especiales.append(titular)
            while libro.exceso(titular) > EPS:
                if not ceder_traspasando(datos, plan, libro, titular, cubridores, libres, huecos):
                    break

    #Ahora el resto de la plantilla ya asignada
    resto = []
    for trabajador_id, trabajador in datos.trabajadores.items():
        if trabajador_id in titulares_especiales:
            continue
        if trabajador.tipo == "correturno":
            continue
        resto.append(trabajador_id)
    while True:
        pendientes = []
        for trabajador_id in resto:
            if libro.exceso(trabajador_id) > EPS:
                pendientes.append(trabajador_id)
        if not pendientes:
            break
        for trabajador_id in pendientes:
            ceder_liberando(datos, plan, libro, trabajador_id, especiales, libres, huecos)
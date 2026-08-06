"""
modelo.py — Construcción del modelo CP-SAT de cuadrantes.

Sobre una ventana de fechas construye variables x[w,d,s] (solo elegibles) y las
restricciones duras
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta, datetime

from cargar_datos import DIAS, LIBRE, Datos, Turno

from ortools.sat.python import cp_model

# Los parámetros LEGALES y el AÑO no viven aquí: son de la instancia, no del problema. Están en
# `<datos>/config.toml` y llegan por `datos.config` (ver cargar_datos.Config):
#   config.horas_objetivo — jornada anual objetivo (h). Techo de TODO lo que no sea cubrir: la
#       equidad apunta ahí, las cesiones de bloque aterrizan por debajo y el relleno de refuerzos
#       (rellenar_refuerzos) para en seco al llegar. (Antes el tope era 1826 y por eso media
#       plantilla derivaba a 1808-1834.)
#   config.rmin   — descanso mínimo entre jornadas (h)                     — C4
#   config.hmax7  — máx. trabajo efectivo por semana ISO (h)               — C6
#   config.cmax   — máx. días trabajados por semana ISO (tope general)     — C5
#   config.cmax_pool — el mismo tope para la plantilla FLEXIBLE (correturnos y mixtos)
# Estaban duplicados aquí y en diagnostico.py, así que el diagnóstico podía juzgar viable un
# dataset con un objetivo distinto del que luego aplicaba el modelo.

# Jornada que COMPUTA una SEMANA de plaza de LOCALIZADO 24h asumida entera (h). Es la unidad en que
# se contabiliza esa plaza para el objetivo anual, y no la suma de sus turnos, por dos motivos:
#  · quien la cubre se lleva la plaza CON sus descansos (_handover_critico): esa semana no hace
#    NINGÚN otro turno, así que es una semana de trabajo completa aunque la rotación solo le ponga
#    2 turnos (la fila corta libra miércoles y jueves; la larga son 5);
#  · sus turnos computan 8 h LEGALES cada uno, que es lo que exige el convenio, pero no describe lo
#    que la plaza ocupa: sumándolos, el cubridor de una semana corta aparecía con 16 h "trabajadas"
#    y el libro anual le reclamaba el resto en otras semanas -> acababa trabajando de más.
# El titular de la rotación queda igualmente FUERA del libro anual (ver _c9_jornada_anual): cubre su
# año entero salvo vacaciones y por acuerdo eso ES su jornada.
JORNADA_LOCALIZADO_SEMANA = 40

# Horizonte rodante: días que DECIDE cada ventana y días de contexto congelado por detrás (la
# 'cola', que da la costura legal entre ventanas). No son ajustables desde fuera: tocarlos cambia
# la costura y el prorrateo, no es un dial de usuario.
DIAS_VENTANA = 14
DIAS_COLA = 28

# Cap PRORRATEADO (dos funciones a la vez): además del tope anual duro (config.horas_objetivo), la jornada acumulada
# de cada NO-fijo hasta el fin de cada ventana se limita al ritmo lineal hacia el OBJETIVO 1776
# (prorrateado por días disponibles transcurridos) + COLCHON_PACE_H. Efecto doble: (a) anti
# front-loading → nadie agota horas antes de diciembre (mata el acantilado, reparte huecos homogéneo);
# (b) 1776 pasa a ser un TECHO BLANDO → casi nadie lo cruza y, si lo hace, por poco (≤ colchón) y solo
# cuando cubrir lo exige. COLCHON_PACE_H = h que se permite POR ENCIMA del ritmo de 1776 (el "por
# poco"; también da holgura para picos de demanda). El tope anual (C9) sigue como respaldo duro.
# Subir el colchón = más cobertura pero más gente por encima de 1776; bajarlo = lo contrario.
COLCHON_PACE_H = 24
# Colchón del cap para los patrones de CESIÓN GRUESA (los que solo pueden recortar bloques enteros,
# p.ej. una quincena de noche = 77 h): sin colchón el cap se incumpliría desde la primera semana (su
# rotación va por encima del ritmo de 1776 SIEMPRE, y no pueden recortar horas sueltas para seguirlo).
# ~Media unidad de cesión permite oscilar alrededor del ritmo y ceder cuando toca. El tope anual duro
# (config.horas_objetivo) sigue mandando al final del año.
COLCHON_NOCHE_H = 40
# Coste, dentro del CALENDARIO DE CESIONES (Nivel 0), de librar un bloque en un ciclo tenso: se paga
# por cada punto porcentual que la carga por persona de ese ciclo supera la media del horizonte. Un
# bloque libre no desaparece, lo cubre otro; si se concede cuando media plantilla está de vacaciones,
# ese otro sale de un turno que se queda sin cubrir. Ver `calendario_cesiones`.
PESO_CESION_TENSA = 60

# Penalización de cobertura (P1): PONDERADA por prioridad, en TRES escalones respecto a PESO_DEV (el
# coste de sacar a un trabajador de su patrón). Así la criticidad del turno decide si se toca o no un
# patrón para cubrirlo:
#   - prioridad 0 (COMODÍN, p.ej. REF CAL M/T): peso 0. NO es un objetivo de cobertura -> son relleno
#     para dar horas y absorber excedente; los llena SOLO la equidad de horas (W3), nunca a costa de un
#     turno real ("de sobrar, que sobren refuerzos"). La estacionalidad sale sola: en verano hay menos
#     gente disponible -> menos REF CAL; en otoño/primavera más.
#   - prioridad 1 (NORMAL): PESO_COBERTURA·prio, POR DEBAJO de PESO_DEV -> el patrón es INTOCABLE por
#     estos turnos (nunca se saca a nadie de su rotación para cubrir un turno normal).
#   - prioridad >=2 (CRÍTICO, p.ej. líneas de noche y UVI 24h): PESO_CRITICO·prio, POR ENCIMA de
#     PESO_DEV -> el turno DEBE cubrirse aunque haya que sacar a su cubridor especial de su patrón (y
#     su turno pase a un correturno). Prefiere igualmente al cubridor de 0-desvío (dedicado/prescrito) y
#     solo paga PESO_DEV cuando no hay ninguno disponible; si tampoco lo hay, queda como hueco marcado.
PESO_COBERTURA = 10      # peso por unidad de prioridad de un turno NORMAL   (prio 1 -> 10  < PESO_DEV)
PESO_CRITICO = 100       # peso por unidad de prioridad de un turno CRÍTICO  (prio 3 -> 300 > PESO_DEV)


def peso_cobertura(t: Turno) -> int:
    """Coste de dejar SIN cubrir una unidad de este turno (P1), en tres escalones respecto a PESO_DEV
    (coste de sacar a alguien de su patrón, ver más abajo):
      · prioridad 0 (comodín REF CAL): 0 -> no es objetivo de cobertura; lo llena la equidad de horas.
        (Antes se usaba (prio+1) para que no valiese 0; ahora se quiere EXACTAMENTE 0: relleno puro.)
      · prioridad 1 (normal): PESO_COBERTURA·prio, POR DEBAJO de PESO_DEV -> el patrón no se toca.
      · prioridad>=2 (crítico: noche, UVI 24h): PESO_CRITICO·prio, POR ENCIMA de PESO_DEV -> se saca al
        cubridor especial de su patrón para cubrirlo y su turno cae a un correturno; se prefiere igual al
        cubridor de 0-desvío (prescrito) y solo se paga PESO_DEV si no hay otro.
    El orden relativo se conserva (0 < normal < crítico) y la criticidad se declara en turnos.csv
    (prioridad), sin casos especiales por turno concreto en el código."""
    if t.prioridad <= 0:
        return 0
    if t.prioridad >= 2:
        return PESO_CRITICO * t.prioridad
    return PESO_COBERTURA * t.prioridad

# Equidad (P2): se equipara al PROMEDIO el nº de findes y de festivos entre los trabajadores capaces
# de cubrirlos. Los de SOLO L-V quedan fuera solos (nunca son elegibles en finde/festivo). Peso IGUAL
# para todos los trabajadores (sin favorecer perfiles) y para ambas métricas (sin favorecer tipo de
# día). LAMBDA queda como dial por si en el futuro se quiere ponderar una métrica sobre otra.
# SÁBADO y DOMINGO van POR SEPARADO, no como un único "finde": medidos juntos, el modelo igualaba
# el TOTAL de días de finde y dejaba repartos opuestos con la misma suma —uno con 20 sábados y 0
# domingos, otro con 12 y 8—. Medido en el año 2026: el finde quedaba en sigma 1.4 mientras los
# domingos iban de 0 a 8 dentro del mismo grupo.
METRICAS = ("sabado", "domingo", "festivo")
LAMBDA = {"sabado": 1, "domingo": 1, "festivo": 1}

# Peso del RANGO (máximo − mínimo del grupo) dentro de la equidad, además de la desviación media.
# La desviación L1 minimiza el total y es indiferente entre "uno se pasa 6" y "seis se pasan 1";
# el rango ataca justo lo que se ve como injusto: que alguien acabe con 12 domingos y otro con 3.
# Es lineal (max/min de CP-SAT), a diferencia de una L2 que exigiría variables producto.
PESO_RANGO = 4

# P5 (desempate): "vale" de evitar que un MIXTO cambie de turno/posición dentro de una misma semana
# laboral (L-V). Correturnos exentos (flexibles por diseño). Tunable.
PESO_ESTAB = 5

# P7 (desempate): "vale" de tirar de un cubridor SUPLENTE existiendo uno principal para esa línea.
# El gestor designa quién hace mejor cada línea (v=1) y quién la cubre solo si aquel no puede (v=2,
# 3…); esto es una preferencia de CALIDAD, no una regla legal, así que va en el nivel bajo: si el
# principal no está disponible, el suplente entra sin discusión antes que dejar la línea sin cubrir.
# En la escala de P_horas (minutos), 120 ≈ 2 h de desviación de jornada por día cubierto: suficiente
# para decidir cuando ambos pueden, insuficiente para mover cobertura o equidad.
PESO_ORDEN = 120

# Prioridad al repartir TRABAJO REAL cuando no llega para todos. Hace falta porque la demanda del
# servicio da 1.698 h por cabeza y el objetivo son 1776: alguien tiene que quedarse corto, y la
# empresa decide quién. Fijos, patrones y mixtos van por delante; el CORRETURNO es el único que
# queda detrás, porque es quien absorbe el relleno de calendario (y, si sobrara plantilla, donde se
# vería). Multiplica la desviación de jornada, así que cuando un turno lo pueden hacer un mixto y un
# correturno, se lo lleva el mixto. Importa sobre todo para los mixtos, que tienen capacidades muy
# estrechas —uno solo puede hacer 4 líneas— y sin esto pierden casi siempre.
PESO_JORNADA = {"patron": 2, "mixto": 2, "correturno": 1}

# Fijación del patrón (NIVEL DE COBERTURA): "vale" de sacar a un trabajador de patrón de su rotación.
# Los patrones están PACTADOS con los sindicatos: se priorizan por ENCIMA de la cobertura de turnos
# NORMALES (peso_cobertura(prio 1) = 10 < 100) y de la equidad, salvo vacaciones u otra imposibilidad.
# Deliberadamente QUEDA POR DEBAJO de la cobertura de turnos CRÍTICOS (peso_cobertura(prio>=2) >= 200 >
# 100): una línea de noche o UVI SÍ justifica sacar a su cubridor especial del patrón (su turno lo
# recoge un correturno). BLANDO, no restricción dura: las leyes SÍ siguen mandando (descanso, días
# consecutivos, horas/semana no se tocan; si la rotación choca con una de ellas, el patrón cede ahí,
# nunca al revés) — evita que una semana concreta imposible vuelva INFACTIBLE toda la ventana.
# Con datos reales (2026-07-10) el peso simétrico (=1) dejaba un turno localizado UVI compartido por
# 2 dedicados + un tercero-comodín: el tercero cubría 39/368 días aun con AMBOS dedicados disponibles
# y libres, porque desviar a cualquiera de su patrón costaba casi nada frente al valor de cobertura.
PESO_DEV = 100

# ...salvo cuando lo que la rotación prescribe ese día es un COMODÍN (REF CAL): entonces desviarse
# cuesta esto y no PESO_DEV. Un refuerzo no es una posición que defender —es relleno de prioridad 0,
# no responde a ninguna demanda—, así que protegerlo por encima de cubrir un turno real (10 por
# unidad de prioridad) era una asimetría no buscada: el modelo dejaba huecos teniendo ese día a
# alguien capacitado haciendo un refuerzo prescrito. Sigue siendo >0 para que el refuerzo se haga
# siempre que no estorbe: soltarlo tiene que COMPRARSE con cobertura, nunca salir gratis.
PESO_DEV_COMODIN = 1

# Etapa 4 (nivel bajo): "vale" de quitarle un día a un fijo. Pequeño, solo para que no retire días
# gratis: retira únicamente cuando baja el exceso de horas sobre el objetivo (1776). << horas/día.
PESO_RETIRA = 1

# Etapa 4 (ASIMÉTRICO): coste por minuto de que un fijo quede POR DEBAJO del ritmo hacia 1776 (falta)
# frente a por ENCIMA (exceso). falta >> exceso: su línea es SUYA por defecto — solo la cede una vez
# alcanzado/superado el ritmo, nunca mientras va corto. Con datos reales (2026-07-10) el peso simétrico
# (ambos=1) dejaba su línea como recurso COMPARTIDO ordinario, indiferente en cobertura a quién la haga:
# un fijo perdió 42 días/año (-272h de 1776) porque cederlos ayudaba la equidad de otros tanto como le
# perjudicaba a él. Con falta≫exceso, ceder estando corto es casi siempre más caro que cualquier
# beneficio ajeno en ese nivel. El tope anual lo garantiza C9 aparte, independiente de este peso.
PESO_FALTA_FIJO = 10
PESO_EXCESO_FIJO = 1
# Lexicográfico por objetivo único: minimizar  W·P1 + P2, con W > max(P2). Como P2 = Σ λ·rango
# y cada rango ≤ nº de días, max(P2) = Σλ·nº_días -> W se calcula según el horizonte
# (W = Σλ·nº_días + 1). Así P1 domina SIEMPRE y P2 solo desempata (evita que al crecer el
# horizonte P2 compita con P1 y el solver deje gente ociosa para bajar rangos).


def rango_fechas(inicio: date, fin: date) -> list[date]:
    """Lista de fechas [inicio, fin] inclusive."""
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


def semana(f: date) -> tuple[int, int]:
    """Clave (año, nº) de la semana ISO (lunes-domingo) a la que pertenece la fecha."""
    a, n, _ = f.isocalendar()
    return (a, n)


def lineas_localizadas(datos: Datos) -> set[str]:
    """Líneas de los patrones UVI (localizado 24h): las únicas que se ceden como PLAZA ENTERA con
    sus descansos (_handover_critico) y, por tanto, las únicas que computan por SEMANA y no por
    turno (ver JORNADA_LOCALIZADO_SEMANA)."""
    return {s for p in _patrones_uvi(datos)
            for fila in datos.patrones.get(p, []) for s in fila.values()
            if s and s != LIBRE and s in datos.turnos}


def jornada_minutos(datos: Datos, plan: dict[tuple[str, date], str],
                    fechas: list[date] | None = None) -> dict[str, int]:
    """Minutos de JORNADA de cada trabajador en `plan`, en la moneda del LIBRO ANUAL (la del
    objetivo 1776) — que NO es la de la jornada legal:

      · turno normal            -> horas_consumo (= horas computadas salvo localizados);
      · SEMANA con localizado   -> JORNADA_LOCALIZADO_SEMANA fija, sean 2 turnos o 5.

    `fechas` = horizonte que se CONTABILIZA. Manda en dos cosas: solo cuentan los días que estén en
    él —lo de fuera es de otro libro: el plan arranca el lunes de la semana del 1 de enero, y esos
    días de diciembre son jornada del año ANTERIOR, ya cerrada— y la semana que quede CORTADA por su
    borde se prorratea (la última del año, o el corte de una ventana). Si es None, cuenta todo el
    plan y toda semana tocada entera.

    La jornada LEGAL (`Turno.horas`, 8 h por localizado) sigue siendo la de C4/C5/C6 y no se toca
    aquí: son dos contabilidades distintas a propósito. Y ojo, esas dos SÍ miran los días de fuera
    del libro: la semana del 29 de diciembre es una semana ISO real y sus topes se cuentan enteros.
    """
    loc = lineas_localizadas(datos)
    dentro = set(fechas) if fechas is not None else None
    dias_sem = Counter(semana(f) for f in fechas) if fechas is not None else None
    porsem: dict[tuple[str, tuple[int, int]], list[str]] = defaultdict(list)
    for (w, f), s in plan.items():
        if s in datos.turnos and (dentro is None or f in dentro):
            porsem[(w, semana(f))].append(s)
    mins: dict[str, int] = defaultdict(int)
    for (w, sm), turnos in porsem.items():
        if any(s in loc for s in turnos):
            n = min(7, dias_sem.get(sm, 7)) if dias_sem is not None else 7
            mins[w] += round(JORNADA_LOCALIZADO_SEMANA * 60 * n / 7)
            turnos = [s for s in turnos if s not in loc]   # por si el handover no aplicara
        mins[w] += sum(round(datos.turnos[s].horas_consumo * 60) for s in turnos)
    return dict(mins)


def carga_diaria(datos: Datos, fechas: list[date]) -> dict[date, float]:
    """Horas de demanda que le tocan a CADA persona disponible ese día.

    Es la medida de tensión de la plantilla: la demanda no cambia en agosto, pero hay mucha menos
    gente para atenderla, así que a cada disponible le corresponde bastante más. La usan dos sitios
    que deben hablar del mismo idioma — el prorrateo del objetivo anual (dar más margen de horas
    cuando la plantilla está tensa) y el calendario de cesiones (no regalar bloques libres justo
    entonces)—, y por eso vive aquí y no duplicada en cada uno."""
    carga: dict[date, float] = {}
    for f in fechas:
        dem = sum(t.dem * t.horas for s, t in datos.turnos.items()
                  if t.prioridad >= 1 and datos.opera(s, f))
        disp = sum(1 for w in datos.trabajadores if datos.disponible(w, f))
        carga[f] = dem / disp if disp else 0.0
    return carga


class Modelo:
    def __init__(self, datos: Datos, fechas: list[date],
                 congelar: dict[tuple[str, date], str],
                 offset_equidad: dict[str, dict[str, int]],
                 cola: set[date],
                 offset_horas: dict[str, int],
                 objetivo_horas: dict[str, int],
                 objetivo_horas_fijo: dict[str, int],
                 tope_paced: dict[str, int],
                 cesiones: set[tuple[str, int]],
                 ancla_patron: date,
                 desde: date):
        self.datos = datos
        self.fechas = fechas
        # Primer día que cuenta para el LIBRO ANUAL de jornada (C9, cap prorrateado y equidad de
        # horas). El horizonte arranca el LUNES de la semana del 1 de enero para que las semanas ISO
        # estén completas, pero esos días de diciembre son jornada del año anterior y su libro está
        # cerrado: se trabajan y cuentan para lo LEGAL (C5/C6/C7, que miran la semana real), no para
        # la jornada anual de este año.
        self.desde = desde
        self.m = cp_model.CpModel()
        # Ancla GLOBAL de la rotación de patrones: fecha fija (misma para todas las ventanas del
        # horizonte) desde la que se cuenta la semana de rotación. IMPRESCINDIBLE que sea global: si se
        # tomara el inicio de cada ventana, el desfase ventana-ancla se queda constante y la rotación se
        # CONGELA (cada trabajador repite 1-2 filas todo el año).
        self.ancla_patron = ancla_patron

        # Horizonte rodante: 'cola' = fechas de contexto (ya resueltas, no se deciden);
        # 'congelar' = asignaciones fijas de esos días; 'offset_equidad' = carga acumulada previa.
        # 'offset_horas' = minutos ya trabajados en ventanas previas (libro de jornada anual, C9);
        # 'objetivo_horas' = minutos objetivo de ESTA ventana por NO-fijo (pacing blando, P_horas);
        # 'objetivo_horas_fijo' = objetivo ACUMULADO (min) de 1776 hasta esta ventana por fijo (Etapa 4).
        self.congelar = congelar
        self.offset_equidad = offset_equidad
        self.offset_horas = offset_horas
        self.objetivo_horas = objetivo_horas
        self.objetivo_horas_fijo = objetivo_horas_fijo
        # 'cesiones' = {(trabajador, ciclo)} que el NIVEL 0 decidió que se libran (ver
        # calendario_cesiones). Manda sobre el cap: los ciclos que no están aquí se trabajan.
        self.cesiones = cesiones
        self.tope_paced = tope_paced   # cap prorrateado acumulado (min) hasta el fin de la ventana, por NO-fijo
        self.cola = set(cola)
        self.retira: dict[tuple[str, date], cp_model.BoolVar] = {}   # Etapa 4: día quitado a un fijo
        self.fijos_activos: list[str] = []                          # fijos con línea congelada (retirables)
        # Clasificación de patrones por tipo de turno, para el trato de HORAS:
        #  - NOCHE (rotación solo de turnos noche): se pasan de 1776; se recortan liberando QUINCENAS
        #    enteras (activo por ciclo de 14 días), nunca días sueltos.
        #  - UVI (localizado 24h, ciclo corto): FUERA de la contabilidad de horas. Trabajan su rotación
        #    todo el año salvo vacaciones —que sí hay que cubrir— y sus contadores no son relevantes
        #    para el modelo (decisión de la empresa): ni tope anual, ni cap prorrateado, ni equidad.
        #  - resto (largos): se recortan a 1776 como el pool flexible (cap prorrateado, días sueltos ok).
        self.patrones_noche: set[str] = set()
        self.patrones_uvi: set[str] = set()
        for p, filas in datos.patrones.items():
            tipos = {datos.turnos[s].tipo for fila in filas for s in fila.values()
                     if s and s != LIBRE and s in datos.turnos}
            if tipos and tipos <= {"noche"}:
                self.patrones_noche.add(p)
            elif "24h" in tipos and len(filas) <= 2:
                self.patrones_uvi.add(p)
        # Líneas de los patrones DEDICADOS (noche y UVI): quien cubre una de ellas adopta la plaza
        # ENTERA esa semana, descansos incluidos (ver _handover_critico).
        # OJO, el criterio NO es la prioridad. H también es prioridad 3, pero eso está puesto para que
        # no se quede desatendido, no porque arrastre descansos: H sí admite cubrirse sin heredar los
        # libres de nadie. Si se le aplicara el handover, quien lo tapa un día no podría hacer su
        # propia línea el resto de la semana.
        self.lineas_criticas: set[str] = {
            s for p in (self.patrones_noche | self.patrones_uvi)
            for fila in datos.patrones.get(p, []) for s in fila.values()
            if s and s != LIBRE and s in datos.turnos}
        # Subconjunto LOCALIZADO (solo UVI): además de arrastrar los descansos, computa por SEMANA
        # entera en el libro anual (ver JORNADA_LOCALIZADO_SEMANA y _minutos_jornada). Las noches no:
        # sus turnos computan 11 h reales cada uno y sumarlos ya describe bien la carga.
        self.lineas_uvi: set[str] = {
            s for p in self.patrones_uvi
            for fila in datos.patrones.get(p, []) for s in fila.values()
            if s and s != LIBRE and s in datos.turnos}
        self._cubre_loc: dict[tuple[str, tuple[int, int]], object] = {}   # cache de _minutos_jornada

        #Conjunto de las variables del modelo x_trab_fecha_turno
        self.x: dict[tuple[str, date, str], cp_model.BoolVar] = {} 
        #Conjunto de trabajadores los cuales para esa fecha son refuerzo
        self.refuerzo: set[tuple[str, date, str]] = set() 
        #Conjunto de turnos que puede hacer un trabajador en una fecha
        self.turnos_wd: dict[tuple[str, date], list[str]] = defaultdict(list) 

        self.trabaja: dict[tuple[str, date], cp_model.BoolVar] = {}   # ¿w trabaja el día d? 1 si si 0 sino
        self.u: dict[tuple[str, date], cp_model.IntVar] = {}         # holgura de cobertura intVar n trabajadores se requieren (turno,fecha)

        self._crear_variables()
        self._pares_pactados()         # C4: encadenamientos que la rotación pactada sí permite
        self._congelar_cola()          # fija los días de contexto (horizonte rodante)
        self._c1_cobertura()
        self._c2_un_turno_dia()
        self._c4_descanso()
        self._c5_dias_consecutivos()
        self._c6_horas_semana()
        self._c7_descanso_semanal()
        self._c8_fijos()
        self._c9_jornada_anual()                  # tope anual duro (libro de horas)
        self.prescripcion = self._prescripcion_patron()   # turno de rotación por (patrón, fecha)
        self._activo_patron()                     # noches: acopla su rotación por QUINCENA (ciclo 14d)
        self._solo_rotacion_uvi()                 # UVI: solo su rotación → vacantes al cubridor, no al par
        self._handover_critico()                  # cubrir una crítica = adoptar la plaza y sus descansos
        self._warm_start_patron()                 # arranque pegado al patrón

    # -- Variables ----------------------------------------------------------- #
    def _crear_variables(self) -> None:
        d = self.datos
        for (trab, turno) in d.capacidades:
            for f in self.fechas:
                elegible, es_refuerzo = d.elegible(trab, turno, f)
                if not elegible:
                    continue
                self.x[(trab, f, turno)] = self.m.new_bool_var(f"x_{trab}_{f:%Y%m%d}_{turno}")
                self.turnos_wd[(trab, f)].append(turno)
                if es_refuerzo:
                    self.refuerzo.add((trab, f, turno))

    #Funcion que devuelve listado de turnos para los que el trabajador puede trabajar en la fecha f
    def _turnos(self, trab: str, f: date) -> list[cp_model.BoolVar]:
        return [self.x[(trab, f, s)] for s in self.turnos_wd.get((trab, f), [])]

    def _congelar_cola(self) -> None:
        """Fija los días de la cola a la solución de la ventana anterior (contexto de la costura).
        Lo asignado -> x==1; lo no asignado ese día -> todo x==0 (descansa)."""
        for f in self.cola:
            for w in self.datos.trabajadores:
                asignado = self.congelar.get((w, f))
                if asignado is not None and (w, f, asignado) in self.x:
                    self.m.add(self.x[(w, f, asignado)] == 1)
                else:
                    for s in self.turnos_wd.get((w, f), []):
                        self.m.add(self.x[(w, f, s)] == 0)


    # -- Restricciones duras ------------------------------------------------- #
    def _c1_cobertura(self) -> None:
        """Cada turno con DEMANDA real (prioridad>=1) se cubre con su demanda; la holgura u recoge lo
        no cubierto. Los turnos COMODÍN (prioridad 0, p.ej. REF CAL M/T) NO tienen demanda: son relleno
        de horas OPCIONAL — un trabajador con excedente los coge para acercarse a 1776 (lo dispara la
        equidad de horas), no hay 'demanda incumplida' que reportar. Siguen siendo asignables (sus x
        existen y compiten por C2/un-turno-al-día y C4/descanso, que deciden mañana vs tarde según los
        turnos vecinos); simplemente no generan holgura ni cuentan como cobertura -> no ensucian el
        listado de huecos con excedente que nunca fue demanda."""
        d = self.datos
        for turno, t in d.turnos.items():
            if t.prioridad == 0:
                continue                           # comodín: sin demanda ni holgura (relleno de horas)
            for f in self.fechas:
                if f in self.cola or not d.opera(turno, f):
                    continue                       # la cola es contexto: no se impone cobertura
                asignados = [self.x[(w, f, turno)] for w in d.trabajadores
                             if (w, f, turno) in self.x]
                holgura = self.m.new_int_var(0, t.dem, f"u_{turno}_{f:%Y%m%d}")
                self.u[(turno, f)] = holgura
                self.m.add(sum(asignados) + holgura == t.dem)

    def _c2_un_turno_dia(self) -> None:
        """trabaja[w,d] = Σ_s x (a lo sumo un turno al día)."""
        for trab in self.datos.trabajadores:
            for f in self.fechas:
                turnos = self._turnos(trab, f)
                self.m.add_at_most_one(turnos) #Como mucho trabajara un turno al dia

                trabaja = self.m.new_bool_var(f"trab_{trab}_{f:%Y%m%d}")
                self.trabaja[(trab, f)] = trabaja
                self.m.add(trabaja == sum(turnos))


    
    def _c4_descanso(self) -> None:
        """Descanso >= config.rmin horas entre el turno de un día y el del siguiente. DURA para todos, con una
        salvedad: los encadenamientos que la propia rotación PACTADA del trabajador contiene
        (_pares_pactados), porque los localizados 24h (22:00→22:00) los exige su diseño. Cualquier otro
        encadenamiento —en particular al salir de la rotación para cubrir otra línea— sí exige las
        12 h: es el descanso que necesita para volver a su turno."""
        incompatibles = self._pares_incompatibles()

        pactados = self.pares_c4
        for trab in self.datos.trabajadores:
            for hoy, manana in zip(self.fechas, self.fechas[1:]):
                for s1 in self.turnos_wd.get((trab, hoy), []):
                    for s2 in self.turnos_wd.get((trab, manana), []):

                        if (s1, s2) in incompatibles and (s1, s2) not in pactados:
                            self.m.add_at_most_one([
                                self.x[(trab, hoy, s1)],
                                self.x[(trab, manana, s2)],
                            ])




    def _intervalo_turno(self, fecha, turno):
        inicio = datetime.combine(fecha, turno.hora_entrada)
        fin = datetime.combine(fecha, turno.hora_salida)

        # Si la salida es menor o igual que la entrada,
        # asumimos que el turno termina al día siguiente.
        if fin <= inicio:
            fin += timedelta(days=1)

        return inicio, fin

        
    def _pares_incompatibles(self) -> set[tuple[str, str]]:
        """
        Devuelve pares (s1, s2) no encadenables en días consecutivos
        porque dejan menos de config.rmin horas de descanso.
        """
        base = date(2000, 1, 1)
        dia_siguiente = base + timedelta(days=1)
        descanso_minimo = timedelta(hours=self.datos.config.rmin)

        incompatibles = set()

        for s1, t1 in self.datos.turnos.items():
            _, fin_s1 = self._intervalo_turno(base, t1)

            for s2, t2 in self.datos.turnos.items():
                inicio_s2, _ = self._intervalo_turno(dia_siguiente, t2)

                descanso = inicio_s2 - fin_s1

                if descanso < descanso_minimo:
                    incompatibles.add((s1, s2))

        return incompatibles

    def _pares_pactados(self) -> None:
        """Encadenamientos de turnos que la rotación PACTADA de cada patrón contiene y que, por tanto,
        quedan exentos de C4 (12 h de descanso). Es la ÚNICA exención legal que sobrevive.

        Antes había una exención global por persona (`tipo == "patron"` -> sin C4/C5/C6/C7). Sobraba:
        ninguna rotación de estos datos supera 48 h ni 6 días por semana, ni se queda sin finde libre,
        así que esos tres límites se cumplen por construcción y aplicarlos no cuesta nada. C4 es
        distinto: los localizados 24 h SÍ lo incumplen por diseño (VADU47127 encadena consigo mismo,
        22:00->22:00, 0 h de descanso; PAT_MEDINA llega a -11 h), y sin esta salvedad C4 rompería el
        localizado a día sí/día no.

        La exención es del PAR DE TURNOS y de la ROTACIÓN QUE SE EJECUTA, no del trabajador: la lista
        es ÚNICA para toda la plantilla. Quien cubre una línea de localizado hace la secuencia de ESE
        patrón (lun+mar seguidos, vie+sab+dom seguidos), y debe poder hacerla entera igual que su
        titular — si no, C4 le bloquea el segundo día y queda tapando días sueltos con descansos
        forzados en medio. Cualquier OTRO encadenamiento (uno que ninguna rotación pactada contiene)
        sí exige las 12 h: es el descanso lógico para volver a su turno.
        (`src/diagnostico.py` §3 verifica, para un juego de datos nuevo, que ninguna rotación necesita
        más exenciones que esta.)"""
        self.pares_c4: set[tuple[str, str]] = set()

        def ocupado(s) -> bool:
            return bool(s) and s != LIBRE and s in self.datos.turnos

        for filas in self.datos.patrones.values():
            T = len(filas)
            for k, fila in enumerate(filas):
                for j, dia in enumerate(DIAS):
                    s1 = fila.get(dia)
                    # el domingo encadena con el lunes de la fila SIGUIENTE de la rotación
                    s2 = (fila if j < 6 else filas[(k + 1) % T]).get(DIAS[(j + 1) % 7])
                    if ocupado(s1) and ocupado(s2):
                        self.pares_c4.add((s1, s2))

    def _c5_dias_consecutivos(self) -> None:
        """Días trabajados por SEMANA ISO (lunes-domingo), NO ventana deslizante: un tramo puede cruzar
        el domingo→lunes y se cuenta por separado en cada semana. Solo semanas completas (7 días); las
        ventanas del rodante son 2 semanas ISO completas, así que el conteo es limpio.
        El tope es POR TRABAJADOR (ver _tope_dias_semana): config.cmax_pool para la plantilla flexible, y
        para los de patrón el máximo que su propia rotación pactada exige. Aplica a todos, también a
        los de patrón: su rotación lo cumple por construcción y así queda acotado el que sale de ella
        a cubrir, que es quien acumulaba rachas largas."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            dias_semana[semana(f)].append(f)
        semanas = [ds for ds in dias_semana.values() if len(ds) == 7]
        for trab in self.datos.trabajadores:
            tope = self._tope_dias_semana(trab)
            for dias in semanas:
                self.m.add(sum(self.trabaja[(trab, f)] for f in dias) <= tope)

    def _tope_dias_semana(self, trab: str) -> int:
        """Días máximos por semana ISO de este trabajador. Correturnos, mixtos y fijos: config.cmax_pool.
        Los de patrón: lo que exija su rotación pactada (nunca menos de cmax_pool, nunca más de cmax),
        para no romper un cuadrante que la empresa ya tiene acordado."""
        t = self.datos.trabajadores[trab]
        filas = self.datos.patrones.get(t.patron or "") if t.tipo == "patron" else None
        cfg = self.datos.config
        if not filas:
            return cfg.cmax_pool
        propio = max(sum(1 for dia in DIAS
                         if fila.get(dia) and fila.get(dia) != LIBRE and fila.get(dia) in self.datos.turnos)
                     for fila in filas)
        return min(cfg.cmax, max(cfg.cmax_pool, propio))

    def _dias_decision(self, libro: bool = False) -> list[date]:
        """Días que DECIDE esta ventana (fuera la cola, que ya está resuelta y congelada).

        Con `libro=True`, además solo los que cuentan para el LIBRO DEL AÑO (>= self.desde): la
        primera ventana arranca el lunes anterior al 1 de enero para tener semanas ISO completas, y
        esos días son jornada del año anterior. Lo LEGAL (C5/C6/C7) sí los cuenta —son días reales de
        una semana real—, así que esos límites usan la lista sin filtrar."""
        return [f for f in self.fechas
                if f not in self.cola and not (libro and f < self.desde)]

    def _minutos(self, trab: str, dias: list[date]) -> list:
        """Términos horas(s)*x (en minutos efectivos COMPUTABLES = jornada legal) del trabajador en
        esos días. Base del tope anual duro (C9) y de la retirada de fijos."""
        return [round(self.datos.turnos[s].horas * 60) * self.x[(trab, f, s)]
                for f in dias for s in self.turnos_wd.get((trab, f), [])]

    def _cubre_localizado(self, trab: str, sem_: tuple[int, int], loc: list):
        """Bool: ¿asume `trab` una plaza de localizado en la semana ISO `sem_`? OR exacto sobre sus
        variables de línea UVI. Cacheado: C9 y la equidad de jornada piden el mismo bool."""
        v = self._cubre_loc.get((trab, sem_))
        if v is None:
            v = self.m.new_bool_var(f"cubre_loc_{trab}_{sem_[0]}w{sem_[1]}")
            self.m.add_max_equality(v, loc)
            self._cubre_loc[(trab, sem_)] = v
        return v

    def _minutos_jornada(self, trab: str, dias: list[date], solo_reales: bool = False) -> list:
        """Términos de JORNADA del trabajador en esos días, en la moneda del LIBRO ANUAL (objetivo
        1776) — la misma que `jornada_minutos` aplica sobre un plan ya resuelto:

          · turno normal          -> horas_consumo(s)·x;
          · SEMANA con localizado -> JORNADA_LOCALIZADO_SEMANA·cubre_localizado, un único término
            por semana ISO, independiente de cuántos turnos ponga la rotación (2 o 5). Prorrateado
            si la semana está cortada por el borde de la ventana.

        NO es la jornada legal: esa la da `_minutos` (8 h por localizado) y es la que gobierna C5,
        C6 y C7. Aquí se mide lo que la plaza OCUPA, que es lo que el objetivo anual reparte.

        `solo_reales` deja fuera los turnos COMODÍN (prioridad 0): lo necesita la equidad de jornada
        (ver _desviacion_jornada), no el tope duro."""
        porsem: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in dias:
            porsem[semana(f)].append(f)
        terminos = []
        for sem_, fs in porsem.items():
            loc = [self.x[(trab, f, s)] for f in fs
                   for s in self.turnos_wd.get((trab, f), []) if s in self.lineas_uvi]
            if loc:
                minutos = round(JORNADA_LOCALIZADO_SEMANA * 60 * len(fs) / 7)
                terminos.append(minutos * self._cubre_localizado(trab, sem_, loc))
            terminos.extend(
                round(self.datos.turnos[s].horas_consumo * 60) * self.x[(trab, f, s)]
                for f in fs for s in self.turnos_wd.get((trab, f), [])
                if s not in self.lineas_uvi
                and not (solo_reales and self.datos.turnos[s].prioridad == 0))
        return terminos

    def _c6_horas_semana(self) -> None:
        """<= 48 h de trabajo efectivo por SEMANA ISO (lunes-domingo), NO ventana deslizante (igual
        criterio que C5). Solo semanas completas. Aplica a TODOS: ninguna rotación pactada pasa de
        48 h (la cumplen por construcción), y así el que cubre fuera de su rotación queda protegido —
        es lo que obliga a darle descanso tras doblar en una línea crítica."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            dias_semana[semana(f)].append(f)
        semanas = [ds for ds in dias_semana.values() if len(ds) == 7]
        for trab in self.datos.trabajadores:
            for dias in semanas:
                minutos = self._minutos(trab, dias)
                if minutos:
                    self.m.add(sum(minutos) <= self.datos.config.hmax7 * 60)

    def _c7_descanso_semanal(self) -> None:
        """Descanso semanal (art. 24), parte (b): al menos un sábado+domingo libres en cada
        ventana de 4 semanas completas. El descanso de >=2 días/semana NO se impone aquí como
        restricción dura —para permitir "sábado sí / domingo no + un día suelto entre semana", y
        que 6 días seguidos DENTRO de una misma semana sea posible pero rarísimo— sino de forma
        blanda y casi prohibitiva en el objetivo (_exceso_semanal).
        Solo se aplica a SEMANAS COMPLETAS del horizonte (las semanas truncadas del borde
        se ignoran; se cubren al alinear el horizonte a semanas / con el horizonte rodante)."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            dias_semana[semana(f)].append(f)
        # semanas completas que tengan al menos un día de ventana (las de solo-cola ya se
        # respetaron en su ventana y están fijadas)
        completas = sorted(s for s, ds in dias_semana.items()
                           if len(ds) == 7 and any(f not in self.cola for f in ds))

        for trab in self.datos.trabajadores:
            # Aplica a TODOS: las rotaciones pactadas dan un finde completo libre dentro de cada
            # ventana de 4 semanas (lo cumplen por construcción), y quien cubre fuera de su rotación
            # queda cubierto por el artículo.
            # (b) sábado+domingo libres, al menos una vez cada 4 semanas completas
            finde_libre = []
            for sem in completas:
                sab = next(d for d in dias_semana[sem] if d.weekday() == 5)
                dom = next(d for d in dias_semana[sem] if d.weekday() == 6)
                fl = self.m.new_bool_var(f"findelibre_{trab}_{sem[0]}w{sem[1]}")
                # Si trabaja sábado, no cuenta como finde libre
                self.m.add(fl == 0).only_enforce_if(self.trabaja[(trab, sab)])
                # Si trabaja domingo, no cuenta como finde libre
                self.m.add(fl == 0).only_enforce_if(self.trabaja[(trab, dom)])
                # Si sábado y domingo son libres, entonces sí cuenta como finde libre
                self.m.add(fl == 1).only_enforce_if([
                    self.trabaja[(trab,sab)].Not(),
                    self.trabaja[(trab,dom)].Not(),
                ])
                finde_libre.append(fl)
            # En cada ventana de 4 semanas completas, al menos un finde completo libre. (Antes este
            # bucle estaba FUERA del de trabajadores → solo obligaba al último no-patrón; ahora aplica
            # a todos, cerrando el incumplimiento del art. 24.)
            for i in range(len(finde_libre) - 3):
                self.m.add(sum(finde_libre[i:i + 4]) >= 1)

    def _linea_fija(self, trab: str) -> str | None:
        """Línea que cubre un fijo. La declara él mismo en trabajadores.csv (columna `linea`), igual
        que un trabajador de patrón declara su patrón. Antes se deducía rebuscando en capacidades
        cuál era su única fila con v=0, y reventaba si encontraba dos."""
        return self.datos.trabajadores[trab].linea


    def _c8_fijos(self) -> None:
        """Fijos congelados (capa 1): trabajan su línea siempre que opere y estén disponibles, con
        el ÚNICO grado de libertad de que se les puede QUITAR un día (retira[w,f]) para cuadrar horas
        a 1776 (Etapa 4). x = 1 − retira: su línea o LIBRE, nunca otro turno."""
        for w, t in self.datos.trabajadores.items():
            if t.tipo != "fijo":
                continue
            phi = self._linea_fija(w)
            # Una línea de más de 8 h no la puede sostener una sola persona 5 días/semana
            # (superaría 48 h/7d o 160 h/4sem): esos fijos se dejan libres, no se congelan.
            if phi is None or self.datos.turnos[phi].horas > 8:
                continue
            self.fijos_activos.append(w)
            for f in self.fechas:
                if f in self.cola:                 # la cola ya está fijada
                    continue
                if (w, f, phi) in self.x:
                    retira = self.m.new_bool_var(f"retira_{w}_{f:%Y%m%d}")
                    self.retira[(w, f)] = retira
                    self.m.add(self.x[(w, f, phi)] == 1 - retira)

    def _prescripcion_patron(self) -> dict[tuple[str, date], str]:
        """Turno que la rotación prescribe a cada (trabajador de patrón, fecha) de la ventana
        (incluye LIBRE). La rotación avanza una fila por semana desde el ANCLA GLOBAL (lunes fijo,
        igual para todas las ventanas); cada trabajador del grupo arranca en la fila que le fija
        `datos.offsets` (declarada en trabajadores.csv, o el orden del grupo si no se declara — ver
        cargar_datos.offsets_patron). Fuente ÚNICA para el warm-start (_warm_start_patron) y la
        fijación (_fijacion_patron)."""
        base = self.ancla_patron
        ancla = base - timedelta(days=base.weekday())        # lunes de la semana ancla
        grupos: dict[str, list[str]] = defaultdict(list)
        for w, t in self.datos.trabajadores.items():
            if t.tipo == "patron" and t.patron:
                grupos[t.patron].append(w)
        pres: dict[tuple[str, date], str] = {}
        for patron, trabs in grupos.items():
            filas = self.datos.patrones.get(patron)
            if not filas:
                continue
            T = len(filas)
            for w in sorted(trabs):
                offset = self.datos.offsets.get(w, 0)
                for f in self.fechas:
                    pres[(w, f)] = filas[(offset + (f - ancla).days // 7) % T][DIAS[f.weekday()]]
        return pres

    def _activo_patron(self) -> None:
        """SOLO patrones de NOCHE. Acopla su rotación a nivel de CICLO de 14 días (las dos filas =
        quincena, su unidad real): `activo[w, ciclo]` ∈ {0,1}, y para cada día prescrito del ciclo,
        x[w,f,phi] == activo[w,ciclo]. Así la QUINCENA se hace ENTERA o se libra ENTERA — nunca media
        quincena ni días sueltos. Además el nochero hace SOLO su rotación (x=0 en lo no prescrito). Qué
        quincenas se liberan lo decide el recorte de horas hacia 1776 (cap prorrateado + P_horas); la
        quincena liberada la cubre el pool. Los demás patrones NO pasan por aquí (fijación blanda)."""
        self.activo: dict[tuple[str, int], cp_model.BoolVar] = {}
        base = self.ancla_patron
        ancla = base - timedelta(days=base.weekday())     # mismo lunes ancla que la rotación
        prescritos: set[tuple[str, date, str]] = set()
        por_ciclo: dict[tuple[str, int], list] = defaultdict(list)
        for (w, f), turno in self.prescripcion.items():
            if self.datos.trabajadores[w].patron not in self.patrones_noche:
                continue                                  # solo noches
            if f in self.cola or turno == LIBRE:
                continue
            var = self.x.get((w, f, turno))
            if var is None:
                continue                                  # no opera / no elegible / vacaciones
            prescritos.add((w, f, turno))
            ciclo = ((f - ancla).days // 7) // 2          # índice del ciclo de 14 días (quincena)
            por_ciclo[(w, ciclo)].append(var)
        for (w, ciclo), vars_dias in por_ciclo.items():
            activo = self.m.new_bool_var(f"activo_{w}_c{ciclo}")
            self.activo[(w, ciclo)] = activo
            for var in vars_dias:
                self.m.add(var == activo)                 # todo el bloque sigue el mismo activo
            # NIVEL 0: el calendario de cesiones manda. Los bloques que decidió librar se libran
            # (activo=0) y los demás se trabajan (activo=1) — así el reparto es el planificado y
            # no el que salga de que dos compañeros lleguen al tope el mismo día.
            if self.cesiones:
                self.m.add(activo == (0 if (w, ciclo) in self.cesiones else 1))
        # NO se impone que los dos titulares de una línea turnen sus libranzas ("a lo sumo uno libra
        # por ciclo"). Se probó como restricción DURA y volvía INFACTIBLES las ventanas de diciembre:
        # ambos titulares acumulan jornada casi idéntica, así que llegan al tope a la vez, y entonces
        # uno estaba obligado a librar y el otro tenía prohibido hacerlo. Dejar la línea a los
        # cubridores ya sale caro por P1 (turno crítico, peso 300/día): si hay cubridor libre, cubrir
        # es lo barato y no hacen falta reglas extra; si no lo hay, el hueco se reporta en vez de
        # reventar la ventana entera.
        # El nochero hace SOLO su rotación: cualquier otra x suya (turno extra o su turno en día LIBRE
        # de su fila) = 0. Su cobertura (vacaciones, quincenas liberadas) la asume el pool.
        for (w, f, turno), var in self.x.items():
            if f in self.cola:
                continue
            if self.datos.trabajadores[w].patron in self.patrones_noche and (w, f, turno) not in prescritos:
                self.m.add(var == 0)

    def _solo_rotacion_uvi(self) -> None:
        """Qué puede hacer un dedicado UVI (localizado 24h) fuera de su rotación. Regla de la empresa,
        en dos mitades:

        · En un día de DESCANSO de su rotación: NADA. Su descanso es intocable — no se le llama para
          tapar un hueco, ni siquiera el de su propia línea cuando su pareja está de vacaciones. Ese
          hueco lo asume un cubridor externo o se reporta.
        · En un día de TRABAJO de su rotación: puede SUSTITUIR su turno por otra línea crítica de la
          que sea cubridor declarado (v=1 en capacidades). No trabaja de más —C2 deja un turno al
          día—: cambia de línea. Así, si VADU47127 (más prioritaria) se queda sin nadie, un dedicado
          de VADP003 puede pasarse a cubrirla y quien se queda descubierto es la línea menos
          prioritaria, que es lo que se quiere.

        Lo que se prohíbe con esto es que un dedicado haga un 24h de MÁS (saldría gratis: el desvío
        de patrón solo penaliza NO hacer lo prescrito, no hacer un extra) y acabe sobrecargado."""
        for (w, f, turno), var in self.x.items():
            if f in self.cola or self.datos.trabajadores[w].patron not in self.patrones_uvi:
                continue
            prescrito = self.prescripcion.get((w, f))
            if prescrito is None or prescrito == LIBRE:
                self.m.add(var == 0)                      # día de descanso: intocable
                continue
            if turno == prescrito:
                continue                                  # su propio turno
            cap = self.datos.capacidades.get((w, turno))
            if cap is not None and cap.v == 1 and self.datos.turnos[turno].prioridad >= 2:
                continue                                  # sustitución por otra línea crítica: sí
            self.m.add(var == 0)

    def _warm_start_patron(self) -> None:
        """Siembra la búsqueda con el patrón: cada trabajador de patrón sugiere la línea que le
        tocaría según la rotación. Es un hint, no obliga (la fijación la hace _fijacion_patron)."""
        for (w, f), turno in self.prescripcion.items():
            if f in self.cola:
                continue
            if turno != LIBRE and (w, f, turno) in self.x:
                self.m.add_hint(self.x[(w, f, turno)], 1)

    def _fijacion_patron(self) -> tuple[object, int]:
        """Fijación BLANDA del patrón (capa 1): cada trabajador de patrón debe hacer su turno de
        rotación. El desvío se penaliza en el NIVEL DE COBERTURA con peso PESO_DEV pequeño (<
        valor de cubrir cualquier turno), así el patrón se sigue SIEMPRE y solo se desplaza a
        alguien de su rotación si con ello se rescata una cobertura que si no quedaría sin cubrir
        —única razón admitida—. Al ser BLANDA (no dura) nunca vuelve infactible una costura de
        rotación o un festivo donde la línea no opera: simplemente lo absorbe como desvío. Quedan
        FUERA (sin variable, no penalizan): vacaciones, festivos sin operatividad de la línea, o
        sin capacidad ese día.

        El desvío va YA PONDERADO (PESO_DEV, o PESO_DEV_COMODIN si lo prescrito es un REF CAL): un
        refuerzo prescrito se suelta en cuanto con ello se cubre un turno real de ese día, mientras
        que una línea de verdad sigue costando 100. Devuelve (suma_desvío_ponderado, cota)."""
        devs, cota = [], 0
        for (w, f), turno in self.prescripcion.items():
            if self.datos.trabajadores[w].patron in self.patrones_noche:
                continue                # noches: las gobierna activo (quincena), no la fijación blanda
            if f in self.cola or turno == LIBRE:
                continue
            var = self.x.get((w, f, turno))
            if var is None:
                continue                # vacaciones / no opera / sin capacidad -> sin fijación
            peso = (PESO_DEV_COMODIN if self.datos.turnos[turno].prioridad == 0 else PESO_DEV)
            devs.append(peso * (1 - var))    # cuesta `peso` si el trabajador NO hace su turno
            cota += peso
        return sum(devs), cota


    # -- Objetivos ----------------------------------------------------------- #
    def _coste_cobertura(self):
        """P1: coste de turnos no cubiertos, PONDERADO por la prioridad de cobertura del turno
        (peso_cobertura(turno)·hueco). Mayor prioridad = más caro dejarlo sin cubrir → cuando hay
        que dejar huecos, caen en los turnos menos prioritarios."""
        return sum(peso_cobertura(self.datos.turnos[turno]) * holgura
                   for (turno, _), holgura in self.u.items())


    def _contribuye(self, metrica: str, turno: str, f: date) -> bool:
        """¿La asignación (turno, día) suma a la carga indeseable 'metrica'?"""
        t = self.datos.turnos[turno]
        match metrica:
            case "noche":
                return t.tipo == "noche"
            case "sabado":
                return f.weekday() == 5
            case "domingo":
                return f.weekday() == 6
            case "finde":                       # sábado+domingo juntos; ya no se usa en METRICAS
                return f.weekday() >= 5
            case "festivo":
                return self.datos.es_festivo(f, t.municipio)
            case "24h":
                return t.tipo == "24h"
            case "12h":
                return t.tipo == "12h"
            case "partido":
                return t.tipo == "partido"
            case _:
                return False

    def _grupos_equidad(self) -> dict[str, list[str]]:
        """Partición de los NO-fijos por grupo de equidad (columna `grupo` de trabajadores.csv).
        Mismos `grupo` se equiparan ENTRE SÍ (findes/festivos); los sin grupo (None) caen en un
        único pool compartido "__global__" → cuando la empresa aún no define grupos, la equidad es
        global (comportamiento anterior). Con grupos definidos, cada grupo se iguala por separado
        (p.ej. localizados en su grupo, sin arrastrar a los demás)."""
        grupos: dict[str, list[str]] = defaultdict(list)
        for w, t in self.datos.trabajadores.items():
            if t.tipo == "fijo":
                continue
            grupos[t.grupo or "__global__"].append(w)
        return grupos

    def _equidad_ponderada(self) -> tuple[dict[str, list[cp_model.IntVar]], int]:
        """Para cada métrica (sábado, domingo, festivo) y cada GRUPO de equidad: carga ACUMULADA por
        trabajador (offset previo del libro + lo asignado en la ventana; fijos fuera) y su desviación
        respecto a la media DE SU GRUPO. Equidad PLANA dentro del grupo (todos pesan igual). Los de
        solo L-V no aparecen (nunca son elegibles en finde ni festivo).

        Se miden DOS cosas por grupo y métrica, porque miden injusticias distintas:
          · la desviación L1 respecto a la media, que aprieta al conjunto;
          · el RANGO (máximo − mínimo), que es lo que se percibe como injusto. La L1 es indiferente
            entre "uno se pasa 6" y "seis se pasan 1"; el rango no, y evita que alguien acabe con 12
            domingos y otro con 3. Es lineal, a diferencia de una L2 que pediría variables producto.
        Devuelve (desviaciones por métrica, cota_p2)."""
        dias = self._dias_decision(libro=True)   # ventana, sin la cola y sin los días del año anterior
        desviaciones: dict[str, list[cp_model.IntVar]] = {m: [] for m in METRICAS}
        cota_p2 = 0

        for metrica in METRICAS:
            for gid, miembros in self._grupos_equidad().items():
                cargas = []
                for w in miembros:
                    off = self.offset_equidad.get(w, {}).get(metrica, 0)     # carga previa (libro)
                    terminos = [self.x[(w, f, s)] for f in dias
                                for s in self.turnos_wd.get((w, f), [])
                                if self._contribuye(metrica, s, f)]
                    if not terminos and off == 0:
                        continue                      # ni carga previa ni puede en la ventana
                    cota_w = off + len(terminos)
                    carga = self.m.new_int_var(off, cota_w, f"carga_{metrica}_{w}")
                    self.m.add(carga == off + sum(terminos))
                    cargas.append((w, carga, cota_w))

                if len(cargas) < 2:
                    continue                          # sin dispersión que repartir en el grupo
                n = len(cargas)                       # equipara al promedio del grupo (peso plano)
                cota_total = sum(c for _, _, c in cargas)
                carga_total = self.m.new_int_var(0, cota_total, f"total_{metrica}_{gid}")
                self.m.add(carga_total == sum(c for _, c, _ in cargas))

                # |n·carga_w − carga_total| = n·|carga_w − media| ≤ n·cota_total
                max_desv = n * cota_total
                for w, carga_w, _ in cargas:
                    desv = self.m.new_int_var(0, max_desv, f"desv_{metrica}_{w}")
                    self.m.add_abs_equality(desv, n * carga_w - carga_total)
                    desviaciones[metrica].append(desv)
                cota_p2 += LAMBDA[metrica] * n * max_desv

                # RANGO del grupo, en la MISMA escala que las desviaciones (×n) para que pese algo
                # frente a ellas: sin escalar sería un puñado de unidades contra miles.
                tope = max(c for _, _, c in cargas)
                mx = self.m.new_int_var(0, tope, f"max_{metrica}_{gid}")
                mn = self.m.new_int_var(0, tope, f"min_{metrica}_{gid}")
                self.m.add_max_equality(mx, [c for _, c, _ in cargas])
                self.m.add_min_equality(mn, [c for _, c, _ in cargas])
                escala = PESO_RANGO * n
                rango = self.m.new_int_var(0, tope * escala, f"rango_{metrica}_{gid}")
                self.m.add(rango == (mx - mn) * escala)
                desviaciones[metrica].append(rango)
                cota_p2 += LAMBDA[metrica] * tope * escala
        return desviaciones, cota_p2

    # -- C9: jornada anual (tope duro + pacing blando) ----------------------- #
    def _c9_jornada_anual(self) -> None:
        """C9 (DURA): la jornada anual no supera config.horas_objetivo, que es el límite legal y no se pasa
        ni una hora, tampoco para cubrir un turno que fuera a quedar vacío. Libro de
        horas acumuladas: minutos previos (offset_horas) + los de esta ventana <= tope. Guarda los
        términos de minutos por NO-fijo para la equidad de horas (P_horas). Los fijos también topan
        (antes estaban fuera → un fijo podía superar el tope en silencio), pero NO entran en
        _min_ventana: su jornada la cuadra _retirada_fijos quitando días (Etapa 4). Los UVI quedan
        FUERA por completo: trabajan su rotación todo el año y por acuerdo eso ES su jornada de 1776,
        computen sus turnos lo que computen.

        Se mide en minutos de JORNADA (_minutos_jornada), no de jornada legal: el tope y la equidad
        de horas tienen que hablar el MISMO idioma. Cuando C9 contaba las 8 h legales del localizado
        y la equidad contaba su ocupación real, el cubridor de una plaza de localizado quedaba corto
        para el tope duro y el modelo le seguía cargando turnos hasta llegar a 1776 de papel: en la
        práctica trabajaba semanas de más. Como la jornada de ocupación es siempre >= la legal,
        toparla en 1776 sigue garantizando el límite del convenio."""
        dias = self._dias_decision(libro=True)                     # solo la ventana, y solo este año
        self._min_ventana: dict[str, list] = {}
        for w, t in self.datos.trabajadores.items():
            if t.patron in self.patrones_uvi:
                continue        # UVI: fuera de la contabilidad de horas (ver docstring de la clase)
            terminos = self._minutos_jornada(w, dias)
            if not terminos:
                continue                                          # no puede trabajar en la ventana
            tope_min = round(self.datos.config.horas_objetivo * t.factor_jornada * 60)   # escalado por reducción de jornada
            off = self.offset_horas.get(w, 0)                     # <= tope por invariante del libro
            cap = tope_min
            paced = self.tope_paced.get(w)                        # cap prorrateado (anti front-loading, no-fijos)
            if paced is not None:
                cap = min(cap, paced)                             # rige el más restrictivo de los dos
            self.m.add(sum(terminos) <= max(0, cap - off))        # max(0,·): si va por delante del ritmo, descansa
            # Equidad de jornada hacia 1776: todos los NO-fijos (los UVI ya se saltaron arriba).
            # Fijos aparte: su jornada se cuadra por retirada de días (Etapa 4).
            if t.tipo != "fijo":
                self._min_ventana[w] = terminos

    def _desviacion_jornada(self) -> tuple[object, int]:
        """P_horas (BLANDA, EQUIDAD): penaliza que los minutos de JORNADA de la ventana se desvíen del
        objetivo prorrateado (objetivo_horas[w], derivado de config.horas_objetivo) por DEBAJO o
        por ENCIMA (desviación absoluta, simétrica). Se mide en JORNADA (_minutos_jornada), no en
        horas computadas: un localizado 24h ocupa más que sus 8 h legales y una SEMANA de plaza de
        localizado ocupa la semana entera → así no arrastra un falso déficit de horas que el modelo
        intentaría compensar cargándole turnos. Reparte la jornada de forma pareja a lo largo del año y evita que
        nadie derive hacia el tope duro. Va al nivel bajo (desempate): solo actúa cuando no
        cuesta cobertura ni equidad de findes. Fijos fuera (su jornada se cuadra por retirada de
        días, Etapa 4). Devuelve (Σ|desv|, cota).
        Nota: exceso y déficit pesan IGUAL (|·|). Si se quisiera penalizar más suave el exceso,
        separar en dos variables (déficit / exceso) con pesos distintos."""
        dias = self._dias_decision(libro=True)
        max_turno_min = max((round(t.horas_consumo * 60) for t in self.datos.turnos.values()), default=0)
        max_min = len(dias) * max_turno_min                       # cota superior de minutos de consumo
        desvs, cota = [], 0
        for w in getattr(self, "_min_ventana", {}):               # no-fijos con jornada en la ventana
            objetivo = self.objetivo_horas.get(w, 0)              # objetivo POR VENTANA (prorrateado)
            if objetivo <= 0:
                continue
            cota_w = max(objetivo, max_min)                       # |Σmin − objetivo| ≤ max(objetivo, max_min)
            # Los COMODINES (prioridad 0, REF CAL) NO cuentan aquí: si contaran, el solver los usaría
            # para cuadrar horas durante la resolución y se gastaría en relleno el presupuesto anual
            # que hace falta para cubrir el pico de vacaciones (medido: absorbían 7.136 h, más que
            # todo el sobrante del año, y dejaban a la plantilla pegada al tope en agosto). El relleno
            # se hace al final, con lo que realmente sobre (ver rellenar_refuerzos).
            terminos = self._minutos_jornada(w, dias, solo_reales=True)
            desv = self.m.new_int_var(0, cota_w, f"desvh_{w}")
            self.m.add_abs_equality(desv, sum(terminos) - objetivo)
            peso = PESO_JORNADA.get(self.datos.trabajadores[w].tipo, 1)
            desvs.append(desv * peso if peso != 1 else desv)
            cota += cota_w * peso
        return sum(desvs), cota

    def _retirada_fijos(self) -> tuple[object, int]:
        """Etapa 4 (BLANDA, ASIMÉTRICA): cuadra la jornada de los fijos a 1776 quitándoles días
        (retira). Separa la desviación de sus minutos acumulados respecto al ritmo de 1776 en dos
        partes con PESO DISTINTO: falta = cuánto queda POR DEBAJO del ritmo (PESO_FALTA_FIJO, grande)
        y exceso = cuánto se pasa POR ENCIMA (PESO_EXCESO_FIJO, pequeño):
        Σmin_ventana − resto = exceso − falta, con resto = objetivo_acum − off una CONSTANTE que
        absorbe los valores acumulados grandes (variables pequeñas, sin desbordar). La asimetría es
        clave: su línea es SUYA por defecto (falta cara → casi nunca cede estando corto), y solo una
        vez alcanzado/superado el ritmo se vuelve barato cederla (exceso barato → sí puede entonces).
        El tope anual lo garantiza C9 aparte, independiente de este peso. Devuelve (Σ costes,
        cota)."""
        dias = self._dias_decision(libro=True)
        costes, cota = [], 0
        for w in self.fijos_activos:
            objetivo_cum = self.objetivo_horas_fijo.get(w)
            if objetivo_cum is None:
                continue
            # Jornada, para hablar el mismo idioma que offset_horas/objetivo_horas_fijo. Para un fijo
            # coincide con la legal (su línea es un turno normal de <=8 h, nunca un localizado), pero
            # dejarlo explícito evita mezclar monedas en la misma ecuación.
            terminos = self._minutos_jornada(w, dias)             # = Σ min(phi)·(1 − retira)
            if not terminos:
                continue
            max_win = len(dias) * max((round(s.horas * 60) for s in self.datos.turnos.values()), default=0)
            resto = objetivo_cum - self.offset_horas.get(w, 0)    # constante: min "de pace" para esta ventana
            falta_max = max(0, resto)                             # Σmin=0 -> falta máxima = resto (si resto>0)
            exceso_max = max(0, max_win - resto)                  # Σmin=max_win -> exceso máximo
            falta = self.m.new_int_var(0, falta_max, f"falta_fijo_{w}")
            exceso = self.m.new_int_var(0, exceso_max, f"exceso_fijo_{w}")
            self.m.add(sum(terminos) - resto == exceso - falta)
            costes.append(PESO_FALTA_FIJO * falta + PESO_EXCESO_FIJO * exceso)
            cota += PESO_FALTA_FIJO * falta_max + PESO_EXCESO_FIJO * exceso_max
        return sum(costes), cota

    def _exceso_semanal(self) -> tuple[object, int]:
        """Penaliza que un trabajador supere 5 días trabajados en una semana ISO completa (=> 6
        días seguidos DENTRO de esa semana, con un solo descanso). Las rachas de 6 que cruzan la
        frontera domingo->lunes NO se penalizan: dejan cada semana en <=5. Va al NIVEL DE
        COBERTURA (peso 1 < valor de cubrir un turno, >=2), así un 6-en-semana solo se acepta si
        rescata un turno que si no quedaría sin cubrir -> posible pero rarísimo. Devuelve
        (suma_exceso, cota)."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            dias_semana[semana(f)].append(f)
        completas = [s for s, ds in dias_semana.items()
                     if len(ds) == 7 and any(f not in self.cola for f in ds)]
        excesos, cota = [], 0
        for trab in self.datos.trabajadores:
            for sem in completas:
                dias = dias_semana[sem]
                exceso = self.m.new_int_var(0, len(dias) - 5, f"exceso_{trab}_{sem[0]}w{sem[1]}")
                self.m.add(exceso >= sum(self.trabaja[(trab, f)] for f in dias) - 5)
                excesos.append(exceso)
                cota += len(dias) - 5
        return sum(excesos), cota

    def _handover_critico(self) -> None:
        """DURA: quien cubre una línea CRÍTICA una semana no hace NINGÚN otro turno esa semana.

        Cubrir una noche o un localizado no es coger unos turnos sueltos: es asumir la plaza, y la
        plaza viene con sus DESCANSOS. La rotación del UVI libra miércoles y jueves; si el cubridor
        los rellena con otra línea se ha quedado con el trabajo y no con el descanso, que es lo que
        la empresa no acepta. Sin esta regla, en 2026 el 70% de las semanas con cobertura crítica
        (44 de 63) llevaban además otros turnos.

        C4 NO basta para conseguirlo: solo mira el día siguiente a cada turno, no los libres que la
        rotación reparte más adelante en la semana. Por eso hace falta la restricción explícita.

        La semana ISO es la granularidad correcta aquí: el bloque del localizado cabe dentro de una
        (libra miércoles y jueves) y el de noche, que son 4+3 noches a caballo de dos semanas, se
        parte justo por donde toca — en cada una de las dos el cubridor hace sus noches y descansa
        el resto.

        Es DURA por decisión de la empresa: no es algo que se pueda penalizar y ceder según convenga.
        El precio a vigilar es que un turno normal que solo pudiera hacer ese cubridor se convierte
        en hueco, porque ya no puede compaginarlo."""
        if not self.lineas_criticas:
            return
        semanas: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            if f not in self.cola:
                semanas[semana(f)].append(f)
        for w in self.datos.trabajadores:
            for sem, dias in semanas.items():
                crit = [self.x[(w, f, s)] for f in dias for s in self.lineas_criticas
                        if (w, f, s) in self.x]
                if not crit:
                    continue
                otras = [self.x[(w, f, s)] for f in dias
                         for s in self.turnos_wd.get((w, f), [])
                         if s not in self.lineas_criticas and (w, f, s) in self.x]
                if not otras:
                    continue
                cubre = self.m.new_bool_var(f"cubre_crit_{w}_{sem[0]}w{sem[1]}")
                self.m.add(sum(crit) <= len(crit) * cubre)          # toca una crítica -> cubre=1
                self.m.add(sum(otras) <= len(otras) * (1 - cubre))  # entonces, nada más

    def _preferencia_cubridor(self) -> tuple[object, int]:
        """P7 (BLANDA): respeta el ORDEN entre los cubridores de una misma línea. El gestor designa
        un cubridor PRINCIPAL (v=1) porque considera que hace mejor esa línea, y suplentes (v=2, 3…)
        que solo deberían entrar si el principal no puede. Aquí se penaliza cada día cubierto por un
        suplente, con peso proporcional a lo lejos que esté del principal.

        Blanda a propósito: es una preferencia de calidad, no una restricción. Cuando el principal
        está de vacaciones, ya ocupado o descansando, el suplente entra sin más — antes eso que un
        hueco. Y al vivir en el nivel bajo, nunca desplaza cobertura ni equidad de findes.
        Devuelve (Σ (v−1)·x, cota)."""
        terminos, cota = [], 0
        for (w, f, s), var in self.x.items():
            if f in self.cola:
                continue
            cap = self.datos.capacidades.get((w, s))
            if cap is None or cap.v <= 1:
                continue                          # titular o cubridor principal: sin coste
            terminos.append((cap.v - 1) * var)
            cota += cap.v - 1
        return sum(terminos), cota

    def _inestabilidad_mixto(self) -> tuple[object, int]:
        """P5 (BLANDA, nivel de desempate): penaliza que un MIXTO use más de un turno
        distinto en días LABORABLES de DIARIO (L-V no festivos) dentro de una misma semana
        ISO —romper la "misma posición de lunes a viernes"—. Se excluyen sábados, domingos
        y festivos (por municipio del turno): ahí el mixto hace turno de finde/festivo, que
        es flexible por diseño, no su posición de diario. Solo mixtos: correturnos son
        flexibles por diseño y fijos/patrón ya tienen la posición determinada. Solo la
        ventana (la cola es contexto). Un mixto con una sola posición de diario posible esa
        semana no aporta nada (|posibles|<=1) → término ≈0 hasta que el dato dé varias
        posiciones L-V. Penalización = nº de posiciones extra tras la 1ª. Devuelve (suma, cota)."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            if f in self.cola or f.weekday() >= 5:        # solo laborables de la ventana
                continue
            dias_semana[semana(f)].append(f)

        self.inestab: dict[tuple[str, tuple[int, int]], cp_model.IntVar] = {}
        penalizaciones, cota = [], 0
        for w, t in self.datos.trabajadores.items():
            if t.tipo != "mixto":
                continue
            for sem, dias in dias_semana.items():
                # (día, turno) de diario: excluye festivos (en festivo el mixto hace turno de
                # festivo/refuerzo, otra posición, como el finde -> no es inestabilidad)
                pares = [(f, s) for f in dias for s in self.turnos_wd.get((w, f), [])
                         if not self.datos.es_festivo(f, self.datos.turnos[s].municipio)]
                posibles = sorted({s for _, s in pares})
                if len(posibles) <= 1:
                    continue                              # sin variabilidad posible
                distintos = []
                for s in posibles:
                    usa = self.m.new_bool_var(f"usa_{w}_{sem[0]}w{sem[1]}_{s}")
                    for f, ss in pares:
                        if ss == s:
                            self.m.add(usa >= self.x[(w, f, s)])   # cota inferior: basta al minimizar
                    distintos.append(usa)
                pen = self.m.new_int_var(0, len(posibles) - 1, f"inestab_{w}_{sem[0]}w{sem[1]}")
                self.m.add(pen >= sum(distintos) - 1)     # posiciones extra tras la 1ª
                self.inestab[(w, sem)] = pen
                penalizaciones.append(pen)
                cota += len(posibles) - 1
        return sum(penalizaciones), cota

    # -- Resolución (objetivo jerárquico por pesos) -------------------------- #
    def resolver(self, tiempo: int = 600, trabajadores_cpu: int = 4, log: bool = False):
        """Objetivo jerárquico en uno solo:  W1·(P1+exceso+desvío_patrón) + W2·P2 + W3·(P_horas+P5),
        con W1>W2>W3. NIVEL COBERTURA (todo con peso pequeño < valor de cubrir un turno, así solo
        se acepta si rescata cobertura): P1 turnos no cubiertos (UNIFORME, sin criticidad por tipo de
        turno) + penalización de 6 días seguidos dentro de una semana + desvío del patrón (fijación
        blanda). -> P2: equidad PLANA del nº de findes/festivos entre capaces (todos pesan igual) ->
        P_horas equidad de jornada (|desv| respecto al objetivo 1776) + P5 estabilidad posicional del
        mixto (los dos desempates comparten el nivel más bajo para no inflar la torre de pesos)."""
        p1 = self._coste_cobertura()
        p_exceso, _ = self._exceso_semanal()               # 6 días seguidos dentro de una semana
        p_dev, _ = self._fijacion_patron()                 # desvío del patrón (ya ponderado por PESO_DEV)
        desviaciones, max_p2 = self._equidad_ponderada()   # cota_p2 exacta (incluye offset)
        self.desviaciones = desviaciones                  # expuesto para el resumen tras resolver
        p2 = sum(LAMBDA[metrica] * sum(vars_desv) for metrica, vars_desv in desviaciones.items())
        p_horas, max_horas = self._desviacion_jornada()    # equidad de horas NO-fijos (|desv| vs objetivo)
        p_ret, max_ret = self._retirada_fijos()            # Etapa 4: horas de fijos (asimétrico: falta≫exceso)
        p_retira = sum(self.retira.values())               # nº de días quitados a fijos (freno a quitar de más)
        p5, max_p5 = self._inestabilidad_mixto()           # estabilidad posicional del mixto (L-V)
        p7, max_p7 = self._preferencia_cubridor()          # orden de preferencia entre cubridores

        # Cotas conservadoras del nivel bajo para escalar los pesos (W1 > max aporte del nivel bajo).
        max_low = (max_horas + max_ret + PESO_RETIRA * len(self.retira) + PESO_ESTAB * max_p5
                   + PESO_ORDEN * max_p7)
        W3 = 1
        W2 = max_low + 1
        W1 = (max_p2 * W2) + max_low + 1

        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = trabajadores_cpu
        solver.parameters.log_search_progress = log
        solver.parameters.max_time_in_seconds = tiempo
        self.m.minimize(W1 * (p1 + p_exceso + p_dev) + W2 * p2
                        + W3 * (p_horas + p_ret + PESO_RETIRA * p_retira + PESO_ESTAB * p5
                                + PESO_ORDEN * p7))
        return solver, solver.solve(self.m)

    # -- Resolución LEXICOGRÁFICA (por pasadas; no desborda a ningún horizonte) ---------- #

# --------------------------------------------------------------------------- #
#  Horizonte rodante: resuelve el periodo por ventanas cosidas
# --------------------------------------------------------------------------- #
def _plan_ventana(mod: Modelo, solver, fechas_ventana: list[date]) -> dict[tuple[str, date], str]:
    """Extrae {(trab, fecha): turno} de las decisiones de la VENTANA (ignora la cola)."""
    dias = set(fechas_ventana)
    return {(w, f): s for (w, f, s), var in mod.x.items()
            if f in dias and solver.value(var)}


def _actualizar_offset(offset: dict, mod: Modelo, plan_ventana: dict) -> None:
    """Suma al libro de equidad las cargas indeseables asignadas en la ventana (fijos fuera).
    Los días anteriores al arranque del año quedan fuera, igual que en el modelo (_dias_decision):
    son findes y festivos del año pasado y su reparto ya se cerró entonces."""
    for (w, f), turno in plan_ventana.items():
        if mod.datos.trabajadores[w].tipo == "fijo" or (mod.desde and f < mod.desde):
            continue
        for metrica in METRICAS:
            if mod._contribuye(metrica, turno, f):
                offset.setdefault(w, {})
                offset[w][metrica] = offset[w].get(metrica, 0) + 1


def _actualizar_offset_horas(offset_horas: dict, datos: Datos, plan_ventana: dict,
                             fechas_ventana: list[date]) -> None:
    """Suma al libro de jornada anual (C9 + pace) los minutos trabajados en la ventana. Incluye a
    los fijos (Etapa 4): un día retirado no aparece en el plan, así que no suma → el libro refleja
    sus horas reales tras las retiradas. Usa la MISMA moneda que _minutos_jornada (semana entera
    para las plazas de localizado); si no, el libro y el modelo se contradirían ventana a ventana."""
    for w, minutos in jornada_minutos(datos, plan_ventana, fechas_ventana).items():
        offset_horas[w] = offset_horas.get(w, 0) + minutos


def _linea_fija_de(datos: Datos, w: str) -> str | None:
    """Línea que cubre un fijo, declarada en trabajadores.csv (versión a nivel módulo)."""
    return datos.trabajadores[w].linea


def _patrones_uvi(datos: Datos) -> set[str]:
    """Patrones UVI (localizado 24h, ciclo corto <=2 filas): sus horas de patrón se ACEPTAN, no se
    recortan a 1776 (mismo criterio que Modelo, versión a nivel módulo para resolver_anual)."""
    uvi = set()
    for p, filas in datos.patrones.items():
        tipos = {datos.turnos[s].tipo for fila in filas for s in fila.values()
                 if s and s != LIBRE and s in datos.turnos}
        if "24h" in tipos and len(filas) <= 2:
            uvi.add(p)
    return uvi


def _patrones_noche(datos: Datos) -> set[str]:
    """Patrones de NOCHE (rotación solo turnos noche): se recortan por QUINCENAS enteras (activo por
    ciclo), granularidad GRUESA. Por eso quedan FUERA del cap prorrateado (tope duro por hora, que los
    sobre-recortaría al forzar una quincena de más): los recorta la equidad blanda hacia 1776, que
    aterriza en la quincena más cercana (1760). No tienen fijación ni hacen front-loading, así que el
    cap duro no les hace falta. Versión módulo para resolver_anual."""
    noche = set()
    for p, filas in datos.patrones.items():
        tipos = {datos.turnos[s].tipo for fila in filas for s in fila.values()
                 if s and s != LIBRE and s in datos.turnos}
        if tipos and tipos <= {"noche"}:
            noche.add(p)
    return noche


def _prescripcion_por_ciclo(datos: Datos, patron: str, fechas: list[date],
                            ancla: date) -> dict[str, dict[int, int]]:
    """(trabajador -> ciclo -> minutos prescritos). El CICLO (14 días desde el ancla) es el BLOQUE
    DE TRABAJO real del nochero: 4 noches de una semana + 3 de la siguiente (77 h). Los dos del
    binomio tienen su bloque dentro del mismo ciclo, desfasados (uno hace lun-jue y el otro
    vie-dom, y a la semana siguiente al revés), así que el ciclo es la unidad de cesión de ambos.
    Solo cuenta días en que la línea opera y el trabajador está disponible."""
    filas = datos.patrones.get(patron) or []
    T = len(filas)
    trabs = sorted(w for w, t in datos.trabajadores.items() if t.patron == patron)
    pres: dict[str, dict[int, int]] = {}
    for w in trabs:
        off = datos.offsets.get(w, 0)
        porciclo: dict[int, int] = defaultdict(int)
        for f in fechas:
            k = (f - ancla).days // 7
            s = filas[(off + k) % T][DIAS[f.weekday()]]
            if s and s != LIBRE and s in datos.turnos and datos.opera(s, f) and datos.disponible(w, f):
                porciclo[k // 2] += round(datos.turnos[s].horas * 60)
        pres[w] = dict(porciclo)
    return pres


def calendario_cesiones(datos: Datos) -> set[tuple[str, int]]:
    """NIVEL 0 — decide, viendo el AÑO ENTERO, qué BLOQUES libra cada trabajador de rotación
    acoplada (hoy los de noche) para bajar su jornada anual al objetivo.

    Por qué existe: su rotación prescribe más horas que el objetivo (1859 frente a 1776), así que
    cada uno debe ceder algún bloque al año. Dejar esa decisión al cap prorrateado NO funciona: los
    dos miembros de un binomio acumulan jornada casi idéntica, llegan al tope el mismo día y ceden
    A LA VEZ — en el año 2026 los cuatro nocheros cedieron en junio y dejaron 17 noches sin cubrir.
    Ninguna penalización blanda escalona a dos trabajadores simétricos: hay que decidirlo, y solo
    se puede decidir mirando el año completo.

    Reglas acordadas con la empresa:
      · la unidad de cesión es el BLOQUE DE TRABAJO entero (4 noches + 3 de la semana siguiente,
        77 h = un ciclo), nunca días sueltos: esos días libra y los cubre otro;
      · repartidos A LO LARGO DEL AÑO (uno por cada tramo igual del horizonte);
      · NUNCA en un ciclo en el que su BINOMIO esté de vacaciones (dejaría la línea entera huérfana);
      · NUNCA en un ciclo en el que TODOS sus cubridores estén de vacaciones (no habría quien cubra);
      · los dos del binomio no ceden el mismo ciclo;
      · y, entre los que quedan, NUNCA cuando la plantilla está tensa si se puede evitar.
    Lo último es lo que se añadió después de ver el año 2026: mirar solo las vacaciones del binomio
    y de los cubridores no basta, porque un ciclo puede tener cubridor libre y aun así ser el peor
    momento del año. Una cesión en agosto se cubre sacando a alguien de otro sitio, y ese otro sitio
    se queda sin cubrir: en 2026 uno de los cuatro bloques cayó el 24/08, dentro del mes que acumuló
    80 de los 122 huecos. La tensión se mide con `carga_diaria` —las horas de demanda que le tocan a
    cada persona disponible— así que no hay nada específico de agosto ni de Valladolid en la regla:
    la penalización aparece sola donde la plantilla se estrecha, sea cuando sea.

    Entre los ciclos que cumplen todo eso se eligen los que dejan la jornada anual más cerca del
    objetivo, garantizando no superarlo. Devuelve {(trabajador, ciclo)}."""
    noche = _patrones_noche(datos)
    if not noche:
        return set()
    ancla = datos.inicio - timedelta(days=datos.inicio.weekday())
    fechas = rango_fechas(ancla, datos.fin)
    nciclos = (fechas[-1] - ancla).days // 14 + 1

    # TENSIÓN de cada ciclo: cuánto se aparta su carga por persona de la media del horizonte, en
    # puntos porcentuales y solo por arriba (ceder en un ciclo flojo no penaliza, es lo deseable).
    carga = carga_diaria(datos, fechas)
    media = sum(carga.values()) / len(carga) if carga else 0.0
    tension: dict[int, int] = {}
    for k in range(nciclos):
        dias = [ancla + timedelta(days=14 * k + i) for i in range(14)]
        dias = [f for f in dias if f in carga]
        c = sum(carga[f] for f in dias) / len(dias) if dias else 0.0
        tension[k] = max(0, round(100 * (c / media - 1))) if media else 0

    def libre_todo(x: str, k: int) -> bool:
        """¿x está disponible los 14 días del ciclo k?"""
        return all(datos.disponible(x, ancla + timedelta(days=14 * k + i)) for i in range(14))

    m = cp_model.CpModel()
    cede: dict[tuple[str, int], cp_model.BoolVar] = {}
    desvs, resumen = [], []
    cubridores_global: set[str] = set()
    for patron in sorted(noche):
        pres = _prescripcion_por_ciclo(datos, patron, fechas, ancla)
        binomio = sorted(pres)
        for w in binomio:
            t = datos.trabajadores[w]
            objetivo = round(datos.config.horas_objetivo * t.factor_jornada * 60)
            hciclo = {k: v for k, v in pres[w].items() if v > 0}
            total = sum(hciclo.values())
            if total <= objetivo:
                continue                                   # no le sobra jornada: no cede nada
            lineas = {s for fila in datos.patrones[patron] for s in fila.values()
                      if s and s != LIBRE and s in datos.turnos}
            cubridores = {x for (x, s), c in datos.capacidades.items()
                          if s in lineas and c.v == 1}
            cubridores_global |= cubridores
            otros = [x for x in binomio if x != w]

            elegibles = [k for k in sorted(hciclo)
                         if all(libre_todo(o, k) for o in otros)
                         and (not cubridores or any(libre_todo(c, k) for c in cubridores))]
            if not elegibles:
                continue
            # nº de bloques a ceder: el mínimo que garantiza bajar del tope cediendo los mayores
            mayores = sorted(hciclo.values(), reverse=True)
            n = 0
            while n < len(mayores) and total - sum(mayores[:n]) > objetivo:
                n += 1
            n = min(n, len(elegibles))
            if n == 0:
                continue

            for k in elegibles:
                cede[(w, k)] = m.new_bool_var(f"cede_{w}_c{k}")
            m.add(sum(cede[(w, k)] for k in elegibles) == n)
            # REPARTO por el año: una cesión en cada tramo igual del horizonte
            for i in range(n):
                tramo = [k for k in elegibles if i * nciclos // n <= k < (i + 1) * nciclos // n]
                if tramo:
                    m.add(sum(cede[(w, k)] for k in tramo) == 1)
            cedido = sum(hciclo[k] * cede[(w, k)] for k in elegibles)
            m.add(total - cedido <= objetivo)                  # por debajo del tope, sí o sí
            desv = m.new_int_var(0, total, f"desvces_{w}")
            m.add_abs_equality(desv, total - cedido - objetivo)
            desvs.append(desv)
            resumen.append((w, total, n))

    # CAPACIDAD DE COBERTURA por ciclo. Los cubridores están COMPARTIDOS entre las líneas de noche,
    # y cubrir un bloque cedido (7 noches en 14 días) ocupa a uno entero —abandona su propia
    # rotación esa quincena—. Así que en un ciclo no pueden cederse más bloques que cubridores
    # libres haya: si no, la cesión se convierte en hueco. Esto cubre también el caso del binomio
    # (dos del mismo par jamás caben en el mismo ciclo si solo hay un cubridor) y el de dos líneas
    # hermanas cediendo a la vez, que es lo que dejó 17 noches sin cubrir en junio.
    for k in range(nciclos):
        a_la_vez = [var for (w, kk), var in cede.items() if kk == k]
        if not a_la_vez:
            continue
        libres = sum(1 for c in cubridores_global if libre_todo(c, k))
        m.add(sum(a_la_vez) <= max(1, libres))
    # y los dos del binomio nunca ceden el mismo ciclo (dejaría la línea entera huérfana)
    for patron in sorted(noche):
        trabs = sorted(w for w, t in datos.trabajadores.items() if t.patron == patron)
        for k in range(nciclos):
            par = [cede[(w, k)] for w in trabs if (w, k) in cede]
            if len(par) >= 2:
                m.add(sum(par) <= 1)

    if not cede:
        return set()
    # Los desvíos van en MINUTOS y apenas discriminan: todos los ciclos de una misma rotación
    # prescriben casi las mismas horas, así que ceder uno u otro deja la jornada anual casi igual.
    # Quien debe decidir es la tensión. Con este peso, un ciclo un 10% por encima de la carga media
    # cuesta 600 —más que cualquier diferencia de desvío que se haya observado (unos 300)—, así que
    # manda el CUÁNDO; el desvío sigue desempatando entre ciclos igual de flojos. El tope de jornada
    # no está en juego: `total - cedido <= objetivo` es duro y se cumple elija lo que elija.
    m.minimize(sum(desvs) + PESO_CESION_TENSA * sum(tension[k] * var
                                                    for (w, k), var in cede.items()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    st = solver.solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"calendario de cesiones: {solver.status_name(st)} — se sigue SIN él; las cesiones "
              f"las decidirá el cap y pueden coincidir", flush=True)
        return set()

    elegidas = {k for k, var in cede.items() if solver.value(var)}
    if resumen:
        print("Calendario de cesiones (bloques libres por exceso de jornada):", flush=True)
        for w, total, n in sorted(resumen):
            ks = sorted(k for (ww, k) in elegidas if ww == w)
            patron = datos.trabajadores[w].patron
            hciclo = _prescripcion_por_ciclo(datos, patron, fechas, ancla)[w]
            final = (total - sum(hciclo[k] for k in ks)) / 60
            cuando = ", ".join(f"{ancla + timedelta(days=14*k):%d/%m}" for k in ks)
            print(f"  {w} ({patron}): {total/60:.0f} h -> cede {n} bloque(s) [{cuando}] "
                  f"-> {final:.0f} h", flush=True)
    return elegidas


def reserva_cubridores(datos: Datos, cesiones: set[tuple[str, int]]) -> dict[str, dict[date, float]]:
    """NIVEL 0 — horas que cada CUBRIDOR debe guardarse para la cobertura crítica que aún tiene por
    delante. Devuelve {trabajador: {fecha: horas pendientes a partir de esa fecha}}.

    El problema que resuelve: la ventana de 14 días no ve el futuro, así que el cap prorrateado le
    marca a cada uno un ritmo UNIFORME. Pero el año de un cubridor tiene PICOS —las semanas en que el
    titular está de vacaciones o ha cedido su bloque— y llega a ellos con la jornada ya gastada. En
    2026 esto dejó 7 noches sin cubrir: el cubridor tenía 1769-1774 h y la noche son 11.

    Y esos picos SE SABEN antes de resolver nada: las vacaciones son dato y las cesiones las acaba de
    decidir `calendario_cesiones`. Así que se recorre el año, se marca cada día en que una línea
    crítica se queda sin ninguno de sus titulares, y se reparte esa carga entre sus cubridores por el
    ORDEN de preferencia declarado (v=1 antes que v=2). De ahí sale, para cada uno, cuántas horas de
    cobertura le quedan por delante en cada momento — que es lo que el cap debe dejarle libre.

    Es la pieza que da visión global sin agrandar el modelo: el año entra como un parámetro, no como
    medio millón de variables (el monolítico de 647.000 no encontró ni una solución en 900 s)."""
    ancla = datos.inicio - timedelta(days=datos.inicio.weekday())
    fechas = rango_fechas(ancla, datos.fin)
    criticas = {s for s, t in datos.turnos.items() if t.prioridad >= 2}
    if not criticas:
        return {}

    # Quién tiene prescrito cada día de cada línea crítica. NO vale preguntar si "algún titular está
    # disponible": los dos de un binomio se reparten la semana —uno lun-jue y otro vie-dom— así que
    # hay que mirar a quién se lo asigna SU rotación ese día concreto.
    prescrito: dict[tuple[str, date], str] = {}
    for p, filas in datos.patrones.items():
        T = len(filas)
        trabs = sorted(w for w, t in datos.trabajadores.items() if t.patron == p)
        for w in trabs:
            off = datos.offsets.get(w, 0)
            for f in fechas:
                s = filas[(off + (f - ancla).days // 7) % T][DIAS[f.weekday()]]
                if s in criticas and datos.opera(s, f):
                    prescrito[(s, f)] = w

    pendiente: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    total: dict[str, float] = defaultdict(float)
    for s in sorted(criticas):
        cubridores = sorted(((c.v, w) for (w, ss), c in datos.capacidades.items()
                             if ss == s and c.v >= 1))
        if not cubridores:
            continue
        # Lo que le CUESTA al cubridor un día de esta línea, en la moneda del libro anual: para un
        # localizado, su ocupación (horas_consumo ≈ 11.43 h), no las 8 h legales. Es una media del
        # ciclo —la semana corta ocupa más de lo que suma y la larga menos—, suficiente para
        # dimensionar la reserva; el reparto exacto por semana lo hace el modelo (_minutos_jornada).
        horas = datos.turnos[s].horas_consumo
        for f in fechas:
            if not datos.opera(s, f):
                continue
            titular = prescrito.get((s, f))
            if titular is None:
                continue                       # nadie la tiene prescrita ese día
            cede = (titular, (f - ancla).days // 14) in cesiones
            if datos.disponible(titular, f) and not cede:
                continue                       # su titular puede: no hace falta cubridor
            # De vacaciones o con el bloque cedido: hace falta un cubridor. Se reparte entre los
            # disponibles dando el día al que MENOS lleve acumulado, con el orden `v` como desempate.
            # Adjudicárselo siempre al primero por orden daría una reserva irreal —1012 h para
            # Y0945237C cuando en la práctica hace 550— y le estrangularía el cap sin motivo.
            libres = [(total[w], v, w) for v, w in cubridores if datos.disponible(w, f)]
            if libres:
                _, _, w = min(libres)
                pendiente[w][f] += horas
                total[w] += horas

    # acumulado HACIA ATRÁS: en cada fecha, lo que queda por cubrir DESPUÉS de ella
    restante: dict[str, dict[date, float]] = {}
    for w, porfecha in pendiente.items():
        acum, curva = 0.0, {}
        for f in reversed(fechas):
            curva[f] = acum                    # lo pendiente ESTRICTAMENTE después de f
            acum += porfecha.get(f, 0.0)
        restante[w] = curva
    if restante:
        print("Reserva de horas para cobertura crítica (Nivel 0):", flush=True)
        for w in sorted(total, key=lambda x: -total[x]):
            print(f"  {w}: {total[w]:.0f} h de cobertura previstas en el año", flush=True)
    return restante


def resolver_anual(datos: Datos, segundos: int = 60, hilos: int = 8,
                   log: bool = False) -> dict[tuple[str, date], str]:
    """Horizonte rodante: resuelve el año por ventanas alineadas a lunes, con cola congelada
    (costura legal) y libro de equidad acumulada. Devuelve el plan completo."""
    # El AÑO que se contabiliza es el de config.toml; el horizonte que se RESUELVE arranca el lunes
    # anterior al 1 de enero, para que las semanas ISO estén completas y los topes semanales
    # (C5/C6/C7) cuadren desde el primer día. Esos días de diciembre se planifican y se muestran,
    # pero su jornada es del año anterior: no entran ni en el libro anual ni en el prorrateo (ver
    # Modelo.desde).
    anio, fin = datos.inicio, datos.fin
    inicio = anio - timedelta(days=anio.weekday())      # alinear a lunes
    plan: dict[tuple[str, date], str] = {}
    offset: dict[str, dict[str, int]] = {}
    offset_horas: dict[str, int] = {}                   # libro de jornada anual (minutos), C9
    # días disponibles (no vacaciones) de cada trabajador en el AÑO: base del prorrateo
    dias_horizonte = rango_fechas(anio, fin)
    uvi = _patrones_uvi(datos)                          # UVI: horas de patrón aceptadas, no se recortan
    noche = _patrones_noche(datos)                      # rotación acoplada: cede bloques enteros
    # NIVEL 0: qué bloques libra cada nochero, decidido sobre el AÑO ENTERO (repartidos, sin
    # coincidir con las vacaciones del binomio ni de los cubridores). Si sale vacío se cae al
    # comportamiento anterior (lo decide el cap prorrateado, con riesgo de coincidencia).
    cesiones = calendario_cesiones(datos)
    # Horas que cada cubridor debe guardarse para la cobertura crítica que le queda por delante.
    reserva = reserva_cubridores(datos, cesiones)
    # PESO de cada día para el prorrateo de la jornada. NO se cuentan los días a pelo: la carga que
    # toca a cada persona disponible NO es uniforme a lo largo del año. En agosto la demanda es la
    # misma pero hay menos gente (vacaciones), así que cada disponible tiene que dar un ~8% MÁS de lo
    # normal; en enero, un ~6% menos. Prorratear por días transcurridos reparte bien el total del año
    # pero en el MOMENTO equivocado: da horas de sobra en invierno y las quita en verano, y el cap
    # corta justo cuando más falta hace (medido en 2026: 126 de los 135 huecos de agosto tenían gente
    # libre a la que solo le faltaban horas). Se pondera por carga esperada = demanda del día entre
    # personas disponibles ese día.
    carga_dia = carga_diaria(datos, dias_horizonte)

    def peso(w: str, dias: list[date]) -> float:
        """Carga esperada que le corresponde a w en esos días (0 en los que no está disponible, y 0
        en los del año anterior: no están en `carga_dia` porque no entran en el reparto de 1776)."""
        return sum(carga_dia[f] for f in dias if f in carga_dia and datos.disponible(w, f))

    disp_año = {w: peso(w, dias_horizonte)
                for w, t in datos.trabajadores.items() if t.tipo != "fijo" and t.patron not in uvi}
    # Etapa 4: días ELEGIBLES de cada fijo congelado (línea ≤8h) sobre el año; base del prorrateo del
    # objetivo 1776 acumulado (los fijos se cuadran quitando días de su propia línea).
    fijo_elig: dict[str, list[date]] = {}
    for w, t in datos.trabajadores.items():
        if t.tipo != "fijo":
            continue
        phi = _linea_fija_de(datos, w)
        if phi is None or datos.turnos[phi].horas > 8:
            continue
        fijo_elig[w] = [f for f in dias_horizonte if datos.elegible(w, phi, f)[0]]
    ini_v, v = inicio, 0
    while ini_v <= fin:
        fin_v = min(ini_v + timedelta(days=DIAS_VENTANA - 1), fin)
        fechas_cola = [f for f in rango_fechas(ini_v - timedelta(days=DIAS_COLA),
                                               ini_v - timedelta(days=1)) if f >= inicio]
        fechas_ventana = rango_fechas(ini_v, fin_v)
        cola = set(fechas_cola)
        congelar = {(w, f): plan[(w, f)] for (w, f) in plan if f in cola}

        # Objetivo de jornada de la ventana: prorrateo de config.horas_objetivo (meta blanda) por la
        # CARGA que le toca en ella, no por días (ver carga_dia). Así la equidad y el cap hablan el
        # mismo idioma: en las ventanas cargadas se espera más de cada uno, y en las flojas menos.
        objetivo_horas = {}
        for w, peso_total in disp_año.items():
            if peso_total == 0:
                continue
            peso_v = peso(w, fechas_ventana)
            factor = datos.trabajadores[w].factor_jornada        # reducción de jornada
            objetivo_horas[w] = round(datos.config.horas_objetivo * factor * 60 * peso_v / peso_total)

        # Etapa 4: objetivo ACUMULADO (min) de 1776 de cada fijo hasta el FIN de esta ventana
        # (prorrateo por días elegibles transcurridos). _retirada_fijos lo usa como línea de ritmo.
        objetivo_horas_fijo = {}
        for w, elig in fijo_elig.items():
            if not elig:
                continue
            factor = datos.trabajadores[w].factor_jornada
            elig_hasta = sum(1 for f in elig if f <= fin_v)
            objetivo_horas_fijo[w] = round(datos.config.horas_objetivo * factor * 60 * elig_hasta / len(elig))

        # Cap PRORRATEADO (no-fijos): jornada acumulada hasta el FIN de esta ventana <= ritmo hacia el
        # OBJETIVO 1776 según la CARGA ya transcurrida (ver carga_dia) + colchón. Tope DURO por
        # ventana: (a) evita agotar horas antes de diciembre y (b) hace de 1776 un techo blando (se
        # cruza como mucho por el colchón). El tope anual lo impone C9 aparte.
        tope_paced = {}
        for w, peso_total in disp_año.items():
            t = datos.trabajadores[w]
            if peso_total == 0:
                continue
            if cesiones and t.patron in noche:
                continue      # su jornada la gobierna el calendario de cesiones, no el cap
            peso_hasta = peso(w, rango_fechas(anio, fin_v))
            factor = t.factor_jornada
            # Colchón (adelanto permitido sobre el ritmo de 1776), por tipo:
            #  · CESIÓN GRUESA (noches: solo pueden recortar quincenas enteras): COLCHON_NOCHE_H, para
            #    que puedan oscilar alrededor del ritmo en vez de incumplirlo desde la primera semana.
            # (Se probó a dar el colchón del pool a los CUBRIDORES de líneas críticas, por absorber
            #  ellos los picos: salió peor en todo. No dedican la holgura a las noches —el colchón
            #  afloja el cap en general y el solver la gasta en cualquier cobertura—, así que llegaban
            #  igual de justos a noviembre y además descolocaban junio: 2 huecos críticos -> 6, y la
            #  jornada se iba a 1787 con sigma 4.0.)
            #  · PATRONES largos: 0 → recortan días sueltos, así que siguen el ritmo de cerca.
            #  · POOL flexible: COLCHON_PACE_H, holgura para picos de cobertura.
            if t.patron in noche:
                colchon = COLCHON_NOCHE_H
            elif t.tipo == "patron":
                colchon = 0
            else:
                colchon = COLCHON_PACE_H
            tope_paced[w] = round((datos.config.horas_objetivo * peso_hasta / peso_total + colchon) * factor * 60)
            # RESERVA (Nivel 0): a un cubridor no se le deja gastar las horas que va a necesitar para
            # la cobertura crítica que aún tiene por delante. Sin esto llegaba a los picos con la
            # jornada agotada —1769-1774 h con noches de 11— y la línea se quedaba vacía.
            pendiente = reserva.get(w, {}).get(fin_v)
            if pendiente:
                tope_paced[w] = min(tope_paced[w],
                                    round((datos.config.horas_objetivo * factor - pendiente) * 60))

        mod = Modelo(datos, fechas_cola + fechas_ventana,
                     congelar=congelar, offset_equidad=offset, cola=cola,
                     offset_horas=offset_horas, objetivo_horas=objetivo_horas,
                     objetivo_horas_fijo=objetivo_horas_fijo, tope_paced=tope_paced,
                     cesiones=cesiones, ancla_patron=inicio,   # ancla GLOBAL fija: la rotación es consistente entre ventanas
                     desde=anio)                          # ...y el libro de horas empieza el 1 de enero
        solver, st = mod.resolver(tiempo=segundos, trabajadores_cpu=hilos, log=log)

        # GUARDIA: sin solución, solver.value() devuelve BASURA en silencio (se llegó a coser un
        # diciembre entero de valores arbitrarios y a reportar coberturas negativas de 1e19). Se corta
        # aquí y se devuelve lo cosido hasta ahora, diciendo qué ventana falló.
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print(f"ventana {v+1:>2}  {ini_v:%d/%m}–{fin_v:%d/%m}  {solver.status_name(st)}: "
                  f"sin solución. Se detiene el rodante y se devuelve el plan hasta {ini_v:%d/%m} "
                  f"({len(plan)} asignaciones).", flush=True)
            return plan

        pv = _plan_ventana(mod, solver, fechas_ventana)
        plan.update(pv)
        _actualizar_offset(offset, mod, pv)
        _actualizar_offset_horas(offset_horas, datos, pv,
                                 [f for f in fechas_ventana if f >= anio])

        v += 1
        # u ya solo contiene turnos con DEMANDA real: los comodín REF CAL no generan holgura
        # (ver _c1_cobertura), así que esta cobertura es directamente la prioritaria.
        huecos = sum(solver.value(u) for u in mod.u.values())
        dem = sum(datos.turnos[t].dem for (t, _) in mod.u)
        print(f"ventana {v:>2}  {ini_v:%d/%m}–{fin_v:%d/%m}  {solver.status_name(st):<9} "
              f"cobertura {100*(dem-huecos)/dem:5.1f}%  ({huecos} huecos)", flush=True)
        ini_v = fin_v + timedelta(days=1)

    # PASADA FINAL: repartir los comodines (REF CAL) entre quienes quedaron por debajo del objetivo.
    # Va aquí y no dentro del modelo para que el relleno use lo que SOBRA y no compita con la
    # cobertura por el presupuesto anual de horas (ver rellenar_refuerzos).
    rellenar_refuerzos(datos, plan)
    return plan


def rellenar_refuerzos(datos: Datos, plan: dict[tuple[str, date], str]) -> int:
    """PASADA FINAL de relleno: reparte los turnos COMODÍN (prioridad 0, los REF CAL) entre quienes
    han quedado por DEBAJO del objetivo de jornada, una vez la cobertura real ya está decidida.

    Va al final a propósito. Cuando el relleno entra en el modelo compite por el mismo presupuesto
    anual que la cobertura: el solver lo usa para cuadrar horas en primavera y luego, en el pico de
    vacaciones, la plantilla está pegada al tope y no puede doblar. Medido en el año 2026: los REF
    CAL absorbían 7.136 h —más que las 6.828 h de sobrante estructural— y la cobertura caía del
    99.5% al 98.6%. Decidiendo el relleno DESPUÉS, solo se reparte lo que de verdad sobra.

    El reparto NO es uniforme: en cada ronda se sirve al que va más corto, así los refuerzos se
    concentran donde están los desfases de horas. Respeta un turno al día, el descanso entre
    jornadas (C4), el tope de días por semana, las 48 h semanales (C6) y el tope anual. Los comodines
    solo operan L-V, así que el descanso de fin de semana (C7) no se ve afectado.
    Devuelve el nº de refuerzos asignados."""
    comodines = [s for s, t in datos.turnos.items() if t.prioridad == 0]
    if not comodines:
        return 0
    # Semanas en que alguien cubre una línea de noche o UVI: ahí adoptó la plaza entera y sus
    # descansos (ver Modelo._handover_critico), así que el relleno tampoco puede meterle un
    # refuerzo. Esta pasada corre fuera del modelo y no lo sabría por su cuenta.
    lineas_criticas = {s for p_ in (_patrones_noche(datos) | _patrones_uvi(datos))
                       for fila in datos.patrones.get(p_, []) for s in fila.values()
                       if s and s != LIBRE and s in datos.turnos}
    sem_bloqueada = {(w, semana(f)) for (w, f), s in plan.items() if s in lineas_criticas}
    # Solo el AÑO: los días del lunes anterior al 1 de enero son jornada del año pasado. Ni cuentan
    # en el libro ni se rellenan (un refuerzo ahí no acercaría a nadie a sus 1776 de este año).
    # Los contadores SEMANALES sí se construyen con el plan entero: la semana ISO es la real.
    fechas = datos.fechas

    # `horas` = jornada acumulada contra el objetivo anual -> moneda del libro (semana entera para
    # las plazas de localizado). `horas_sem` = jornada LEGAL de la semana, que es lo que topa config.hmax7.
    horas: dict[str, float] = defaultdict(float)
    horas.update({w: m / 60 for w, m in jornada_minutos(datos, plan, fechas).items()})
    dias_sem: dict[tuple[str, tuple[int, int]], int] = defaultdict(int)
    horas_sem: dict[tuple[str, tuple[int, int]], float] = defaultdict(float)
    for (w, f), s in plan.items():
        dias_sem[(w, semana(f))] += 1
        horas_sem[(w, semana(f))] += datos.turnos[s].horas

    def tope_dias(w: str) -> int:
        t = datos.trabajadores[w]
        filas = datos.patrones.get(t.patron or "") if t.tipo == "patron" else None
        if not filas:
            return datos.config.cmax_pool
        propio = max(sum(1 for dia in DIAS if fila.get(dia) and fila.get(dia) != LIBRE
                         and fila.get(dia) in datos.turnos) for fila in filas)
        return min(datos.config.cmax, max(datos.config.cmax_pool, propio))

    def descanso_ok(w: str, f: date, s: str) -> bool:
        """¿Deja el turno s en el día f al menos config.rmin horas con el turno del día anterior y el del
        siguiente? (los comodines no aparecen en ninguna rotación pactada, así que no hay exención)"""
        t = datos.turnos[s]
        for delta in (-1, 1):
            vecino = plan.get((w, f + timedelta(days=delta)))
            if vecino is None:
                continue
            t1, t2 = (datos.turnos[vecino], t) if delta == -1 else (t, datos.turnos[vecino])
            ini1 = datetime.combine(date(2000, 1, 1), t1.hora_entrada)
            fin1 = datetime.combine(date(2000, 1, 1), t1.hora_salida)
            if fin1 <= ini1:
                fin1 += timedelta(days=1)
            ini2 = datetime.combine(date(2000, 1, 2), t2.hora_entrada)
            if (ini2 - fin1).total_seconds() / 3600 < datos.config.rmin:
                return False
        return True

    asignados = 0
    while True:
        # a quién le falta más jornada (solo quien esté por debajo del objetivo)
        faltan = sorted(((datos.config.horas_objetivo * datos.trabajadores[w].factor_jornada - horas[w], w)
                         for w in datos.trabajadores),
                        reverse=True)
        for falta, w in faltan:
            if falta <= 0:
                return _fin_relleno(asignados)
            t = datos.trabajadores[w]
            tope = datos.config.horas_objetivo * t.factor_jornada
            hueco = None
            for f in fechas:
                if (w, f) in plan or not datos.disponible(w, f):
                    continue
                sem = semana(f)
                if (w, sem) in sem_bloqueada:
                    continue                  # esa semana cubre una crítica: descansa, no se rellena
                if dias_sem[(w, sem)] >= tope_dias(w):
                    continue
                for s in comodines:
                    if not datos.elegible(w, s, f)[0]:
                        continue
                    h = datos.turnos[s].horas
                    if horas[w] + h > tope or horas_sem[(w, sem)] + h > datos.config.hmax7:
                        continue
                    if not descanso_ok(w, f, s):
                        continue
                    hueco = (f, s, h, sem)
                    break
                if hueco:
                    break
            if hueco is None:
                continue                      # este no puede coger más: se prueba con el siguiente
            f, s, h, sem = hueco
            plan[(w, f)] = s
            horas[w] += h
            dias_sem[(w, sem)] += 1
            horas_sem[(w, sem)] += h
            asignados += 1
            break                             # vuelve a ordenar: siempre sirve al que va más corto
        else:
            return _fin_relleno(asignados)


def _fin_relleno(asignados: int) -> int:
    print(f"Relleno final de refuerzos: {asignados} turnos comodín asignados "
          f"a quienes iban por debajo del objetivo", flush=True)
    return asignados

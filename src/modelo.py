"""
modelo.py — Construcción del modelo CP-SAT de cuadrantes.

Sobre una ventana de fechas construye variables x[w,d,s] (solo elegibles) y las
restricciones duras
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta, time, datetime

from cargar_datos import DIAS, DATA, LIBRE, Datos, Turno, cargar

from ortools.sat.python import cp_model

FECHA_INI = date(2026, 1, 1)
FECHA_FIN = date(2026, 1, 31)

# Parámetros legales (V Convenio CyL)
RMIN = 12          # descanso mínimo entre jornadas (h)                    — C4
HMAX7 = 48         # máx. trabajo efectivo por semana ISO (h)              — C6
# (El antiguo límite cuatrisemanal de 160 h se eliminó: redundante con el semanal para este convenio.)
HORAS_OBJETIVO = 1776  # jornada anual objetivo (h): meta de EQUIDAD (blanda), se persigue sin obligar
HMAX_AÑO = 1826        # tope legal anual (h): límite DURO, no sobrepasable (aplica al año completo)
CMAX = 6           # máx. días consecutivos trabajados

# Cap PRORRATEADO (dos funciones a la vez): además del tope legal duro (1826), la jornada acumulada
# de cada NO-fijo hasta el fin de cada ventana se limita al ritmo lineal hacia el OBJETIVO 1776
# (prorrateado por días disponibles transcurridos) + COLCHON_PACE_H. Efecto doble: (a) anti
# front-loading → nadie agota horas antes de diciembre (mata el acantilado, reparte huecos homogéneo);
# (b) 1776 pasa a ser un TECHO BLANDO → casi nadie lo cruza y, si lo hace, por poco (≤ colchón) y solo
# cuando cubrir lo exige. COLCHON_PACE_H = h que se permite POR ENCIMA del ritmo de 1776 (el "por
# poco"; también da holgura para picos de demanda). El tope legal 1826 (C9) sigue como respaldo duro.
# Subir el colchón = más cobertura pero más gente por encima de 1776; bajarlo = lo contrario.
COLCHON_PACE_H = 24

# Penalización de cobertura (P1): UNIFORME por hueco (sin criticidad por tipo de turno; todo hueco
# pesa igual). > 1 para que rescatar una cobertura domine sobre relajar una racha de 6 días o
# desviar el patrón. (En el futuro se puede volver a diferenciar por tipo de turno.)
PESO_COBERTURA = 10


def peso_cobertura(t: Turno) -> int:
    """Coste de dejar SIN cubrir una unidad de este turno (P1). Usa (prioridad+1), NO prioridad
    directamente: si se multiplicase por prioridad a secas, un turno con prioridad=0 costaría
    SIEMPRE cero → el modelo no se molestaría nunca en cubrirlo (comprobado: 0% cubierto a
    propósito). Con +1, prioridad=0 sigue siendo el escalón más bajo (dominado por CUALQUIER
    turno de prioridad>=1) pero con incentivo NO NULO: se cubre cuando sobra margen, que es
    justamente la función de un turno "comodín" (p.ej. REF CAL: herramienta para asignar horas
    de ayuda a quien las necesite, no cobertura real). El orden relativo entre prioridades se
    conserva (0<1<3 → 10<20<40); no requiere tocar turnos.csv ni casos especiales por turno."""
    return PESO_COBERTURA * (t.prioridad + 1)

# Equidad (P2): se equipara al PROMEDIO el nº de findes y de festivos entre los trabajadores capaces
# de cubrirlos. Los de SOLO L-V quedan fuera solos (nunca son elegibles en finde/festivo). Peso IGUAL
# para todos los trabajadores (sin favorecer perfiles) y para ambas métricas (sin favorecer tipo de
# día). LAMBDA queda como dial por si en el futuro se quiere ponderar finde vs festivo.
METRICAS = ("finde", "festivo")
LAMBDA = {"finde": 1, "festivo": 1}

# P5 (desempate): "vale" de evitar que un MIXTO cambie de turno/posición dentro de una misma semana
# laboral (L-V). Correturnos exentos (flexibles por diseño). Tunable.
PESO_ESTAB = 5

# Fijación del patrón (NIVEL DE COBERTURA): "vale" de sacar a un trabajador de patrón de su rotación.
# Los patrones están PACTADOS con los sindicatos: se priorizan por ENCIMA de cobertura y equidad,
# salvo vacaciones u otra imposibilidad. > máximo peso_cobertura posible (PESO_COBERTURA·(prioridad
# máx.+1)) para que desviar a alguien de su patrón sea SIEMPRE más caro que dejar sin cubrir incluso
# el turno más crítico. BLANDO, no restricción dura: las leyes SÍ siguen mandando (descanso, días
# consecutivos, horas/semana no se tocan; si la rotación choca con una de ellas, el patrón cede ahí,
# nunca al revés) — evita que una semana concreta imposible vuelva INFACTIBLE toda la ventana.
# Con datos reales (2026-07-10) el peso simétrico (=1) dejaba un turno localizado UVI compartido por
# 2 dedicados + un tercero-comodín: el tercero cubría 39/368 días aun con AMBOS dedicados disponibles
# y libres, porque desviar a cualquiera de su patrón costaba casi nada frente al valor de cobertura.
PESO_DEV = 100

# Etapa 4 (nivel bajo): "vale" de quitarle un día a un fijo. Pequeño, solo para que no retire días
# gratis: retira únicamente cuando baja el exceso de horas sobre el objetivo (1776). << horas/día.
PESO_RETIRA = 1

# Etapa 4 (ASIMÉTRICO): coste por minuto de que un fijo quede POR DEBAJO del ritmo hacia 1776 (falta)
# frente a por ENCIMA (exceso). falta >> exceso: su línea es SUYA por defecto — solo la cede una vez
# alcanzado/superado el ritmo, nunca mientras va corto. Con datos reales (2026-07-10) el peso simétrico
# (ambos=1) dejaba su línea como recurso COMPARTIDO ordinario, indiferente en cobertura a quién la haga:
# un fijo perdió 42 días/año (-272h de 1776) porque cederlos ayudaba la equidad de otros tanto como le
# perjudicaba a él. Con falta≫exceso, ceder estando corto es casi siempre más caro que cualquier
# beneficio ajeno en ese nivel. El tope legal 1826 lo garantiza C9 aparte, independiente de este peso.
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


class Modelo:
    def __init__(self, datos: Datos, fechas: list[date],
                 congelar: dict[tuple[str, date], str] | None = None,
                 offset_equidad: dict[str, dict[str, int]] | None = None,
                 cola: set[date] | None = None,
                 offset_horas: dict[str, int] | None = None,
                 objetivo_horas: dict[str, int] | None = None,
                 objetivo_horas_fijo: dict[str, int] | None = None,
                 tope_paced: dict[str, int] | None = None,
                 ancla_patron: date | None = None):
        self.datos = datos
        self.fechas = fechas
        self.m = cp_model.CpModel()
        # Ancla GLOBAL de la rotación de patrones: fecha fija (misma para todas las ventanas del
        # horizonte) desde la que se cuenta la semana de rotación. IMPRESCINDIBLE que sea global: si se
        # tomara el inicio de cada ventana, el desfase ventana-ancla se queda constante y la rotación se
        # CONGELA (cada trabajador repite 1-2 filas todo el año). Si None, cae al inicio de la ventana
        # (solo válido para un modelo de una sola ventana, p.ej. la prueba de __main__).
        self.ancla_patron = ancla_patron

        # Horizonte rodante: 'cola' = fechas de contexto (ya resueltas, no se deciden);
        # 'congelar' = asignaciones fijas de esos días; 'offset_equidad' = carga acumulada previa.
        # 'offset_horas' = minutos ya trabajados en ventanas previas (libro de jornada anual, C9);
        # 'objetivo_horas' = minutos objetivo de ESTA ventana por NO-fijo (pacing blando, P_horas);
        # 'objetivo_horas_fijo' = objetivo ACUMULADO (min) de 1776 hasta esta ventana por fijo (Etapa 4).
        self.congelar = congelar or {}
        self.offset_equidad = offset_equidad or {}
        self.offset_horas = offset_horas or {}
        self.objetivo_horas = objetivo_horas or {}
        self.objetivo_horas_fijo = objetivo_horas_fijo or {}
        self.tope_paced = tope_paced or {}   # cap prorrateado acumulado (min) hasta el fin de la ventana, por NO-fijo
        self.cola = set(cola) if cola is not None else {f for (_, f) in self.congelar}
        self.retira: dict[tuple[str, date], cp_model.BoolVar] = {}   # Etapa 4: día quitado a un fijo
        self.fijos_activos: list[str] = []                          # fijos con línea congelada (retirables)
        # Clasificación de patrones por tipo de turno, para el trato de HORAS:
        #  - NOCHE (rotación solo de turnos noche): se pasan de 1776; se recortan liberando QUINCENAS
        #    enteras (activo por ciclo de 14 días), nunca días sueltos.
        #  - UVI (localizado 24h, ciclo corto): sus horas de patrón+vacaciones se ACEPTAN aunque excedan
        #    el límite; NO se recortan (su consumo alto es la naturaleza del localizado).
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

        #Conjunto de las variables del modelo x_trab_fecha_turno
        self.x: dict[tuple[str, date, str], cp_model.BoolVar] = {} 
        #Conjunto de trabajadores los cuales para esa fecha son refuerzo
        self.refuerzo: set[tuple[str, date, str]] = set() 
        #Conjunto de turnos que puede hacer un trabajador en una fecha
        self.turnos_wd: dict[tuple[str, date], list[str]] = defaultdict(list) 

        self.trabaja: dict[tuple[str, date], cp_model.BoolVar] = {}   # ¿w trabaja el día d? 1 si si 0 sino
        self.u: dict[tuple[str, date], cp_model.IntVar] = {}         # holgura de cobertura intVar n trabajadores se requieren (turno,fecha)

        self._crear_variables()
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
        """Cada turno operativo se cubre con su demanda; la holgura u recoge lo no cubierto."""
        d = self.datos
        for turno, t in d.turnos.items():
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
        """Descanso >= RMIN horas entre el turno de un día y el del siguiente. DURA para la plantilla
        general, pero los PATRONES quedan exentos (conciliación pactada, ver _exento_legal): los
        localizados UVI son 24h on-call (22:00→22:00) y su rotación encadena días consecutivos que
        dejarían <12h; sin la exención, C4 rompía el localizado a día sí/día no."""
        incompatibles = self._pares_incompatibles()

        for trab in self.datos.trabajadores:
            if self._exento_legal(trab):
                continue
            for hoy, manana in zip(self.fechas, self.fechas[1:]):
                for s1 in self.turnos_wd.get((trab, hoy), []):
                    for s2 in self.turnos_wd.get((trab, manana), []):

                        if (s1, s2) in incompatibles:
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
        porque dejan menos de RMIN horas de descanso.
        """
        base = date(2000, 1, 1)
        dia_siguiente = base + timedelta(days=1)
        descanso_minimo = timedelta(hours=RMIN)

        incompatibles = set()

        for s1, t1 in self.datos.turnos.items():
            _, fin_s1 = self._intervalo_turno(base, t1)

            for s2, t2 in self.datos.turnos.items():
                inicio_s2, _ = self._intervalo_turno(dia_siguiente, t2)

                descanso = inicio_s2 - fin_s1

                if descanso < descanso_minimo:
                    incompatibles.add((s1, s2))

        return incompatibles

    def _exento_legal(self, trab: str) -> bool:
        """¿El trabajador está exento de los límites legales GENÉRICOS (C5 días consecutivos, C6/C6b
        horas semanales/cuatrisemanales, C7 descanso semanal)? Los de PATRÓN sí: su rotación es una
        CONCILIACIÓN pactada que por diseño puede superar esos límites (p.ej. las noches encadenan 7
        días / 77 h una semana de cada dos). Siguen sujetos a C4 (descanso mínimo entre turnos) y C9
        (tope anual duro 1826), que NO se relajan."""
        return self.datos.trabajadores[trab].tipo == "patron"

    def _c5_dias_consecutivos(self) -> None:
        """Como mucho CMAX días trabajados por SEMANA ISO (lunes-domingo), NO ventana deslizante: un
        tramo puede cruzar el domingo→lunes (p.ej. jue-dom + lun-jue) y se cuenta por separado en cada
        semana, mientras cada semana deje >=1 día libre. Solo semanas completas (7 días) del horizonte;
        las ventanas del rodante son 2 semanas ISO completas, así que el conteo es limpio. Los de
        patrón quedan exentos (rotación pactada; ver _exento_legal)."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            dias_semana[semana(f)].append(f)
        semanas = [ds for ds in dias_semana.values() if len(ds) == 7]
        for trab in self.datos.trabajadores:
            if self._exento_legal(trab):
                continue
            for dias in semanas:
                self.m.add(sum(self.trabaja[(trab, f)] for f in dias) <= CMAX)

    def _minutos(self, trab: str, dias: list[date]) -> list:
        """Términos horas(s)*x (en minutos efectivos COMPUTABLES = jornada legal) del trabajador en
        esos días. Base del tope duro 1826 (C9) y de la retirada de fijos."""
        return [round(self.datos.turnos[s].horas * 60) * self.x[(trab, f, s)]
                for f in dias for s in self.turnos_wd.get((trab, f), [])]

    def _minutos_consumo(self, trab: str, dias: list[date]) -> list:
        """Términos horas_consumo(s)*x (minutos de CONSUMO de capacidad) del trabajador en esos días.
        Base de la EQUIDAD de jornada (no de lo legal): un localizado 24h consume CONSUMO_LOCALIZADO
        (≈11.43 h), no sus 8 h computadas, así el localizado puro y quien lo cubre de forma excepcional
        quedan ~1776 de CONSUMO y no se les penaliza el defecto de horas computadas."""
        return [round(self.datos.turnos[s].horas_consumo * 60) * self.x[(trab, f, s)]
                for f in dias for s in self.turnos_wd.get((trab, f), [])]

    def _c6_horas_semana(self) -> None:
        """<= 48 h de trabajo efectivo por SEMANA ISO (lunes-domingo), NO ventana deslizante (igual
        criterio que C5). Solo semanas completas. Patrón exento (noches pactadas; ver _exento_legal)."""
        dias_semana: dict[tuple[int, int], list[date]] = defaultdict(list)
        for f in self.fechas:
            dias_semana[semana(f)].append(f)
        semanas = [ds for ds in dias_semana.values() if len(ds) == 7]
        for trab in self.datos.trabajadores:
            if self._exento_legal(trab):
                continue
            for dias in semanas:
                minutos = self._minutos(trab, dias)
                if minutos:
                    self.m.add(sum(minutos) <= HMAX7 * 60)

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
            if self._exento_legal(trab):
                continue                       # patrón: su descanso lo define la rotación pactada
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
        """Devuelve la única línea de capacidad normal de un fijo, si existe."""
        turnos = []

        for (w, turno), cap in self.datos.capacidades.items():
            if w != trab:
                continue

            if cap.v == 0 and (cap.lv or cap.sab or cap.dom or cap.fest):
                turnos.append(turno)

        if not turnos:
            return None

        if len(turnos) > 1:
            raise ValueError(
                f"El trabajador fijo {trab} tiene más de una línea fija: {turnos}"
            )

        return turnos[0]


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
        igual para todas las ventanas); cada trabajador del grupo arranca en una fila distinta (offset
        por orden en el grupo). Fuente ÚNICA para el warm-start (_warm_start_patron) y la fijación
        (_fijacion_patron)."""
        base = self.ancla_patron or self.fechas[0]           # ancla global (rodante) o inicio de ventana (1 sola)
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
            for offset, w in enumerate(sorted(trabs)):
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
        base = self.ancla_patron or self.fechas[0]
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
                self.m.add(var == activo)                 # toda la quincena sigue el mismo activo
        # El nochero hace SOLO su rotación: cualquier otra x suya (turno extra o su turno en día LIBRE
        # de su fila) = 0. Su cobertura (vacaciones, quincenas liberadas) la asume el pool.
        for (w, f, turno), var in self.x.items():
            if f in self.cola:
                continue
            if self.datos.trabajadores[w].patron in self.patrones_noche and (w, f, turno) not in prescritos:
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
        sin capacidad ese día. Devuelve (suma_desvío, cota)."""
        devs, cota = [], 0
        for (w, f), turno in self.prescripcion.items():
            if self.datos.trabajadores[w].patron in self.patrones_noche:
                continue                # noches: las gobierna activo (quincena), no la fijación blanda
            if f in self.cola or turno == LIBRE:
                continue
            var = self.x.get((w, f, turno))
            if var is None:
                continue                # vacaciones / no opera / sin capacidad -> sin fijación
            devs.append(1 - var)        # 1 si el trabajador NO hace su turno de rotación
            cota += 1
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
            case "finde":
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
        """Para cada métrica (finde, festivo) y cada GRUPO de equidad: carga ACUMULADA por trabajador
        (offset previo del libro + lo asignado en la ventana; fijos fuera) y su desviación respecto a
        la media DE SU GRUPO. Equidad PLANA dentro del grupo (todos pesan igual). Los de solo L-V no
        aparecen (nunca elegibles en finde/festivo). Devuelve (desviaciones por métrica, cota_p2)."""
        dias = [f for f in self.fechas if f not in self.cola]     # solo la ventana, no la cola
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
        return desviaciones, cota_p2

    # -- C9: jornada anual (tope duro + pacing blando) ----------------------- #
    def _c9_jornada_anual(self) -> None:
        """C9 (DURA): la jornada anual efectiva no supera HMAX_AÑO (tope legal). Libro de horas
        acumuladas: minutos previos (offset_horas) + los de esta ventana <= tope. Guarda los términos
        de minutos por NO-fijo para la equidad de horas (P_horas). Los fijos también topan (antes
        estaban fuera → un fijo podía superar el tope en silencio), pero NO entran en _min_ventana:
        su jornada la cuadra _retirada_fijos quitando días (Etapa 4)."""
        dias = [f for f in self.fechas if f not in self.cola]     # solo la ventana
        self._min_ventana: dict[str, list] = {}
        for w, t in self.datos.trabajadores.items():
            terminos = self._minutos(w, dias)
            if not terminos:
                continue                                          # no puede trabajar en la ventana
            tope_min = round(HMAX_AÑO * t.factor_jornada * 60)    # tope escalado por reducción de jornada
            off = self.offset_horas.get(w, 0)                     # <= tope por invariante del libro
            cap = tope_min
            paced = self.tope_paced.get(w)                        # cap prorrateado (anti front-loading, no-fijos)
            if paced is not None:
                cap = min(cap, paced)                             # rige el más restrictivo de los dos
            self.m.add(sum(terminos) <= max(0, cap - off))        # max(0,·): si va por delante del ritmo, descansa
            # Equidad de jornada hacia 1776: todos los NO-fijos salvo los patrones UVI (sus horas de
            # patrón+vacaciones se aceptan aunque excedan; no se recortan). Fijos aparte (retirada).
            if t.tipo != "fijo" and t.patron not in self.patrones_uvi:
                self._min_ventana[w] = terminos

    def _desviacion_jornada(self) -> tuple[object, int]:
        """P_horas (BLANDA, EQUIDAD): penaliza que los minutos de CONSUMO de la ventana se desvíen del
        objetivo prorrateado (objetivo_horas[w], derivado de HORAS_OBJETIVO=1776) por DEBAJO o
        por ENCIMA (desviación absoluta, simétrica). Se mide sobre CONSUMO (no computado): un
        localizado 24h consume ≈11.43 h por su naturaleza aunque compute 8 → así no arrastra un
        falso déficit de horas. Reparte la jornada de forma pareja a lo largo del año y evita que
        nadie derive hacia el tope duro (1826). Va al nivel bajo (desempate): solo actúa cuando no
        cuesta cobertura ni equidad de findes. Fijos fuera (su jornada se cuadra por retirada de
        días, Etapa 4). Devuelve (Σ|desv|, cota).
        Nota: exceso y déficit pesan IGUAL (|·|). Si se quisiera penalizar más suave el exceso,
        separar en dos variables (déficit / exceso) con pesos distintos."""
        dias = [f for f in self.fechas if f not in self.cola]
        max_turno_min = max((round(t.horas_consumo * 60) for t in self.datos.turnos.values()), default=0)
        max_min = len(dias) * max_turno_min                       # cota superior de minutos de consumo
        desvs, cota = [], 0
        for w in getattr(self, "_min_ventana", {}):               # no-fijos con jornada en la ventana
            objetivo = self.objetivo_horas.get(w, 0)
            if objetivo <= 0:
                continue
            terminos = self._minutos_consumo(w, dias)             # EQUIDAD sobre CONSUMO, no computado
            cota_w = max(objetivo, max_min)                       # |Σmin − objetivo| ≤ max(objetivo, max_min)
            desv = self.m.new_int_var(0, cota_w, f"desvh_{w}")
            self.m.add_abs_equality(desv, sum(terminos) - objetivo)
            desvs.append(desv)
            cota += cota_w
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
        El tope legal 1826 lo garantiza C9 aparte, independiente de este peso. Devuelve (Σ costes,
        cota)."""
        dias = [f for f in self.fechas if f not in self.cola]
        costes, cota = [], 0
        for w in self.fijos_activos:
            objetivo_cum = self.objetivo_horas_fijo.get(w)
            if objetivo_cum is None:
                continue
            terminos = self._minutos(w, dias)                     # = Σ min(phi)·(1 − retira)
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
    def resolver(self,gap:float = 0.05,tiempo:int = 600,trabajadores_cpu: int = 4, log: bool = False):
        """Objetivo jerárquico en uno solo:  W1·(P1+exceso+desvío_patrón) + W2·P2 + W3·(P_horas+P5),
        con W1>W2>W3. NIVEL COBERTURA (todo con peso pequeño < valor de cubrir un turno, así solo
        se acepta si rescata cobertura): P1 turnos no cubiertos (UNIFORME, sin criticidad por tipo de
        turno) + penalización de 6 días seguidos dentro de una semana + desvío del patrón (fijación
        blanda). -> P2: equidad PLANA del nº de findes/festivos entre capaces (todos pesan igual) ->
        P_horas equidad de jornada (|desv| respecto al objetivo 1776) + P5 estabilidad posicional del
        mixto (los dos desempates comparten el nivel más bajo para no inflar la torre de pesos)."""
        p1 = self._coste_cobertura()
        p_exceso, _ = self._exceso_semanal()               # 6 días seguidos dentro de una semana
        p_dev, _ = self._fijacion_patron()                 # desvío del patrón (fijación blanda)
        desviaciones, max_p2 = self._equidad_ponderada()   # cota_p2 exacta (incluye offset)
        self.desviaciones = desviaciones                  # expuesto para el resumen tras resolver
        p2 = sum(LAMBDA[metrica] * sum(vars_desv) for metrica, vars_desv in desviaciones.items())
        p_horas, max_horas = self._desviacion_jornada()    # equidad de horas NO-fijos (|desv| vs objetivo)
        p_ret, max_ret = self._retirada_fijos()            # Etapa 4: horas de fijos (asimétrico: falta≫exceso)
        p_retira = sum(self.retira.values())               # nº de días quitados a fijos (freno a quitar de más)
        p5, max_p5 = self._inestabilidad_mixto()           # estabilidad posicional del mixto (L-V)

        # Cotas conservadoras del nivel bajo para escalar los pesos (W1 > max aporte del nivel bajo).
        max_low = max_horas + max_ret + PESO_RETIRA * len(self.retira) + PESO_ESTAB * max_p5
        W3 = 1
        W2 = max_low + 1
        W1 = (max_p2 * W2) + max_low + 1

        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = trabajadores_cpu
        solver.parameters.log_search_progress = log
        solver.parameters.relative_gap_limit = gap
        solver.parameters.max_time_in_seconds = tiempo
        self.m.minimize(W1 * (p1 + p_exceso + PESO_DEV * p_dev) + W2 * p2
                        + W3 * (p_horas + p_ret + PESO_RETIRA * p_retira + PESO_ESTAB * p5))
        return solver, solver.solve(self.m)

    # -- Resolución LEXICOGRÁFICA (por pasadas; no desborda a ningún horizonte) ---------- #
    def resolver_lexicografico(self, tiempos: tuple[int, int, int] = (900, 600, 600),
                               trabajadores_cpu: int = 8, gap: float = 0.0, log: bool = False):
        """Objetivo lexicográfico por PASADAS (sin torre de pesos → no desborda int64 ni con el año
        completo). Tres niveles en orden estricto de prioridad, cada uno con sus coeficientes
        NATURALES (pequeños); la prioridad se impone CONGELANDO cada nivel con una restricción
        (nivel ≤ su óptimo) antes de optimizar el siguiente:
          1) cobertura: huecos + 6-días-seguidos + desvío del patrón
          2) equidad:   nº de findes/festivos entre capaces
          3) horas:     desviación de jornada (no-fijos) + retirada de fijos + estabilidad del mixto
        Cada pasada arranca warm-started con la solución de la anterior. Devuelve (solver, estado)."""
        p1 = self._coste_cobertura()
        p_exceso, _ = self._exceso_semanal()
        p_dev, _ = self._fijacion_patron()
        desviaciones, _ = self._equidad_ponderada()
        self.desviaciones = desviaciones
        p2 = sum(LAMBDA[m] * sum(v) for m, v in desviaciones.items())
        p_horas, _ = self._desviacion_jornada()
        p_ret, _ = self._retirada_fijos()
        p_retira = sum(self.retira.values())
        p5, _ = self._inestabilidad_mixto()

        niveles = [
            ("cobertura", p1 + p_exceso + PESO_DEV * p_dev),
            ("equidad", p2),
            ("horas", p_horas + p_ret + PESO_RETIRA * p_retira + PESO_ESTAB * p5),
        ]

        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = trabajadores_cpu
        solver.parameters.log_search_progress = log
        if gap > 0:
            solver.parameters.relative_gap_limit = gap

        self.plan_lexico: dict[tuple[str, date], str] = {}   # última solución COMPLETA buena
        st = cp_model.UNKNOWN
        for i, (nombre, expr) in enumerate(niveles):
            solver.parameters.max_time_in_seconds = tiempos[i]
            self.m.minimize(expr)
            st = solver.solve(self.m)
            if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                print(f"  nivel {nombre}: {solver.status_name(st)} — me quedo con la solución del "
                      f"nivel previo ({len(self.plan_lexico)} asignaciones)", flush=True)
                return solver, st
            self.plan_lexico = {(w, f): s for (w, f, s), v in self.x.items() if solver.value(v)}
            val = round(solver.objective_value)
            print(f"  nivel {nombre}: óptimo={val}  ({solver.status_name(st)}, {solver.wall_time:.0f}s)",
                  flush=True)
            if not isinstance(expr, int):
                self.m.add(expr <= val)                       # congela este nivel (prioridad estricta)
            if i + 1 < len(niveles):                          # warm-start de la siguiente pasada
                asignados = [self.x[k] for k in self.x if solver.value(self.x[k])]
                quitados = [self.retira[k] for k in self.retira if solver.value(self.retira[k])]
                self.m.clear_hints()
                for var in asignados + quitados:
                    self.m.add_hint(var, 1)
        return solver, st


# --------------------------------------------------------------------------- #
#  Horizonte rodante: resuelve el periodo por ventanas cosidas
# --------------------------------------------------------------------------- #
def _plan_ventana(mod: Modelo, solver, fechas_ventana: list[date]) -> dict[tuple[str, date], str]:
    """Extrae {(trab, fecha): turno} de las decisiones de la VENTANA (ignora la cola)."""
    dias = set(fechas_ventana)
    return {(w, f): s for (w, f, s), var in mod.x.items()
            if f in dias and solver.value(var)}


def _actualizar_offset(offset: dict, mod: Modelo, plan_ventana: dict) -> None:
    """Suma al libro de equidad las cargas indeseables asignadas en la ventana (fijos fuera)."""
    for (w, f), turno in plan_ventana.items():
        if mod.datos.trabajadores[w].tipo == "fijo":
            continue
        for metrica in METRICAS:
            if mod._contribuye(metrica, turno, f):
                offset.setdefault(w, {})
                offset[w][metrica] = offset[w].get(metrica, 0) + 1


def _actualizar_offset_horas(offset_horas: dict, datos: Datos, plan_ventana: dict) -> None:
    """Suma al libro de jornada anual (C9 + pace) los minutos trabajados en la ventana. Incluye a
    los fijos (Etapa 4): un día retirado no aparece en el plan, así que no suma → el libro refleja
    sus horas reales tras las retiradas."""
    for (w, f), turno in plan_ventana.items():
        offset_horas[w] = offset_horas.get(w, 0) + round(datos.turnos[turno].horas * 60)


def _linea_fija_de(datos: Datos, w: str) -> str | None:
    """Única línea de capacidad normal de un fijo (versión a nivel módulo de Modelo._linea_fija)."""
    lineas = [turno for (ww, turno), c in datos.capacidades.items()
              if ww == w and c.v == 0 and (c.lv or c.sab or c.dom or c.fest)]
    return lineas[0] if len(lineas) == 1 else None


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


def resolver_anual(datos: Datos, inicio: date, fin: date, dias_ventana: int = 14,
                   dias_cola: int = 28, segundos: int = 60, hilos: int = 8,
                   gap: float = 0.0, log: bool = False) -> dict[tuple[str, date], str]:
    """Horizonte rodante: resuelve [inicio, fin] por ventanas alineadas a lunes, con cola
    congelada (costura legal) y libro de equidad acumulada. Devuelve el plan completo."""
    inicio -= timedelta(days=inicio.weekday())          # alinear a lunes
    plan: dict[tuple[str, date], str] = {}
    offset: dict[str, dict[str, int]] = {}
    offset_horas: dict[str, int] = {}                   # libro de jornada anual (minutos), C9
    # días disponibles (no vacaciones) de cada trabajador en todo el horizonte: base del prorrateo
    dias_horizonte = rango_fechas(inicio, fin)
    uvi = _patrones_uvi(datos)                          # UVI: horas de patrón aceptadas, no se recortan
    disp_año = {w: sum(datos.disponible(w, f) for f in dias_horizonte)
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
        fin_v = min(ini_v + timedelta(days=dias_ventana - 1), fin)
        fechas_cola = [f for f in rango_fechas(ini_v - timedelta(days=dias_cola),
                                               ini_v - timedelta(days=1)) if f >= inicio]
        fechas_ventana = rango_fechas(ini_v, fin_v)
        cola = set(fechas_cola)
        congelar = {(w, f): plan[(w, f)] for (w, f) in plan if f in cola}

        # Objetivo de jornada de la ventana: prorrateo de HORAS_OBJETIVO (meta blanda, 1776) por
        # días disponibles (P4). El tope duro (HMAX_AÑO, 1826) lo impone C9 aparte.
        objetivo_horas = {}
        for w, disp_total in disp_año.items():
            if disp_total == 0:
                continue
            disp_v = sum(datos.disponible(w, f) for f in fechas_ventana)
            factor = datos.trabajadores[w].factor_jornada        # reducción de jornada
            objetivo_horas[w] = round(HORAS_OBJETIVO * factor * 60 * disp_v / disp_total)

        # Etapa 4: objetivo ACUMULADO (min) de 1776 de cada fijo hasta el FIN de esta ventana
        # (prorrateo por días elegibles transcurridos). _retirada_fijos lo usa como línea de ritmo.
        objetivo_horas_fijo = {}
        for w, elig in fijo_elig.items():
            if not elig:
                continue
            factor = datos.trabajadores[w].factor_jornada
            elig_hasta = sum(1 for f in elig if f <= fin_v)
            objetivo_horas_fijo[w] = round(HORAS_OBJETIVO * factor * 60 * elig_hasta / len(elig))

        # Cap PRORRATEADO (no-fijos): jornada acumulada hasta el FIN de esta ventana <= ritmo lineal
        # hacia el OBJETIVO 1776 por días DISPONIBLES transcurridos + colchón. Tope DURO por ventana:
        # (a) evita agotar horas antes de diciembre (reparte huecos homogéneo) y (b) hace de 1776 un
        # techo blando (se cruza como mucho por el colchón). El tope legal 1826 lo impone C9 aparte.
        # Las NOCHES quedan FUERA del cap duro: recortan por QUINCENA (granularidad gruesa) y el cap
        # duro las sobre-recortaría al forzar una quincena de más; las cuadra la equidad blanda (Alt 2).
        noche = _patrones_noche(datos)
        tope_paced = {}
        for w, disp_total in disp_año.items():
            t = datos.trabajadores[w]
            if disp_total == 0 or t.patron in noche:
                continue
            disp_hasta = sum(datos.disponible(w, f) for f in rango_fechas(inicio, fin_v))
            factor = t.factor_jornada
            # Colchón (adelanto sobre el ritmo de 1776) SOLO para el pool flexible: les da holgura para
            # picos de cobertura. Los PATRONES largos no lo necesitan (rotación fija, no hacen
            # front-loading) → colchón 0 los deja en ~1776 en vez de clavados en 1800 (=1776+24).
            colchon = 0 if t.tipo == "patron" else COLCHON_PACE_H
            tope_paced[w] = round((HORAS_OBJETIVO * disp_hasta / disp_total + colchon) * factor * 60)

        mod = Modelo(datos, fechas_cola + fechas_ventana,
                     congelar=congelar, offset_equidad=offset, cola=cola,
                     offset_horas=offset_horas, objetivo_horas=objetivo_horas,
                     objetivo_horas_fijo=objetivo_horas_fijo, tope_paced=tope_paced,
                     ancla_patron=inicio)   # ancla GLOBAL fija: la rotación es consistente entre ventanas
        solver, st = mod.resolver(tiempo=segundos, trabajadores_cpu=hilos, gap=gap, log=log)

        pv = _plan_ventana(mod, solver, fechas_ventana)
        plan.update(pv)
        _actualizar_offset(offset, mod, pv)
        _actualizar_offset_horas(offset_horas, datos, pv)

        v += 1
        huecos = sum(solver.value(u) for u in mod.u.values())
        dem = sum(datos.turnos[t].dem for (t, _) in mod.u)
        print(f"ventana {v:>2}  {ini_v:%d/%m}–{fin_v:%d/%m}  {solver.status_name(st):<9} "
              f"cobertura {100*(dem-huecos)/dem:5.1f}%  ({huecos} huecos)", flush=True)
        ini_v = fin_v + timedelta(days=1)
    return plan


def resolver_monolitico(datos: Datos, inicio: date, fin: date,
                        tiempos: tuple[int, int, int] = (900, 600, 600), hilos: int = 8,
                        warm_plan: dict[tuple[str, date], str] | None = None,
                        log: bool = False) -> tuple[dict[tuple[str, date], str], Modelo,
                                                    cp_model.CpSolver, int]:
    """Modelo de AÑO COMPLETO (sin ventanas) resuelto por lexicográfico secuencial: ve todo el
    horizonte → reparte huecos y horas por todo el año (sin acantilado de fin de año) y no desborda
    int64. Opcionalmente WARM-STARTED con un plan (p.ej. el del rodante): parte de esa solución y
    solo la pule. Sin cola ni libros: todo se decide de una; objetivos = anuales completos (el tope
    duro 1826 lo impone C9 directamente, off=0). Devuelve (plan, modelo, solver, estado)."""
    inicio -= timedelta(days=inicio.weekday())          # alinear a lunes (restricciones semanales)
    fechas = rango_fechas(inicio, fin)
    objetivo_horas, objetivo_horas_fijo = {}, {}
    for w, t in datos.trabajadores.items():
        obj = round(HORAS_OBJETIVO * t.factor_jornada * 60)     # objetivo anual completo
        if t.tipo == "fijo":
            objetivo_horas_fijo[w] = obj                # cumulativo hasta fin de año = objetivo pleno
        else:
            objetivo_horas[w] = obj
    mod = Modelo(datos, fechas, objetivo_horas=objetivo_horas, objetivo_horas_fijo=objetivo_horas_fijo)
    if warm_plan:
        mod.m.clear_hints()                             # sustituye el hint del patrón por el plan dado
        for (w, f), s in warm_plan.items():
            if (w, f, s) in mod.x:
                mod.m.add_hint(mod.x[(w, f, s)], 1)
    print(f"monolítico {fechas[0]:%d/%m/%Y}–{fechas[-1]:%d/%m/%Y}  ({len(mod.x):,} vars, "
          f"warm_start={'sí' if warm_plan else 'no'})", flush=True)
    solver, st = mod.resolver_lexicografico(tiempos=tiempos, trabajadores_cpu=hilos, log=log)
    return mod.plan_lexico, mod, solver, st        # última solución COMPLETA buena (no basura si un nivel falla)


def imprimir_resumen(modelo: Modelo, solver: cp_model.CpSolver, status: int) -> None:
    """Imprime un resumen básico de la solución."""
    print("\n" + "=" * 80)
    print("RESUMEN DEL SOLVER")
    print("=" * 80)
    estados = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }
    print(f"Estado: {estados.get(status, status)}")
    print(f"Tiempo: {solver.WallTime():.2f} s")
    print(f"Conflictos: {solver.NumConflicts()}")
    print(f"Ramas: {solver.NumBranches()}")

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Objetivo: {solver.ObjectiveValue():.0f}")
        print(f"Mejor cota: {solver.BestObjectiveBound():.0f}")

        if solver.ObjectiveValue() != 0:
            gap = abs(solver.ObjectiveValue() - solver.BestObjectiveBound()) / abs(solver.ObjectiveValue())
            print(f"Gap aproximado: {gap:.2%}")

        print("\n" + "-" * 80)
        print("TURNOS NO CUBIERTOS")
        print("-" * 80)

        total_holguras = 0

        if hasattr(modelo, "u"):
            for (turno, f), holgura in sorted(modelo.u.items(), key=lambda x: (x[0][1], x[0][0])):
                valor = solver.Value(holgura)
                if valor:
                    total_holguras += valor
                   #print(f"{f} | {turno}: {valor}")

        if total_holguras == 0:
            print("Todos los turnos quedaron cubiertos.")
        else:
            print(f"Total huecos sin cubrir: {total_holguras}")

        print("=" * 80 + "\n")
# --------------------------------------------------------------------------- #
#  Prueba: resolver una ventana (enero de 2026)
# --------------------------------------------------------------------------- #
def main() -> None:
    datos = cargar(DATA)
    fechas = rango_fechas(FECHA_INI,FECHA_FIN)

    print("=" * 80)
    print("CONSTRUCCIÓN DEL MODELO")
    print("=" * 80)
    print(f"Horizonte: {fechas[0]} -> {fechas[-1]}")
    print(f"Nº días: {len(fechas)}")
    print(f"Nº trabajadores: {len(datos.trabajadores)}")
    print(f"Nº turnos: {len(datos.turnos)}")
    print("=" * 80)

    modelo = Modelo(datos, fechas)

    
    solver, status = modelo.resolver(
        tiempo=120,
        trabajadores_cpu=8,
        log=False,
    )

    imprimir_resumen(modelo, solver, status)

if __name__ == "__main__":
    main()

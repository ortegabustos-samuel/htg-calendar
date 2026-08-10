"""Métricas y pesos de INFORME: lo que se cuenta y cómo se pondera al reportar.

Ojo a la moneda: `jornada_minutos` cuenta en horas de CONSUMO (una semana de localizado ocupa
`JORNADA_LOCALIZADO_SEMANA` entera). El motor nuevo NO usa esa moneda para el libro anual —el
objetivo de 1776 es una cifra de convenio y se lleva en horas legales—, pero los informes sí la
enseñan, que es otra cosa. Las dos contabilidades conviven a propósito."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from calendario import semana
from cargar_datos import LIBRE, Datos, Turno

# Jornada que COMPUTA una SEMANA de plaza de LOCALIZADO 24h asumida entera (h). Es la unidad en que
# se contabiliza esa plaza para el objetivo anual, y no la suma de sus turnos, por dos motivos:
#  · si falta la fila ENTERA, quien la cubre se lleva la plaza CON sus descansos (_adopcion_plaza):
#    NINGÚN otro turno, así que es una semana de trabajo completa aunque la rotación solo le ponga
#    2 turnos (la fila corta libra miércoles y jueves; la larga son 5);
#  · sus turnos computan 8 h LEGALES cada uno, que es lo que exige el convenio, pero no describe lo
#    que la plaza ocupa: sumándolos, el cubridor de una semana corta aparecía con 16 h "trabajadas"
#    y el libro anual le reclamaba el resto en otras semanas -> acababa trabajando de más.
# El titular de la rotación queda igualmente FUERA del libro anual (ver _c9_jornada_anual): cubre su
# año entero salvo vacaciones y por acuerdo eso ES su jornada.
JORNADA_LOCALIZADO_SEMANA = 40

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


def lineas_localizadas(datos: Datos) -> set[str]:
    """Líneas de los patrones UVI (localizado 24h): las únicas que se ceden como PLAZA ENTERA con
    sus descansos (_adopcion_plaza) y, por tanto, las únicas que computan por SEMANA y no por
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

"""
base.py — Paso A del pipeline: el cuadrante base, sin ninguna decisión libre.

Pinta lo que los datos YA prescriben, y nada más:

  * PATRÓN     — la fila de `patrones.csv` que le toca esa semana. La rotación avanza una fila
                 por semana desde el LUNES ANCLA (el lunes de la semana en que cae el 1 de enero)
                 y cada trabajador arranca en la fila que declara `fila_inicial` en
                 trabajadores.csv — eso es lo que da continuidad con el cuadrante del año anterior
                 (ver `cargar_datos.offsets_patron`).
  * FIJO       — su `linea`, los días en que su capacidad la cubre (L-V por definición, salvo que
                 capacidades.csv declare otra cosa).
  * MIXTO      — es un cuasi-fijo, no un correturno con menos capacidades, y sus capacidades ya
                 declaran las dos naturalezas: unas líneas con `lv=1` (operan 248 días, o sea de
                 lunes a viernes) de las que es titular de hecho, y otras con `sab/dom/fest=1` a
                 las que sale puntualmente. Ocupa las primeras y saca su CUOTA de fines de semana
                 en las segundas (ver `colocar_mixtos`). Va en una segunda pasada porque depende de
                 qué dejan sin cubrir los patrones.
  * CORRETURNO — nada. Es el único pool rodante de verdad, y su semana la decide el paso C.

Importante mencionar que dias como vacaciones o festivos ni siquiera se contemplan como disponibles  

Salida: el `plan` — dict {(id_trab, fecha) -> id_turno} — que es el objeto que se pasan entre sí
todas las etapas y el que consume la vista de Excel.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

import legal
from cargar_datos import DIAS, LIBRE, Datos

def turno_patron(datos: Datos, trabajador_id: str, fecha: date) -> str:
    """Celda que la rotación prescribe al trabajador el día fecha: un id de turno, `LIBRE`
    Ojo: prescribir un turno no significa que se trabaje la línea
    puede no operar ese día y el trabajador puede estar de vacaciones."""

    trabajador = datos.trabajadores[trabajador_id]
    filas = datos.patrones.get(trabajador.patron)
    offset = datos.offsets.get(trabajador_id, 0)
    semanas = (fecha - datos.primer_lunes).days // 7
    fila = filas[(offset + semanas) % len(filas)]
    return fila[DIAS[fecha.weekday()]]


def prescrito(datos: Datos, trabajador_id: str, fecha: date) -> str | None:
    """Plaza que le toca a este trabajador ese día, IGNORANDO si está disponible.

    Es lo que le corresponde por rotación o por su línea fija, siempre que la plaza exista ese día
    (la línea opera y su capacidad cubre el tipo de día). Que esté de vacaciones no cambia lo que
    le tocaba, y por eso esto y `disponible` son preguntas separadas: juntas dicen qué plazas se
    quedan solas, que es lo que el paso B traspasa a un cubridor.
    """
    trabajador = datos.trabajadores[trabajador_id]
    if trabajador.tipo == "patron":
        turno_id = turno_patron(datos, trabajador_id, fecha)
        return turno_id if (turno_id != LIBRE and turno_id in datos.turnos and datos.opera(turno_id, fecha)) else None
    if trabajador.tipo == "fijo":
        cap = datos.capacidades.get((trabajador_id, trabajador.linea))
        if not datos.opera(trabajador.linea, fecha):
            return None
        dia = datos.tipo_dia(fecha, datos.turnos[trabajador.linea].municipio)       #En un principio esto es redundante suponiendo 
        return trabajador.linea if {"LV": cap.lv, "SAB": cap.sab,                   # a fijos solo hacer Lunes-Viernes
                           "DOM": cap.dom, "FEST": cap.fest}[dia] == 1 else None
    return None


def construir(datos: Datos) -> dict[tuple[str, date], str]:
    """Plan base del año: {(id_trab, fecha) -> id_turno}."""
    plan: dict[tuple[str, date], str] = {}
    for dia in datos.fechas:
        for trabajador_id in datos.trabajadores:
            if not datos.disponible(trabajador_id, dia):
                continue                                  # vacaciones: ausencia, no asignación
            turno_id = prescrito(datos, trabajador_id, dia)
            if turno_id is not None:
                plan[(trabajador_id, dia)] = turno_id
    return plan


# --------------------------------------------------------------------------- #
#  Los mixtos: cuasi-fijos con cuota de fin de semana
# --------------------------------------------------------------------------- #
FINDE = ("SAB", "DOM", "FEST")


def lineas_de(datos: Datos, trabajador_id: str, clase: str) -> list[str]:
    """Líneas del trabajador de una clase: 'lv' o 'finde'. Sale de los flags de capacidades.csv,
    que es donde el planificador ya separó las dos naturalezas del mixto."""
    salida = []
    for (trabajador_id_cap, turno_id), cap in datos.capacidades.items():
        if trabajador_id != trabajador_id_cap or turno_id not in datos.turnos:
            continue
        if (cap.lv == 1) if clase == "lv" else bool(cap.sab or cap.dom or cap.fest):
            salida.append(turno_id)
    return sorted(salida)


def referencia_finde(datos: Datos, plan: dict[tuple[str, date], str]) -> dict[str, Counter]:
    """Municipio -> sábados/domingos/festivos que hace de media una persona del grupo de patrón MÁS
    NUMEROSO de ese municipio.

    Es la vara de medir de la equidad: el reparto que la empresa ya da por bueno, y contra el que
    hay que equiparar a quien hoy no hace ningún fin de semana. En Valladolid sale de
    PAT_GRANDE_VALL (38 personas): 16 sábados, 6 domingos y 3 festivos.
    """
    gente: dict[str, int] = Counter()
    municipio: dict[str, Counter] = defaultdict(Counter)
    findes: dict[str, Counter] = defaultdict(Counter)
    for w, t in datos.trabajadores.items():
        if t.tipo == "patron" and t.patron:
            gente[t.patron] += 1
    for (w, f), s in plan.items():
        t = datos.trabajadores[w]
        if t.tipo != "patron" or not t.patron:
            continue
        muni = datos.turnos[s].municipio
        municipio[t.patron][muni] += 1
        dia = datos.tipo_dia(f, muni)
        if dia in FINDE:
            findes[t.patron][dia] += 1

    mayor: dict[str, tuple[int, str]] = {}
    for patron, n in gente.items():
        if not municipio[patron]:
            continue
        muni = municipio[patron].most_common(1)[0][0]
        if muni not in mayor or n > mayor[muni][0]:
            mayor[muni] = (n, patron)
    return {muni: Counter({d: round(findes[patron][d] / n) for d in FINDE})
            for muni, (n, patron) in mayor.items()}


def cuota_finde(datos: Datos, trab: str, refs: dict[str, Counter]) -> Counter:
    """Cuota de este mixto: la del municipio donde hace sus fines de semana."""
    suyas = lineas_de(datos, trab, "finde")
    if not suyas:
        return Counter()
    muni = Counter(datos.turnos[s].municipio for s in suyas).most_common(1)[0][0]
    return refs.get(muni, Counter())


def _libre(datos: Datos, cubiertas: Counter, turno_id: str, fecha: date) -> bool:
    return cubiertas[(turno_id, fecha)] < datos.turnos[turno_id].dem


def _elegir_linea(datos: Datos, plan: dict[tuple[str, date], str], cubiertas: Counter, trab: str,
                  fecha: date, lineas: list[str], clases: tuple[str, ...],
                  anterior: str | None) -> str | None:
    """De sus líneas descubiertas ese día: la que hacía ayer (continuidad); si no, la que menos
    gente más puede hacer, que es la más difícil de tapar por otro.

    Aquí SÍ se comprueba la legalidad, al revés que en un traspaso del paso B: el mixto va saltando
    entre varias líneas suyas de franjas distintas, así que la secuencia que resulta se la inventa
    el pipeline y no la ha pactado nadie (una tarde que acaba a las 21:30 seguida de una mañana que
    entra a las 07:00 son 9,5 h de descanso).

    `clases` acota el tipo de día, y no es un detalle: una misma línea puede declarar `lv=1` y
    `sab=1`, así que sin este filtro la pasada de lunes a viernes se llevaba también los sábados
    de esa línea, sin tope, y reventaba tanto la cuota de equidad como la jornada anual.
    """
    posibles = [turno_id for turno_id in lineas
                if datos.tipo_dia(fecha, datos.turnos[turno_id].municipio) in clases
                and datos.elegible(trab, turno_id, fecha) == (True, False)
                and _libre(datos, cubiertas, turno_id, fecha)
                and legal.permite(datos, plan, trab, fecha, turno_id)]
    if not posibles:
        return None
    if anterior in posibles:
        return anterior
    return min(posibles, key=lambda s: (sum(1 for (_, ss) in datos.capacidades if ss == s), s))


def _uniformes(dias: list[date], cuantos: int) -> list[date]:
    """`cuantos` días repartidos lo más uniformemente posible a lo largo de la lista. Es lo que
    hace que los fines de semana caigan espaciados por el año y no en bloque."""
    if cuantos <= 0 or not dias:
        return []
    if cuantos >= len(dias):
        return list(dias)
    paso = len(dias) / cuantos
    return [dias[min(len(dias) - 1, int(i * paso + paso / 2))] for i in range(cuantos)]


def _soltar_dia_lv(datos: Datos, plan: dict[tuple[str, date], str], libro,
                   cubiertas: Counter, trab: str, f: date) -> tuple[date, str] | None:
    """Libra un día entre semana de la MISMA semana ISO para hacer sitio al de finde.

    Es literalmente lo que hace el planificador a mano: se libra un día entre semana para hacer un
    sábado. Deja las horas neutras y evita pasarse del tope de días por semana (cinco de L-V más el
    sábado ya son seis, y con el domingo siete).

    CUÁL se suelta es aquí solo una elección provisional —la línea que más gente puede tapar—,
    porque en este paso los correturnos todavía no están colocados y no hay forma de saber quién
    estará libre. La decisión de verdad la toma el paso D, que ve la semana entera: la devolvemos
    marcada como flexible y allí se elige el día mirando quién puede cubrir el hueco que deja.
    Devuelve (día soltado, línea) o None si no había ninguno.
    """
    lunes = f - timedelta(days=f.weekday())
    suyos = [lunes + timedelta(days=i) for i in range(5) if (trab, lunes + timedelta(days=i)) in plan]
    if not suyos:
        return None
    peor = max(suyos, key=lambda g: sum(1 for (_, ss) in datos.capacidades if ss == plan[(trab, g)]))
    s = plan.pop((trab, peor))
    libro.borra(trab, s)
    cubiertas[(s, peor)] -= 1
    return peor, s


def colocar_mixtos(datos: Datos, plan: dict[tuple[str, date], str], libro):
    """Segunda pasada del base: coloca a los mixtos sobre lo que los patrones dejan libre.

    Devuelve dos cosas:
      * `protegidos` — los días de fin de semana que son CUOTA. El paso B no debe cederlos: no son
        exceso, son la equidad que justifica que el mixto salga de su línea.
      * `flexibles` — (mixto, lunes, día soltado, línea) por cada semana en que se le quitó un día
        entre semana para hacer el finde. Cuál se suelta lo reabre el paso D.
    """
    cubiertas: Counter = Counter()
    for (_, fecha), turno_id in plan.items():
        cubiertas[(turno_id, fecha)] += 1
    refs = referencia_finde(datos, plan)
    protegidos: dict[str, set[date]] = defaultdict(set)
    flexibles: list[tuple[str, date, date, str]] = []

    mixtos = [trabajador_id for trabajador_id, trabajador in datos.trabajadores.items() if trabajador.tipo == "mixto"]
    # Los más atados primero: quien solo puede hacer una línea no tiene con qué negociar.
    mixtos.sort(key=lambda trabajador_id: (len(lineas_de(datos, trabajador_id, "lv")), trabajador_id))

    for trabajador_id in mixtos:
        lv = lineas_de(datos, trabajador_id, "lv")
        anterior: str | None = None
        for fecha in datos.fechas:                          # 1) ocupa su línea de lunes a viernes
            if not datos.disponible(trabajador_id, fecha) or (trabajador_id, fecha) in plan:
                continue
            turno_id = _elegir_linea(datos, plan, cubiertas, trabajador_id, fecha, lv, ("LV",), anterior)
            anterior = turno_id
            if turno_id is None:
                continue
            plan[(trabajador_id, fecha)] = turno_id
            libro.apunta(trabajador_id, turno_id)
            cubiertas[(turno_id, fecha)] += 1

        findes = lineas_de(datos, trabajador_id, "finde")   # 2) su cuota, repartida por el año
        cuota = cuota_finde(datos, trabajador_id, refs)
        for clase in FINDE:
            candidatos = [fecha for fecha in datos.fechas
                          if datos.disponible(trabajador_id, fecha)
                          and (trabajador_id, fecha) not in plan
                          and any(datos.tipo_dia(fecha, datos.turnos[s].municipio) == clase
                                  and datos.elegible(trabajador_id, s, fecha) == (True, False)
                                  and _libre(datos, cubiertas, s, fecha) for s in findes)]
            for fecha in _uniformes(candidatos, cuota.get(clase, 0)):
                turno_id = _elegir_linea(datos, plan, cubiertas, trabajador_id, fecha, findes,
                                         (clase,), None)
                if turno_id is None:
                    continue
                soltado = _soltar_dia_lv(datos, plan, libro, cubiertas, trabajador_id, fecha)
                if soltado is None:
                    continue
                plan[(trabajador_id, fecha)] = turno_id
                libro.apunta(trabajador_id, turno_id)
                cubiertas[(turno_id, fecha)] += 1
                protegidos[trabajador_id].add(fecha)
                flexibles.append((trabajador_id, fecha - timedelta(days=fecha.weekday()),
                                  soltado[0], soltado[1]))
    return protegidos, flexibles


def resumen_mixtos(datos: Datos, plan: dict[tuple[str, date], str], libro) -> None:
    refs = referencia_finde(datos, plan)
    print("\nPASO A2 — mixtos como cuasi-fijos, con cuota de fin de semana")
    print("  referencia de equidad: "
          + " · ".join(f"{m} {c['SAB']}S/{c['DOM']}D/{c['FEST']}F" for m, c in sorted(refs.items())))
    print(f"\n{'mixto':<12} {'lineas LV':>9} {'dias':>6} {'L-V':>5} {'sab':>5} {'dom':>5} "
          f"{'fest':>5} {'horas':>7}   cuota")
    print("-" * 74)
    for w, t in sorted(datos.trabajadores.items()):
        if t.tipo != "mixto":
            continue
        dias: Counter = Counter()
        for (ww, f), s in plan.items():
            if ww == w:
                dias[datos.tipo_dia(f, datos.turnos[s].municipio)] += 1
        c = cuota_finde(datos, w, refs)
        print(f"{w:<12} {len(lineas_de(datos, w, 'lv')):>9} {sum(dias.values()):>6} "
              f"{dias['LV']:>5} {dias['SAB']:>5} {dias['DOM']:>5} {dias['FEST']:>5} "
              f"{libro.horas(w):>7.0f}   {c['SAB']}S/{c['DOM']}D/{c['FEST']}F")
    print("-" * 74)

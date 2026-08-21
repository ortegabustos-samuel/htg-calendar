"""
esqueleto.py — Paso A del pipeline: el cuadrante base, sin ninguna decisión libre.

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

Dos cosas que NO se pintan, a propósito:
  * Las vacaciones son ausencia, no asignación: viven en `datos.disponible`, y la vista de Excel
    las dibuja aparte. Meterlas en el plan las convertiría en un turno más.
  * Un día que la rotación prescribe pero la línea no opera (festivo local, fin de semana) queda
    vacío: esa plaza no existe ese día, así que no hay nada que asignar ni que cubrir.

Salida: el `plan` — dict {(id_trab, fecha) -> id_turno} — que es el objeto que se pasan entre sí
todas las etapas y el que consume la vista de Excel.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

import legal
from cargar_datos import DIAS, LIBRE, Datos


def ancla(datos: Datos) -> date:
    """Lunes desde el que se cuentan las semanas de la rotación.

    Es el lunes de la semana en que cae el 1 de enero (no el 1 de enero), para que la semana de
    patrón sea siempre una semana completa L-D. Debe ser el MISMO para todo el horizonte: si se
    recalculara por tramos, la rotación se desplazaría en cada corte.
    """
    return datos.inicio - timedelta(days=datos.inicio.weekday())


def fila_patron(datos: Datos, trab: str, f: date) -> dict[str, str] | None:
    """Fila de `patrones.csv` que ejecuta `trab` la semana de `f`, o None si no es de patrón."""
    t = datos.trabajadores[trab]
    if t.tipo != "patron" or not t.patron:
        return None
    filas = datos.patrones.get(t.patron)
    if not filas:
        return None
    offset = datos.offsets.get(trab, 0)
    semanas = (f - ancla(datos)).days // 7
    return filas[(offset + semanas) % len(filas)]


def turno_patron(datos: Datos, trab: str, f: date) -> str | None:
    """Celda que la rotación prescribe a `trab` el día `f`: un id de turno, `LIBRE`, o None si no
    es un trabajador de patrón. Ojo: prescribir un turno no significa que se trabaje — la línea
    puede no operar ese día y el trabajador puede estar de vacaciones."""
    fila = fila_patron(datos, trab, f)
    return fila[DIAS[f.weekday()]] if fila else None


def prescrito(datos: Datos, trab: str, f: date) -> str | None:
    """Plaza que le toca a este trabajador ese día, IGNORANDO si está disponible.

    Es lo que le corresponde por rotación o por su línea fija, siempre que la plaza exista ese día
    (la línea opera y su capacidad cubre el tipo de día). Que esté de vacaciones no cambia lo que
    le tocaba, y por eso esto y `disponible` son preguntas separadas: juntas dicen qué plazas se
    quedan solas, que es lo que el paso B traspasa a un cubridor.
    """
    t = datos.trabajadores[trab]
    if t.tipo == "patron":
        s = turno_patron(datos, trab, f)
        return s if (s and s != LIBRE and s in datos.turnos and datos.opera(s, f)) else None
    if t.tipo == "fijo" and t.linea:
        cap = datos.capacidades.get((trab, t.linea))
        if cap is None or not datos.opera(t.linea, f):
            return None
        dia = datos.tipo_dia(f, datos.turnos[t.linea].municipio)
        # Solo por capacidad NORMAL: un fijo ocupa su plaza; cubrir la de otro es `v>=1` y es cosa
        # de pasos posteriores.
        return t.linea if {"LV": cap.lv, "SAB": cap.sab,
                           "DOM": cap.dom, "FEST": cap.fest}[dia] == 1 else None
    return None


def construir(datos: Datos) -> dict[tuple[str, date], str]:
    """Plan base del año: {(id_trab, fecha) -> id_turno}."""
    plan: dict[tuple[str, date], str] = {}
    for f in datos.fechas:
        for w in datos.trabajadores:
            if not datos.disponible(w, f):
                continue                                  # vacaciones: ausencia, no asignación
            s = prescrito(datos, w, f)
            if s is not None:
                plan[(w, f)] = s
    return plan


# --------------------------------------------------------------------------- #
#  Los mixtos: cuasi-fijos con cuota de fin de semana
# --------------------------------------------------------------------------- #
FINDE = ("SAB", "DOM", "FEST")


def lineas_de(datos: Datos, trab: str, clase: str) -> list[str]:
    """Líneas del trabajador de una clase: 'lv' o 'finde'. Sale de los flags de capacidades.csv,
    que es donde el planificador ya separó las dos naturalezas del mixto."""
    salida = []
    for (w, s), cap in datos.capacidades.items():
        if w != trab or s not in datos.turnos:
            continue
        if (cap.lv == 1) if clase == "lv" else bool(cap.sab or cap.dom or cap.fest):
            salida.append(s)
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


def _libre(datos: Datos, cubiertas: Counter, turno: str, f: date) -> bool:
    return cubiertas[(turno, f)] < datos.turnos[turno].dem


def _elegir_linea(datos: Datos, plan: dict[tuple[str, date], str], cubiertas: Counter, trab: str,
                  f: date, lineas: list[str], clases: tuple[str, ...],
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
    posibles = [s for s in lineas
                if datos.tipo_dia(f, datos.turnos[s].municipio) in clases
                and datos.elegible(trab, s, f) == (True, False)
                and _libre(datos, cubiertas, s, f)
                and legal.permite(datos, plan, trab, f, s)]
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
    """Segunda pasada del esqueleto: coloca a los mixtos sobre lo que los patrones dejan libre.

    Devuelve dos cosas:
      * `protegidos` — los días de fin de semana que son CUOTA. El paso B no debe cederlos: no son
        exceso, son la equidad que justifica que el mixto salga de su línea.
      * `flexibles` — (mixto, lunes, día soltado, línea) por cada semana en que se le quitó un día
        entre semana para hacer el finde. Cuál se suelta lo reabre el paso D.
    """
    cubiertas: Counter = Counter()
    for (_, f), s in plan.items():
        cubiertas[(s, f)] += 1
    refs = referencia_finde(datos, plan)
    protegidos: dict[str, set[date]] = defaultdict(set)
    flexibles: list[tuple[str, date, date, str]] = []

    mixtos = [w for w, t in datos.trabajadores.items() if t.tipo == "mixto"]
    # Los más atados primero: quien solo puede hacer una línea no tiene con qué negociar.
    mixtos.sort(key=lambda w: (len(lineas_de(datos, w, "lv")), w))

    for trab in mixtos:
        lv = lineas_de(datos, trab, "lv")
        anterior: str | None = None
        for f in datos.fechas:                          # 1) ocupa su línea de lunes a viernes
            if not datos.disponible(trab, f) or (trab, f) in plan:
                continue
            s = _elegir_linea(datos, plan, cubiertas, trab, f, lv, ("LV",), anterior)
            anterior = s
            if s is None:
                continue
            plan[(trab, f)] = s
            libro.apunta(trab, s)
            cubiertas[(s, f)] += 1

        findes = lineas_de(datos, trab, "finde")        # 2) su cuota, repartida por el año
        cuota = cuota_finde(datos, trab, refs)
        for clase in FINDE:
            candidatos = [f for f in datos.fechas
                          if datos.disponible(trab, f) and (trab, f) not in plan
                          and any(datos.tipo_dia(f, datos.turnos[s].municipio) == clase
                                  and datos.elegible(trab, s, f) == (True, False)
                                  and _libre(datos, cubiertas, s, f) for s in findes)]
            for f in _uniformes(candidatos, cuota.get(clase, 0)):
                s = _elegir_linea(datos, plan, cubiertas, trab, f, findes, (clase,), None)
                if s is None:
                    continue
                soltado = _soltar_dia_lv(datos, plan, libro, cubiertas, trab, f)
                if soltado is None:
                    continue
                plan[(trab, f)] = s
                libro.apunta(trab, s)
                cubiertas[(s, f)] += 1
                protegidos[trab].add(f)
                flexibles.append((trab, f - timedelta(days=f.weekday()), soltado[0], soltado[1]))
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

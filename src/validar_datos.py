"""
validar_datos.py — Revisa los CSV de entrada ANTES de resolver y avisa de lo que está mal.

Revisa en cuatro niveles, de lo que impide leer a lo que solo es sospechoso:
  0. CONFIG (config.toml)
  1. FORMATO
  2. REFERENCIAS
  3. CONTRATO
  4. VIABILIDAD

No corrige nada, solo informa: el arreglo va en el CSV, que es la fuente de verdad.
Sale con código 1 si hay algún ERROR.

Uso:  python3 src/validar_datos.py
"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cargar_datos import DATA, DIAS, DIAS_LV, LIBRE, _cargar_config, cargar          # noqa: E402

# Columnas que cada fichero DEBE traer. Las opcionales (factor_jornada, linea, municipio,
# fila_inicial, dem) no se exigen: el cargador les da valor por defecto.
OBLIGATORIAS = {
    "turnos.csv": ["id_turno", "municipio", "lv", "sabado", "domingo", "festivo",
                   "hora_entrada", "hora_salida", "horas_computadas"],
    "trabajadores.csv": ["id_trab", "tipo", "vac1_inicio", "vac2_inicio"],
    "capacidades.csv": ["id_trab", "id_turno", "lv", "sab", "dom", "fest", "v"],
    "patrones.csv": ["patron", "fila"] + DIAS,
    "festivos.csv": ["fecha", "ambito"],
    "calendarios_municipio.csv": ["municipio", "calendario_festivos"],
}

# Columnas que identifican una fila. Los cargadores usan dict[clave] = ..., así que una clave
# repetida NO da error: la segunda pisa a la primera y se pierde información sin avisar.
CLAVES = {
    "turnos.csv": ("id_turno",),
    "trabajadores.csv": ("id_trab",),
    "capacidades.csv": ("id_trab", "id_turno"),
    "patrones.csv": ("patron", "fila"),
    "calendarios_municipio.csv": ("municipio",),
}
TIPOS_TRAB = ("fijo", "patron", "mixto", "correturno")


class Informe:
    """Tres niveles:
      ERROR — no resolver con esto: se pierde información o el cuadrante saldrá mal.
      aviso — mira a ver: probablemente intencionado, pero también podría ser un descuido.
      nota  — contexto estructural, ni bueno ni malo; sirve para saber qué esperar del cuadrante.
    """

    def __init__(self) -> None:
        self.errores: list[str] = []
        self.avisos: list[str] = []
        self.notas: list[str] = []

    def error(self, msg: str) -> None:
        self.errores.append(msg)

    def aviso(self, msg: str) -> None:
        self.avisos.append(msg)

    def nota(self, msg: str) -> None:
        self.notas.append(msg)


def _fecha(txt: str) -> date | None:
    #Comprueba si una fecha es real y correcta
    try:
        return datetime.strptime(txt.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  1. FORMATO — lo que impide leer bien el fichero
# --------------------------------------------------------------------------- #
def revisar_config(inf: Informe) -> bool:
    """config.toml: año y parámetros del convenio. Devuelve si se puede seguir (sin año no hay
    horizonte y el nivel 4 no puede hacer nada)."""
    try:
        cfg = _cargar_config()
    except Exception as e:
        inf.error(f"config.toml: {e}")
        return False
    if not 1900 < cfg.anio < 2200:
        inf.error(f"config.toml: anio={cfg.anio} no parece un año")
    if cfg.horas_objetivo <= 0:
        inf.error(f"config.toml: horas_objetivo={cfg.horas_objetivo}; debe ser > 0")
    if not 0 <= cfg.descanso_minimo <= 24:
        inf.error(f"config.toml: descanso_minimo={cfg.descanso_minimo} horas de descanso no caben en un día")
    if not 0 < cfg.horas_max_semana <= 7 * 24:
        inf.error(f"config.toml: horas_max_semana={cfg.horas_max_semana} horas no caben en una semana")
    if not 1 <= cfg.dias_max_semana <= 7:
        inf.error(f"config.toml: dias_max_semana={cfg.dias_max_semana} días está fuera de 1..7")
    if not 0 < cfg.ratio_rigido <= 1:
        inf.error(f"config.toml: ratio_rigido={cfg.ratio_rigido} está fuera de (0, 1]: es una "
                  f"proporción de días de descanso por día de trabajo")
    dias = cfg.dias_descanso_finde
    if dias:
        indices = sorted(DIAS_LV.index(d) for d in dias if d in DIAS_LV)
        valido = (len(dias) == 2 and len(set(dias)) == 2 and len(indices) == 2
                  and indices[1] - indices[0] == 1)
        if not valido:
            inf.error(f"config.toml: dias_descanso_finde={dias!r} debe ir vacío o tener "
                      f"exactamente 2 días de {DIAS_LV}, distintos y consecutivos")
    return not inf.errores


def revisar_formato(inf: Informe) -> tuple[dict[str, list[dict]], list[str]]:
    """Lee los CSV en crudo, sin pasar por el cargador, para ver el texto tal cual está.

    Devuelve (filas de cada fichero, ficheros ILEGIBLES). La distinción importa: un espacio
    sobrante en una celda es un error, pero el fichero se sigue entendiendo y los niveles
    siguientes pueden decir cosas útiles sobre él. Uno que no existe, o al que le falta una
    columna, no deja nada que analizar — y solo eso corta la revisión."""
    crudo: dict[str, list[dict]] = {}
    ilegibles: list[str] = []
    for nombre, obligatorias in OBLIGATORIAS.items():
        crudo[nombre] = []
        ruta = DATA / nombre
        if not ruta.exists():
            inf.error(f"{nombre}: no existe en {DATA}")
            ilegibles.append(nombre)
            continue
        with open(ruta, encoding="utf-8-sig", newline="") as fh:
            filas = list(csv.reader(fh))
        if not filas:
            inf.error(f"{nombre}: fichero vacío")
            ilegibles.append(nombre)
            continue

        cab = filas[0]
        if any(c != c.strip() for c in cab):
            inf.error(f"{nombre}: la cabecera tiene espacios sobrantes -> {cab}")
            cab = [c.strip() for c in cab]
        faltan = [c for c in obligatorias if c not in cab]
        if faltan:
            inf.error(f"{nombre}: faltan columnas obligatorias {faltan} (cabecera: {cab})")
            ilegibles.append(nombre)
            continue

        datos: list[dict] = []
        for n, fila in enumerate(filas[1:], start=2):
            if not any(x.strip() for x in fila):
                continue                                   # línea en blanco: se ignora
            if len(fila) > len(cab):
                inf.error(f"{nombre}:{n} tiene {len(fila)} campos y la cabecera {len(cab)} — "
                          f"¿una coma de más, o un valor con coma sin comillas?")
                continue
            if len(fila) < len(cab):
                # Truncar la fila por la derecha es legítimo si lo que falta es opcional: DictReader
                # lo deja a None y el cargador le da su valor por defecto (así vienen las líneas sin
                # `fila_inicial`). Solo es error si se ha caído una columna que sí hace falta.
                perdidas = [c for c in cab[len(fila):] if c in obligatorias]
                if perdidas:
                    inf.error(f"{nombre}:{n} se corta antes de {perdidas}, que son obligatorias")
                    continue
                fila = fila + [""] * (len(cab) - len(fila))
            for col, val in zip(cab, fila):
                # El bug real: ' LIBRE'. El cargador NO hace strip en estos campos, así que el
                # arreglo tiene que ir en el CSV; señalar y no tocar es deliberado.
                if val.strip() and val != val.strip():
                    inf.error(f"{nombre}:{n} columna '{col}' con espacios sobrantes: {val!r}")
            datos.append(dict(zip(cab, (v for v in fila))))

        claves = CLAVES.get(nombre)
        if claves:
            for k, veces in Counter(tuple(r[c] for c in claves) for r in datos).items():
                if veces > 1:
                    inf.error(f"{nombre}: la clave {k} aparece {veces} veces; solo valdrá la última")
        crudo[nombre] = datos
    return crudo, ilegibles


# --------------------------------------------------------------------------- #
#  2. REFERENCIAS — que lo citado exista
# --------------------------------------------------------------------------- #
def revisar_referencias(crudo: dict[str, list[dict]], inf: Informe) -> None:
    turnos = {r["id_turno"] for r in crudo["turnos.csv"]}
    trabs = {r["id_trab"] for r in crudo["trabajadores.csv"]}
    patrones = {r["patron"] for r in crudo["patrones.csv"]}
    municipios = {r["municipio"] for r in crudo["calendarios_municipio.csv"]}
    calendarios = {r["calendario_festivos"] for r in crudo["calendarios_municipio.csv"]}

    for r in crudo["capacidades.csv"]:
        if r["id_trab"] not in trabs:
            inf.error(f"capacidades: trabajador desconocido '{r['id_trab']}'")
        if r["id_turno"] not in turnos:
            inf.error(f"capacidades: turno desconocido '{r['id_turno']}' (de {r['id_trab']})")

    for r in crudo["patrones.csv"]:
        for dia in DIAS:
            v = r[dia]
            if v and v != LIBRE and v not in turnos:
                inf.error(f"patrones: {r['patron']} fila {r['fila']} {dia}='{v}' no es un turno "
                          f"ni LIBRE — el cargador lo descarta y ese día quedará sin prescribir")

    for r in crudo["trabajadores.csv"]:
        w = r["id_trab"]
        if (r.get("patron") or "").strip() and r["patron"] not in patrones:
            inf.error(f"trabajadores: {w} declara el patrón '{r['patron']}', que no existe")
        if (r.get("linea") or "").strip() and r["linea"] not in turnos:
            inf.error(f"trabajadores: {w} declara la línea '{r['linea']}', que no existe")
        muni = (r.get("municipio") or "").strip()
        if muni and muni not in municipios:
            inf.aviso(f"trabajadores: {w} es de '{muni}', que no está en calendarios_municipio.csv "
                      f"— no se le aplicará ningún festivo local")

    for r in crudo["turnos.csv"]:
        if r["municipio"] not in municipios:
            inf.aviso(f"turnos: {r['id_turno']} opera en '{r['municipio']}', sin calendario "
                      f"asignado — solo verá los festivos de ámbito 'Nacional'")

    for a in sorted({r["ambito"].strip() for r in crudo["festivos.csv"]} - calendarios - {"Nacional"}):
        inf.aviso(f"festivos: el ámbito '{a}' no es 'Nacional' ni el calendario de ningún municipio, "
                  f"así que esas fechas no son festivo para nadie")
    for r in crudo["festivos.csv"]:
        if _fecha(r["fecha"]) is None:
            inf.error(f"festivos: fecha '{r['fecha']}' no es DD/MM/AAAA")


# --------------------------------------------------------------------------- #
#  3. CONTRATO — las reglas que los CSV deben cumplir
# --------------------------------------------------------------------------- #
def revisar_contrato(crudo: dict[str, list[dict]], inf: Informe) -> None:
    trabs = {r["id_trab"]: r for r in crudo["trabajadores.csv"]}
    turnos = {r["id_turno"]: r for r in crudo["turnos.csv"]}

    for w, r in trabs.items():
        tipo = r["tipo"].strip()
        if tipo not in TIPOS_TRAB:
            inf.error(f"trabajadores: {w} tiene tipo '{tipo}'; se espera uno de {TIPOS_TRAB}")
        if tipo == "fijo" and not (r.get("linea") or "").strip():
            inf.error(f"trabajadores: el fijo {w} no declara `linea`; su plaza se deriva de ahí "
                      f"y sin ella no tiene ninguna capacidad")
        if tipo == "patron" and not (r.get("patron") or "").strip():
            inf.error(f"trabajadores: {w} es de patrón pero no dice cuál")
        if tipo != "fijo" and (r.get("linea") or "").strip():
            inf.aviso(f"trabajadores: {w} no es fijo pero declara `linea`; se ignora")
        if tipo != "patron" and (r.get("patron") or "").strip():
            inf.aviso(f"trabajadores: {w} no es de patrón pero declara `patron`; se ignora")

        vac = [_fecha(r[c]) for c in ("vac1_inicio", "vac2_inicio")]
        for col, f in zip(("vac1_inicio", "vac2_inicio"), vac):
            if f is None:
                inf.error(f"trabajadores: {w} tiene {col}='{r[col]}'; se espera DD/MM/AAAA")
        if all(vac):
            a, b = sorted(vac)
            if a + timedelta(days=14) >= b:
                inf.aviso(f"trabajadores: las dos quincenas de {w} se solapan o se tocan "
                          f"({a} y {b}); contarán como un único periodo largo")
        crudo_f = (r.get("factor_jornada") or "").strip().replace(",", ".")
        if crudo_f:
            try:
                if not 0 < float(crudo_f) <= 1:
                    inf.error(f"trabajadores: {w} tiene factor_jornada={crudo_f}, fuera de (0,1]")
            except ValueError:
                inf.error(f"trabajadores: {w} tiene factor_jornada='{crudo_f}', que no es un número")

    for r in crudo["turnos.csv"]:
        s = r["id_turno"]
        for col in ("lv", "sabado", "domingo", "festivo"):
            if r[col].strip() not in ("0", "1"):
                inf.error(f"turnos: {s} tiene {col}='{r[col]}'; se espera 0 o 1")
        for col, hh in (("hora_entrada", r["hora_entrada"]), ("hora_salida", r["hora_salida"])):
            if ":" not in hh:
                inf.error(f"turnos: {s} tiene {col}='{hh}'; se espera HH:MM")
        try:
            if float(r["horas_computadas"].strip().replace(",", ".")) <= 0:
                inf.error(f"turnos: {s} computa {r['horas_computadas']} h; debe ser > 0")
        except ValueError:
            inf.error(f"turnos: {s} tiene horas_computadas='{r['horas_computadas']}', no numérico")
        # dem: columna OPCIONAL y sparse — vacía significa 1 (mismo criterio que cargar_datos._cargar_turnos).
        crudo_dem = r["dem"].strip()
        if crudo_dem and not crudo_dem.isdigit():
            inf.error(f"turnos: {s} tiene dem='{crudo_dem}'; se espera un entero >= 0")
        else:
            dem = int(crudo_dem) if crudo_dem else 1
            if dem == 0 and not s.upper().startswith("REF"):
                # Demanda 0 es la marca de un REFUERZO: horas de apoyo sin cobertura detrás, con
                # las que se completa la jornada de quien se queda corto. En esas líneas es lo
                # esperado y no hay nada que avisar; en cualquier otra, sí.
                inf.aviso(f"turnos: {s} tiene demanda 0, así que no es una plaza a cubrir sino un "
                          f"refuerzo de horas; si no era la intención, revísalo")
            elif not any(r[c] == "1" for c in ("lv", "sabado", "domingo", "festivo")):
                inf.aviso(f"turnos: {s} pide {dem} persona(s) pero no opera ningún tipo de día")

    # Nº de trabajadores == nº de filas del patrón. La rotación asigna filas[(orden + semanas) % T]:
    # con más gente que filas, dos comparten fila y hacen el mismo turno el mismo día; con menos,
    # quedan filas que nadie recorre y sus turnos aparecen como hueco todo el año.
    filas_pat = Counter(r["patron"] for r in crudo["patrones.csv"])
    gente_pat = Counter(r["patron"] for r in trabs.values()
                        if r["tipo"].strip() == "patron" and (r.get("patron") or "").strip())
    for p in sorted(set(filas_pat) | set(gente_pat)):
        nf, ng = filas_pat.get(p, 0), gente_pat.get(p, 0)
        if nf and not ng:
            inf.aviso(f"patrones: '{p}' tiene {nf} filas y ningún trabajador; no se usará")
        elif nf != ng:
            inf.error(f"patrones: '{p}' tiene {nf} filas y {ng} trabajadores; deben coincidir")

    # `fila_inicial` (opcional): la fila que hace el trabajador en la PRIMERA semana del horizonte.
    # Es lo que enlaza la rotación con la del año anterior, así que un valor mal puesto no rompe
    # nada visible — sale un cuadrante válido que sencillamente NO continúa donde tocaba.
    # (Que dos acaben en la MISMA fila se comprueba en revisar_viabilidad, sobre los offsets ya
    # resueltos: aquí solo se ven los declarados, y una colisión puede darse entre uno declarado y
    # otro deducido del orden alfabético.)
    declarados_pat: Counter = Counter()
    for w, r in trabs.items():
        crudo_fila = (r.get("fila_inicial") or "").strip()
        if not crudo_fila:
            continue
        if r["tipo"].strip() != "patron":
            inf.aviso(f"trabajadores: {w} no es de patrón pero declara fila_inicial; se ignora")
            continue
        p = (r.get("patron") or "").strip()
        try:
            fi = int(crudo_fila)
        except ValueError:
            inf.error(f"trabajadores: {w} tiene fila_inicial='{crudo_fila}'; se espera un entero")
            continue
        nf = filas_pat.get(p, 0)
        if fi < 0 or (nf and fi >= nf):
            inf.error(f"trabajadores: {w} tiene fila_inicial={fi}, fuera del patrón '{p}' "
                      f"(tiene {nf} filas: 0..{nf - 1})")
            continue
        declarados_pat[p] += 1
    # Mezclar declarados y deducidos es legítimo (el que no declara cae al orden alfabético), pero
    # casi siempre es un olvido: quien empieza a fijar filas suele querer fijarlas todas.
    for p, n in sorted(declarados_pat.items()):
        if n < gente_pat.get(p, 0):
            inf.aviso(f"patrones: en '{p}' solo {n} de {gente_pat.get(p, 0)} trabajadores declaran "
                      f"fila_inicial; los demás la deducen del orden alfabético, que cambia al "
                      f"entrar o salir gente del grupo")

    # Índices de fila: 0..T-1 sin huecos (el cargador ordena por el índice, pero si falta el 2 la
    # rotación se desplaza y nadie lo nota).
    por_patron: dict[str, list[str]] = defaultdict(list)
    for r in crudo["patrones.csv"]:
        por_patron[r["patron"]].append(r["fila"])
    for p, idx in sorted(por_patron.items()):
        try:
            nums = sorted(int(i) for i in idx)
        except ValueError:
            inf.error(f"patrones: '{p}' tiene índices de fila no numéricos: {idx}")
            continue
        if nums != list(range(len(nums))):
            inf.error(f"patrones: '{p}' numera las filas {nums}; se esperaba 0..{len(nums)-1}")

    # capacidades.csv solo declara lo que NO se puede derivar (fijo -> linea, patrón -> su matriz,
    # correturno -> todas las líneas sin cubridor designado). Lo redundante no rompe, pero confunde.
    en_patron: dict[str, set[str]] = defaultdict(set)
    for r in crudo["patrones.csv"]:
        for dia in DIAS:
            if r[dia] and r[dia] != LIBRE:
                en_patron[r["patron"]].add(r[dia])

    # Marcar un día que la línea no opera no rompe nada (`elegible` mira `opera` primero), pero
    # delata una columna copiada sin mirar. Se agrupa por línea: si son cuarenta correturnos, el
    # problema es la línea, no cada uno de ellos.
    dia_muerto: dict[tuple[str, str], list[str]] = defaultdict(list)

    for r in crudo["capacidades.csv"]:
        w, s = r["id_trab"], r["id_turno"]
        t = trabs.get(w)
        if t is None or s not in turnos:
            continue                                       # ya reportado en referencias
        try:
            v = int(r["v"])
        except ValueError:
            inf.error(f"capacidades ({w},{s}): v='{r['v']}' no es un entero")
            continue
        if v < 0:
            inf.error(f"capacidades ({w},{s}): v={v}; 0 = no cubridor, 1 = principal, 2,3… suplentes")
        dias = {c: r[c].strip() for c in ("lv", "sab", "dom", "fest")}
        for c, val in dias.items():
            if val not in ("0", "1"):
                inf.error(f"capacidades ({w},{s}): {c}='{val}'; se espera 0 o 1")
        if v == 0 and not any(val == "1" for val in dias.values()):
            inf.error(f"capacidades ({w},{s}): ningún día marcado y v=0; la fila no habilita nada, "
                      f"equivale a no existir")
        for c, k in (("lv", "lv"), ("sab", "sabado"), ("dom", "domingo"), ("fest", "festivo")):
            if dias[c] == "1" and turnos[s][k] == "0":
                dia_muerto[(s, c)].append(w)
        tipo = t["tipo"].strip()
        if tipo == "fijo" and (t.get("linea") or "").strip() == s:
            inf.aviso(f"capacidades ({w},{s}): redundante, es la línea del fijo y ya se deriva")
        elif tipo == "patron" and s in en_patron.get((t.get("patron") or "").strip(), set()):
            inf.aviso(f"capacidades ({w},{s}): redundante, ese turno ya está en su patrón")

    for (s, c), quienes in sorted(dia_muerto.items()):
        cuantos = f"{len(quienes)} filas marcan" if len(quienes) > 1 else f"{quienes[0]} marca"
        inf.aviso(f"capacidades: {cuantos} '{c}' en {s}, que no opera ese día; la marca sobra")

    # Duplicidad de plaza: dos fijos en la misma línea solo tiene sentido si la línea pide 2.
    plazas = Counter((r.get("linea") or "").strip() for r in trabs.values()
                     if r["tipo"].strip() == "fijo" and (r.get("linea") or "").strip())
    for s, n in sorted(plazas.items()):
        if s in turnos and turnos[s]["dem"].strip().isdigit() and n > int(turnos[s]["dem"]):
            inf.aviso(f"trabajadores: {n} fijos en la línea {s}, que solo pide "
                      f"{turnos[s]['dem']}; sobrará gente sin sitio")


# --------------------------------------------------------------------------- #
#  4. VIABILIDAD — no rompe la carga, pero anticipa un mal cuadrante
# --------------------------------------------------------------------------- #
def revisar_viabilidad(inf: Informe) -> None:
    """Solo corre si el resto pasó: necesita los datos ya cargados por cargar_datos.

    No mide profundidad de líneas críticas ni patrones dedicados (noche/UVI): esas nociones
    vivían en `Turno.prioridad`/`Turno.tipo`, que `cargar_datos.py` ya no deriva — se está
    replanteando cómo expresar la criticidad, así que este nivel se queda con lo que sí puede
    medir sin inventar semántica nueva."""
    d = cargar()
    objetivo = d.config.horas_objetivo
    anio = d.config.anio
    fechas = d.fechas
    inf.nota(f"horizonte: año {anio} (config.toml), objetivo {objetivo} h")

    # Los festivos tienen que ser del año que se resuelve; si no, el cuadrante sale con los
    # festivos de otro año y nada lo delata.
    fuera = sorted({f for fs in d.festivos.values() for f in fs if f.year != anio})
    if fuera:
        inf.error(f"festivos.csv: {len(fuera)} fecha(s) no son del año {anio} "
                  f"(p.ej. {fuera[0]:%d/%m/%Y}); o sobran, o el anio de config.toml está mal")

    # Filas de arranque EFECTIVAS (lo declarado en fila_inicial, o el orden alfabético si no viene).
    # Se mira aquí y no sobre el CSV porque una colisión puede darse entre un trabajador que declara
    # su fila y otro que la deduce del orden: por separado, ninguno de los dos parece mal.
    por_fila: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for w, off in d.offsets.items():
        por_fila[d.trabajadores[w].patron][off].append(w)
    for p, filas_w in sorted(por_fila.items()):
        for off, ws in sorted(filas_w.items()):
            if len(ws) > 1:
                inf.error(f"patrones: en '{p}' la fila {off} la hacen {len(ws)} trabajadores "
                          f"({', '.join(sorted(ws))}); harían turnos idénticos todo el año y otra "
                          f"fila se quedaría sin recorrer. Revisa `fila_inicial`")

    # Balance anual: horas que hay que cubrir contra horas que la plantilla puede dar. Cuenta TODOS
    # los turnos por igual: sin `prioridad` no hay forma de distinguir demanda real de relleno.
    dem = sum(t.dem * t.horas for f in fechas for s, t in d.turnos.items() if d.opera(s, f))
    cap = sum(objetivo * t.factor_jornada for t in d.trabajadores.values())
    # Las vacaciones ya están descontadas del objetivo anual: 1776 es lo que trabaja cada uno.
    if dem > cap:
        inf.error(f"la demanda ({dem:,.0f} h) supera la capacidad de la plantilla ({cap:,.0f} h a "
                  f"{objetivo} h/persona): habrá huecos sí o sí, faltan "
                  f"{(dem - cap) / objetivo:.1f} personas")
    else:
        inf.nota(f"balance anual: demanda {dem:,.0f} h, capacidad {cap:,.0f} h; sobran "
                 f"{(cap - dem) / objetivo:.1f} personas de holgura, que habrá que absorber "
                 f"con refuerzos o dejar sin asignar")

    # Profundidad de cobertura: una línea sin nadie con capacidad será hueco todo el año. (La
    # comprobación adicional de líneas CRÍTICAS se retira junto con `prioridad` — ver docstring.)
    for s in sorted(d.turnos):
        cub = [w for (w, ss), c in d.capacidades.items() if ss == s and c.v >= 1]
        norm = [w for (w, ss), c in d.capacidades.items()
                if ss == s and (c.lv or c.sab or c.dom or c.fest)]
        if not norm and not cub:
            inf.error(f"la línea {s} no tiene a NADIE con capacidad; será hueco todo el año")


# --------------------------------------------------------------------------- #
#  Orquestación
# --------------------------------------------------------------------------- #
def validar() -> Informe:
    """Ejecuta los cuatro niveles, informando de todo lo que se pueda en una sola pasada: arreglar
    los CSV de uno en uno, relanzando entre cada arreglo, sería insufrible.

    Solo hay dos cortes, y los dos son porque seguir daría ruido en vez de información:
      * un fichero ILEGIBLE (ausente, vacío, sin una columna obligatoria) no deja nada que mirar,
        y los niveles 2 y 3 lo verían como si estuviera vacío: todo lo que lo cita saldría roto.
      * el nivel 4 mide el año entero (balance, profundidad, horas por patrón); con referencias
        rotas esos números serían inventados, así que solo corre si no hay ningún error."""
    inf = Informe()

    if not revisar_config(inf):
        inf.nota("no se ha revisado nada más: sin config.toml válido no hay horizonte que medir")
        return inf
    crudo, ilegibles = revisar_formato(inf)
    if ilegibles:
        inf.nota(f"no se ha revisado nada más: {', '.join(ilegibles)} no se puede(n) leer")
        return inf
    revisar_referencias(crudo, inf)
    revisar_contrato(crudo, inf)
    if inf.errores:
        inf.nota("el balance anual y la profundidad de cobertura no se han medido: con estos "
                 "errores los números no significarían nada")
        return inf
    try:
        revisar_viabilidad(inf)
    except Exception as e:                                          # noqa: BLE001
        inf.error(f"los datos no se pueden cargar: {type(e).__name__}: {e}")
    return inf


def main() -> int:
    print(f"Validando {DATA}\n")
    inf = validar()

    for etiqueta, mensajes in (("ERROR", inf.errores), ("aviso", inf.avisos), ("nota ", inf.notas)):
        for m in mensajes:
            print(f"  {etiqueta}  {m}")
        if mensajes:
            print()

    print(f"{len(inf.errores)} errores · {len(inf.avisos)} avisos · {len(inf.notas)} notas")
    if inf.errores:
        print("Con errores no conviene resolver: el cuadrante saldría mal, o no saldría.")
    return 1 if inf.errores else 0


if __name__ == "__main__":
    raise SystemExit(main())

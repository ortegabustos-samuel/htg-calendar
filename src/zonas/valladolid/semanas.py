"""
zonas/valladolid/semanas.py — Lunes a viernes de Valladolid: el reparto semanal de franjas.

EN CONSTRUCCIÓN. Primera pieza: cuántos días de cada FRANJA hace cada uno cada semana. No escribe
en el plan ni elige días ni líneas: es para ver el reparto antes de colocarlos.

Semana a semana, todas las bases a la vez (los correturnos son de todas):

  1. Días que le tocan a cada fijo y titular: su RITMO, para acabar lo más cerca posible de su
     jornada sin pasarla. Lo que le queda (lo que ya tiene en el plan de todo el año, findes
     incluidos, ya está descontado) se reparte entre las semanas que le quedan en proporción a lo
     que admite cada una. Los correturnos no tienen ritmo: hacen lo que haga falta, sin pasar de
     su jornada; hay más horas de plantilla que trabajo, así que no tienen por qué llegar.
  2. Lo que solo pueden hacer los correturnos: las ausencias de los titulares (vacaciones y los
     días que no les tocan por su ritmo). Se les dan primero.
  3. Las franjas duras de las líneas abiertas (sin titulares ni cubridores), por cola entre fijos
     y correturnos: va quien tenga la menor PROPORCIÓN de días duros sobre días trabajados. Un
     fijo coge la semana entera en esa franja; un correturno, solo los días que le toquen, y puede
     mezclar franjas en la semana. Quien tiene finde esa semana va el último (sus semanas se
     rellenan de mañana), y a igualdad el que no viene de una semana dura.
  4. La mañana recoge al resto.

Mezclar franjas en la semana no rompe el descanso entre días: con mañana < partido < tarde < noche,
si la semana se ordena sin bajar nunca de franja, de un día al siguiente siempre hay 12 h.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from dominio import Datos, Franja, Plan, TipoDia, TipoTrabajador

DUREZA = (Franja.PARTIDO, Franja.TARDE, Franja.NOCHE, Franja.MANANA)   # de más dura a menos
DURAS = {Franja.PARTIDO, Franja.TARDE, Franja.NOCHE}
MINUTOS_DIA = 480       # para pasar de minutos a días


def semanas_del_anio(datos: Datos) -> list[date]:
    return sorted({f - timedelta(days=f.weekday()) for f in datos.lista_dias_calendario})


def lineas_abiertas(datos: Datos, base: str) -> list[str]:
    """Líneas de lunes a viernes de la base que rotan fijos y correturnos: sin titulares ni
    cubridores."""
    return [s for s, t in datos.turnos.items()
            if t.base == base and not t.titulares and not t.cubridores and TipoDia.LV in t.dias]


def grupos_titulares(datos: Datos, base: str) -> dict[tuple[str, ...], list[str]]:
    """Las líneas de lunes a viernes de la base con titulares (y sin cubridores), por grupo de
    titulares: un fijo con su línea, o los tres que se reparten VADN181-183."""
    grupos = defaultdict(list)
    for s, t in datos.turnos.items():
        if t.base == base and t.titulares and not t.cubridores and TipoDia.LV in t.dias:
            grupos[tuple(t.titulares)].append(s)
    return dict(grupos)


def dias_lv(datos: Datos, base: str, lunes: date) -> list[date]:
    """Los días de lunes a viernes de esa semana que son del año y no son festivo en esa base."""
    return [f for f in (lunes + timedelta(days=i) for i in range(5))
            if datos.inicio <= f <= datos.fin and not datos.es_festivo(f, base)]


def tope_semana(datos: Datos, plan: Plan, trabajador_id: str, lunes: date) -> int:
    """Cuántos días de lunes a viernes puede trabajar como mucho esa semana: los que tiene libres,
    menos los descansos semanales que no le salgan del fin de semana, sin pasar de C5. En una
    semana partida por vacaciones o por el principio o el final del año no se reserva descanso."""
    base = datos.trabajadores[trabajador_id].base
    dias = [lunes + timedelta(days=i) for i in range(7)]

    def libre(f):
        return datos.inicio <= f <= datos.fin and datos.disponible(trabajador_id, f) and plan.hueco(trabajador_id, f)

    lv = [f for f in dias_lv(datos, base, lunes) if libre(f)]
    partida = any(not (datos.inicio <= f <= datos.fin) or not datos.disponible(trabajador_id, f) for f in dias)
    descansos_finde = sum(1 for f in dias[5:] if libre(f) and not datos.es_festivo(f, base))
    faltan = 0 if partida else max(0, datos.zona.convenio.libres_semana - descansos_finde)
    trabajados = sum(1 for f in dias if plan.turno_de(trabajador_id, f) is not None)
    return max(0, min(len(lv) - faltan, datos.zona.convenio.dias_max_semana - trabajados))


def con_finde(plan: Plan, trabajador_id: str, lunes: date) -> bool:
    return any(plan.turno_de(trabajador_id, lunes + timedelta(days=i)) is not None for i in (5, 6))


def rotacion(datos: Datos, plan: Plan) -> dict[tuple[str, date], Counter]:
    """(trabajador, lunes) -> {franja: días} de lunes a viernes. Los titulares van con franja
    None: hacen sus líneas. Devuelve también, en la clave (None, lunes), las plazas que se quedan
    sin nadie esa semana."""
    bases = sorted({t.base for t in datos.turnos.values()})
    abiertas = {b: lineas_abiertas(datos, b) for b in bases}
    grupos = {b: grupos_titulares(datos, b) for b in bases}
    rotan = sorted({w for b in bases for s in abiertas[b] for w in datos.pools[s]
                    if datos.trabajadores[w].tipo == TipoTrabajador.FIJO})
    titulares = sorted({w for b in bases for tit in grupos[b] for w in tit})
    corres = sorted(w for w, t in datos.trabajadores.items() if t.tipo == TipoTrabajador.CORRETURNO)
    semanas = semanas_del_anio(datos)
    tope = {(w, l): tope_semana(datos, plan, w, l) for w in rotan + titulares + corres for l in semanas}

    gastados = {w: plan.minutos(w) for w in rotan + titulares + corres}   # findes incluidos
    arrastre = defaultdict(float)          # la parte decimal del ritmo, de semana en semana
    trabajados, duros = Counter(), Counter()
    ultima_dura = {}
    rueda = Counter()                      # en cada grupo de titulares, a qué línea le toca faltar
    resultado = defaultdict(Counter)

    def ritmo(w, i, lunes):
        """Lo que le queda de jornada, repartido en proporción a lo que admite cada semana que
        falta. 4,4 días de media son semanas de 4 y de 5: la parte decimal pasa a la siguiente."""
        quedan = datos.minutos_objetivo(w) - gastados[w]
        capacidad = sum(tope[(w, l)] for l in semanas[i:])
        if not capacidad or not tope[(w, lunes)]:
            return 0
        ideal = quedan / MINUTOS_DIA * tope[(w, lunes)] / capacidad + arrastre[w]
        dias = max(0, min(int(ideal), tope[(w, lunes)], quedan // MINUTOS_DIA))
        arrastre[w] = ideal - dias if dias == int(ideal) else 0.0
        return dias

    def proporcion(w):
        return duros[w] / trabajados[w] if trabajados[w] else 0.0

    def puede_franja(w, lineas, franja, lunes):
        """Si puede alguna de esas líneas de esa franja algún día de lunes a viernes de la semana."""
        return any(datos.turnos[s].franja == franja and w in datos.pools[s]
                   and any(datos.puede(w, s, f) for f in dias_lv(datos, datos.turnos[s].base, lunes))
                   for s in lineas)

    def apuntar(w, lunes, franja, n):
        resultado[(w, lunes)][franja] += n
        trabajados[w] += n
        gastados[w] += n * MINUTOS_DIA
        if franja in DURAS:
            duros[w] += n
            ultima_dura[w] = lunes

    for i, lunes in enumerate(semanas):
        # 1. Días de esta semana
        dias = {w: ritmo(w, i, lunes) for w in rotan + titulares}
        cupo = {w: min(tope[(w, lunes)], max(0, (datos.minutos_objetivo(w) - gastados[w]) // MINUTOS_DIA))
                for w in corres}

        # 2. Lo que hay que cubrir: en las líneas abiertas, y las ausencias de los titulares
        demanda, ausencias = Counter(), Counter()
        lineas_ausencia = defaultdict(set)      # (base, franja) -> líneas de titular que faltan
        for b in bases:
            for s in abiertas[b]:
                for f in dias_lv(datos, b, lunes):
                    if datos.opera(s, f):
                        demanda[(b, datos.turnos[s].franja)] += datos.turnos[s].personas
            for tit, lineas in grupos[b].items():
                plazas = sum(datos.turnos[s].personas for s in lineas
                             for f in dias_lv(datos, b, lunes) if datos.opera(s, f))
                for w in tit:
                    apuntar(w, lunes, None, dias[w])
                for _ in range(max(0, plazas - sum(dias[w] for w in tit))):
                    s = lineas[rueda[tit] % len(lineas)]
                    rueda[tit] += 1
                    ausencias[(b, datos.turnos[s].franja)] += 1
                    lineas_ausencia[(b, datos.turnos[s].franja)].add(s)

        # Cuántos días necesita esta semana de los correturnos, repartidos de uno en uno al que
        # menos lleva: las ausencias, más lo que no lleguen a cubrir los fijos de cada base
        hueco = sum(max(0, sum(n for (b2, _), n in demanda.items() if b2 == b)
                        - sum(dias[w] for w in rotan if datos.trabajadores[w].base == b)) for b in bases)
        presupuesto = Counter()
        for _ in range(sum(ausencias.values()) + hueco):
            libres = [w for w in corres if presupuesto[w] < cupo[w]]
            if not libres:
                break
            presupuesto[min(libres, key=lambda w: (trabajados[w] + presupuesto[w], w))] += 1

        usados = Counter()

        def dar_a_correturno(lineas, franja, n, al_reves=False, hasta_el_cupo=False):
            """n días de esa franja a correturnos, de uno en uno: las duras al de menor
            proporción de días duros, las mañanas (al_reves) al de mayor. Normalmente dentro de su
            presupuesto de la semana; para tapar lo último, hasta lo que admiten. Devuelve los que
            se quedan sin nadie."""
            while n:
                cands = [w for w in corres if (cupo[w] - usados[w] if hasta_el_cupo else presupuesto[w])
                         and puede_franja(w, lineas, franja, lunes)]
                if not cands:
                    return n
                w = (max if al_reves else min)(cands, key=lambda w: (proporcion(w), w))
                apuntar(w, lunes, franja, 1)
                presupuesto[w] = max(0, presupuesto[w] - 1)
                usados[w] += 1
                n -= 1
            return 0

        sin_nadie = Counter()
        # 3a. Las ausencias de los titulares: solo correturnos
        for (b, franja), n in sorted(ausencias.items(), key=lambda x: DUREZA.index(x[0][1])):
            n = dar_a_correturno(lineas_ausencia[(b, franja)], franja, n, al_reves=franja not in DURAS)
            sin_nadie[(b, franja)] += dar_a_correturno(lineas_ausencia[(b, franja)], franja, n,
                                                  al_reves=franja not in DURAS, hasta_el_cupo=True)

        # 3b. Franjas duras de las líneas abiertas, por cola entre fijos y correturnos
        asignada = {}
        for franja in DUREZA[:-1]:
            for b in bases:
                falta = demanda[(b, franja)]
                while falta > 0:
                    fijos = [w for w in rotan if datos.trabajadores[w].base == b and dias[w]
                             and w not in asignada and puede_franja(w, abiertas[b], franja, lunes)]
                    otros = [w for w in corres if presupuesto[w] and puede_franja(w, abiertas[b], franja, lunes)]
                    cola = lambda w: (con_finde(plan, w, lunes), proporcion(w),
                                      ultima_dura.get(w) == lunes - timedelta(weeks=1), w)
                    if not fijos and not otros:
                        sin_nadie[(b, franja)] += dar_a_correturno(abiertas[b], franja, falta, hasta_el_cupo=True)
                        break
                    w = min(fijos + otros, key=cola)
                    # Si a la franja le faltan menos días de los que trae ese fijo, mejor un
                    # correturno: el fijo trabajaría días que no cubren nada
                    if w in fijos and dias[w] > falta and otros:
                        w = min(otros, key=cola)
                    if w in corres:
                        n = min(presupuesto[w], falta)
                        presupuesto[w] -= n
                        usados[w] += n
                    else:
                        n = dias[w]
                        asignada[w] = franja
                    apuntar(w, lunes, franja, n)
                    falta -= n

        # 4. La mañana recoge al resto: primero los fijos, luego los correturnos lo que falte
        for w in rotan:
            if dias[w] and w not in asignada:
                b = datos.trabajadores[w].base
                franja = next((fr for fr in reversed(DUREZA) if puede_franja(w, abiertas[b], fr, lunes)), None)
                if franja is not None:
                    asignada[w] = franja
                    apuntar(w, lunes, franja, dias[w])
                    demanda[(b, franja)] -= dias[w]
        for b in bases:
            if demanda[(b, Franja.MANANA)] > 0:
                sin_nadie[(b, Franja.MANANA)] += dar_a_correturno(abiertas[b], Franja.MANANA,
                                                             demanda[(b, Franja.MANANA)],
                                                             al_reves=True, hasta_el_cupo=True)

        # 5. Si aún quedan plazas sin nadie, un día más a los fijos de esa franja que lo admitan,
        # al que más jornada tiene por delante: el ritmo se lo descuenta en las semanas siguientes
        for (b, franja), n in sin_nadie.items():
            while n:
                cands = [w for w, fr in asignada.items()
                         if fr == franja and datos.trabajadores[w].base == b
                         and sum(resultado[(w, lunes)].values()) < tope[(w, lunes)]
                         and datos.minutos_objetivo(w) - gastados[w] >= MINUTOS_DIA]
                if not cands:
                    break
                w = max(cands, key=lambda w: (datos.minutos_objetivo(w) - gastados[w], w))
                apuntar(w, lunes, franja, 1)
                n -= 1
            sin_nadie[(b, franja)] = n
        resultado[(None, lunes)] = Counter({fr: n for (_, fr), n in sin_nadie.items() if n})
    return resultado


def informe(datos: Datos, plan: Plan, rot: dict, muestra: int = 6) -> None:
    """Huecos por semana, proporción de días duros y horas finales por tipo, y una muestra."""
    semanas = semanas_del_anio(datos)
    letra = {Franja.MANANA: "M", Franja.TARDE: "T", Franja.NOCHE: "N", Franja.PARTIDO: "P", None: "L"}
    tipo = {w: datos.trabajadores[w].tipo for (w, _) in rot if w is not None}
    titulares = {w for (w, l), c in rot.items() if w is not None and None in c}

    huecos = [(l, rot[(None, l)]) for l in semanas if sum(rot[(None, l)].values())]
    print(f"Semanas con plazas sin nadie: {len(huecos)}")
    for l, c in huecos[:10]:
        print(f"   {l:%d/%m}: " + " ".join(f"{letra[fr]}{n}" for fr, n in c.items() if n))

    def fila(w):
        dias = sum(sum(rot[(w, l)].values()) for l in semanas if (w, l) in rot)
        duros = sum(n for l in semanas if (w, l) in rot for fr, n in rot[(w, l)].items() if fr in DURAS)
        horas = (plan.minutos(w) + dias * MINUTOS_DIA) / 60
        return dias, duros, horas

    for nombre, quien in (("fijos que rotan", [w for w in tipo if tipo[w] == TipoTrabajador.FIJO and w not in titulares]),
                          ("titulares", sorted(titulares)),
                          ("correturnos", [w for w in tipo if tipo[w] == TipoTrabajador.CORRETURNO])):
        datos_fila = sorted((fila(w), w) for w in quien)
        props = sorted(d / n * 100 for (n, d, _), _ in datos_fila if n)
        horas = sorted(h for (_, _, h), _ in datos_fila)
        print(f"\n{nombre} ({len(quien)}): % de días duros L-V  mín {props[0]:.0f} · mediana {props[len(props)//2]:.0f}"
              f" · máx {props[-1]:.0f}   |   horas al final  mín {horas[0]:.0f} · mediana {horas[len(horas)//2]:.0f}"
              f" · máx {horas[-1]:.0f}")
        if nombre == "correturnos":
            for (n, d, h), w in datos_fila:
                print(f"   {w}: {n} días L-V, {d / n * 100 if n else 0:.0f} % duros, {h:.0f} h")

    print(f"\nMuestra (minúscula = semana con finde; un correturno puede mezclar, p. ej. M2T2):")
    for w in ([w for w in sorted(tipo) if tipo[w] == TipoTrabajador.FIJO and w not in titulares][:muestra]
              + [w for w in sorted(tipo) if tipo[w] == TipoTrabajador.CORRETURNO][:4]):
        celdas = []
        for l in semanas[:26]:
            c = rot.get((w, l), Counter())
            texto = "".join(f"{letra[fr]}{n}" for fr in reversed(DUREZA) if (n := c.get(fr)))
            texto = texto.lower() if con_finde(plan, w, l) else texto
            celdas.append(f"{texto or '·':5}")
        print(f"   {w} {''.join(celdas)}")

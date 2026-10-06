"""
zonas/valladolid/fijos.py — Lunes a viernes de los FIJOS de Valladolid: un CP-SAT de todo el año.

EN CONSTRUCCIÓN. Paso 2: cobertura, jornada y cuotas de semanas duras.

Llega con el plan hecho (patrones, libranzas, findes y festivos): lo que ya está es constante,
ocupa el día y gasta jornada. Entran todos los fijos: los que rotan, los titulares y los
cubridores. Los correturnos van después, con lo que sobre.

Se resuelve BASE A BASE: los fijos de una base solo hacen líneas de su base, así que cada base es
un problema independiente, y uno pequeño se resuelve mejor y antes que uno grande.

Dos fases:

  1. MODELO ANUAL. Por fijo y día laborable, en qué CASILLA trabaja: (bolsa, franja, minutos).
     La BOLSA son las líneas que rota la misma gente: las abiertas de cada base, o las de cada
     grupo de titulares. Los minutos separan las líneas de una franja que no computan igual (en
     Medina hay de 7, 8 y 9 h), para que la jornada y C6 salgan exactas. No elige la línea. Cada
     semana, una sola ZONA (bolsa y franja) por persona: la semana es estable por construcción.
  2. LÍNEAS. Semana a semana, la línea dentro de cada casilla, directa y sin solver: la misma
     toda la semana si se puede, con el C4 real contra lo que ya hay en el plan.

Restricciones: un turno al día, no sobrecubrir, C4 entre días seguidos (optimista por casilla,
exacto en la fase de líneas), C5, C6, dos libres por semana que no sean festivo, descanso de fin de
semana (sábado y domingo trabajados -> dos libres seguidos entre semana) y el techo de la jornada.
Los descansos por festivo trabajado (DF) no entran: se hacen a mano.

Objetivo por niveles: 0 cobertura de lo que solo pueden hacer fijos · 1 cobertura · 2 suma de
déficits de jornada · 3 peor déficit · 4-6 cuotas de semanas de noche, de partido y de tarde.

El nivel 0 va delante porque lo que no cubran los fijos lo cubren después los correturnos, y hay
plazas a las que ellos no llegan: las noches y las guardias de otra base, o las líneas con
cubridores. Si los fijos no llegan a todo, que lo que se quede sin cubrir sea lo que un correturno
sí puede hacer.

CUOTAS: cada fijo que puede elegir franja tiene una cuota de semanas de cada franja dura de su
bolsa: floor(proporción de esa franja en las plazas del año de su bolsa × sus semanas
disponibles). Se minimiza la suma de lo que cada uno se aleja de su cuota, por arriba o por abajo.
El floor deja las semanas que sobran para los correturnos, en la misma proporción. En una franja
que los correturnos no pueden hacer (la noche de Medina) no hay a quién dejarlas: ahí la cuota es
la parte de cada uno de las semanas que hagan falta en total, en proporción a sus semanas
disponibles. Quien solo
puede una franja (un titular de una sola línea, una conciliación) no tiene cuota: su semana ya
viene dada.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from ortools.sat.python import cp_model

import legal
from cpsat import lexicografico, nivel
from dominio import Datos, Plan, TipoDia, TipoTrabajador

DURAS = ("noche", "partido", "tarde")       # franjas con cuota, en el orden de sus niveles


def lunes_de(fecha: date) -> date:
    return fecha - timedelta(days=fecha.weekday())


def bolsa(datos: Datos, turno_id: str) -> str:
    """Las líneas que rota la misma gente: las abiertas de una base, o las de un grupo de titulares."""
    t = datos.turnos[turno_id]
    return f"{t.base}" if not t.titulares else "titulares " + "/".join(t.titulares)


def casilla(datos: Datos, turno_id: str) -> tuple[str, str, int]:
    t = datos.turnos[turno_id]
    return bolsa(datos, turno_id), t.franja.value, t.minutos_computo


def faltan(datos: Datos, plan: Plan) -> dict[tuple[str, date], int]:
    """(línea, día laborable) -> plazas sin cubrir por lo que ya hay en el plan. Fuera las líneas
    de patrón: esas las cubren el esqueleto y las libranzas."""
    de_patron = {s for patron in datos.patrones.values() for s in patron.turnos}
    cubiertas = Counter((s, f) for (_, f), s in plan.items() if isinstance(s, str))
    resultado = {}
    for s, t in datos.turnos.items():
        if s in de_patron:
            continue
        for f in datos.lista_dias_calendario:
            if datos.opera(s, f) and datos.tipo_dia(f, t.base) == TipoDia.LV:
                n = t.personas - cubiertas[(s, f)]
                if n > 0:
                    resultado[(s, f)] = n
    return resultado


# --------------------------------------------------------------------------- #
#  Fase 1: el modelo anual por casillas
# --------------------------------------------------------------------------- #
class Modelo:
    def __init__(self, datos: Datos, plan: Plan, base: str):
        self.base = base
        self.construir(datos, plan,
                       sorted(w for w, t in datos.trabajadores.items()
                              if t.tipo == TipoTrabajador.FIJO and t.base == base),
                       {(s, f): n for (s, f), n in faltan(datos, plan).items() if datos.turnos[s].base == base})

    def casilla_de(self, turno_id: str):
        """La casilla de una línea en este modelo. El de correturnos la redefine: una por línea."""
        return casilla(self.datos, turno_id)

    def construir(self, datos: Datos, plan: Plan, gente: list[str], plazas: dict):
        """Las variables del modelo para esa gente y esas plazas pendientes."""
        self.datos, self.plan = datos, plan
        self.m = cp_model.CpModel()
        self.gente = gente
        quienes = set(gente)
        self.faltan = plazas

        # opciones[(w, f)][k] = líneas de la casilla k que puede hacer ese día: está en alguno de
        # sus tres grupos (titular, cubridor, pool) y cumple C4 con lo que ya tiene en el plan
        self.opciones = defaultdict(lambda: defaultdict(list))
        for (s, f) in self.faltan:
            k = self.casilla_de(s)
            for grupo in datos.grupos(s, f):
                for w in grupo:
                    if w in quienes and plan.hueco(w, f) and legal.descanso_ok(datos, plan, w, f, s):
                        self.opciones[(w, f)][k].append(s)

        # Cobertura especial: las casillas en las que esa persona solo está como CUBRIDOR de sus
        # líneas (71152292K en H1 y H2). Cubrir no es un reparto: no crea zona ni cuenta en cuotas
        self.especial = {(w, f, k) for (w, f), ks in self.opciones.items() for k, lineas in ks.items()
                         if all(w in datos.turnos[s].cubridores for s in lineas)}

        self.y = {(w, f, k): self.m.NewBoolVar(f"y_{w}_{f:%m%d}_{'_'.join(map(str, k))}")
                  for (w, f), ks in self.opciones.items() for k in ks}
        self.por_dia = defaultdict(list)            # (w, f) -> [(k, var)]
        for (w, f, k), v in self.y.items():
            self.por_dia[(w, f)].append((k, v))
        self.z = {}
        self.z_por = defaultdict(list)              # (w, zona) -> sus variables de semana

    def trabaja(self, w, f):
        """1/0 si ese día ya está decidido en el plan (un descanso es 0); si no, la suma de sus casillas."""
        if not self.plan.hueco(w, f):
            return 0 if self.plan.turno_de(w, f) is None else 1
        return sum(v for _, v in self.por_dia.get((w, f), ()))

    # -- restricciones ------------------------------------------------------ #
    def un_turno_y_cobertura(self):
        for vs in self.por_dia.values():
            self.m.AddAtMostOne(v for _, v in vs)
        plazas = Counter()
        for (s, f), n in self.faltan.items():
            plazas[(self.casilla_de(s), f)] += n
        por_casilla, por_linea = defaultdict(list), defaultdict(list)
        for (w, f, k), v in self.y.items():
            por_casilla[(k, f)].append(v)
            if len(self.opciones[(w, f)][k]) == 1:     # solo puede UNA línea de la casilla
                por_linea[(self.opciones[(w, f)][k][0], f)].append(v)
        for clave, vs in por_casilla.items():
            self.m.Add(sum(vs) <= plazas[clave])
        # Los que solo pueden una línea no pueden pasar de lo que pide esa línea: sin esto el
        # recuento por casilla cuadra pero la fase de líneas no tiene dónde ponerlos
        for (s, f), vs in por_linea.items():
            if len(vs) > self.faltan[(s, f)]:
                self.m.Add(sum(vs) <= self.faltan[(s, f)])

    def zonas(self):
        """Una zona (bolsa y franja) por semana. La zona vale 1 si y solo si esa semana trabaja
        algún día en ella: sin el "solo si" los niveles futuros podrían marcar semanas vacías. Los
        días que cubre como cubridor no son zona: no forman parte de ningún reparto."""
        por_semana = defaultdict(lambda: defaultdict(list))
        for (w, f, k), v in self.y.items():
            if (w, f, k) in self.especial:
                continue                    # cubrir como cubridor no es su zona de la semana
            por_semana[(w, lunes_de(f))][k[:2]].append(v)
        for (w, lunes), zs in por_semana.items():
            elegidas = []
            for zn, vs in zs.items():
                z = self.m.NewBoolVar(f"z_{w}_{lunes:%m%d}_{zn[0]}_{zn[1]}")
                self.z[(w, lunes, zn)] = z
                self.z_por[(w, zn)].append(z)
                for v in vs:
                    self.m.AddImplication(v, z)
                self.m.Add(z <= sum(vs))
                elegidas.append(z)
            self.m.AddAtMostOne(elegidas)

    def descanso(self):
        """C4 entre días seguidos, optimista: se prohíbe el par de casillas solo si NINGÚN par de
        líneas descansa. Dentro de la casilla lo exacto lo pone la fase de líneas."""
        compatibles = {}
        for (w, f), ks in self.opciones.items():
            manana = self.opciones.get((w, f + timedelta(days=1)))
            if not manana:
                continue
            for k1, l1 in ks.items():
                for k2, l2 in manana.items():
                    clave = (tuple(l1), tuple(l2))
                    if clave not in compatibles:
                        compatibles[clave] = any(legal.descanso_entre(self.datos, a, b) for a in l1 for b in l2)
                    if not compatibles[clave]:
                        self.m.AddBoolOr([self.y[(w, f, k1)].Not(),
                                          self.y[(w, f + timedelta(days=1), k2)].Not()])

    def cuenta_como_libre(self, w, f):
        """Un día puede ser descanso semanal si está dentro del año, no es vacaciones y no es un
        festivo de lunes a viernes. Un festivo en sábado o domingo que no trabaja cuenta como su
        descanso semanal; uno entre semana es un descanso de más."""
        festivo_lv = f.weekday() < 5 and self.datos.es_festivo(f, self.datos.trabajadores[w].base)
        return self.datos.inicio <= f <= self.datos.fin and self.datos.disponible(w, f) and not festivo_lv

    def libre(self, w, f):
        return 1 - self.trabaja(w, f) if self.cuenta_como_libre(w, f) else 0

    def libres_posibles(self, w, fechas):
        """Los libres que tendría sin trabajar nada nuevo."""
        return sum(1 for f in fechas if self.cuenta_como_libre(w, f) and self.plan.turno_de(w, f) is None)

    def con_vacaciones(self, w, fechas):
        """Semana partida, por vacaciones o por el principio o el final del año: no se le exige
        el descanso semanal."""
        return any(not (self.datos.inicio <= f <= self.datos.fin) or not self.datos.disponible(w, f)
                   for f in fechas)

    def semanas(self):
        """C5, C6, descanso semanal y descanso de fin de semana, por semana ISO."""
        convenio = self.datos.zona.convenio
        for w in self.gente:
            for lunes in sorted({lunes_de(f) for f in self.datos.lista_dias_calendario}):
                fechas = [lunes + timedelta(days=i) for i in range(7)]
                mios = [(k, v) for f in fechas for k, v in self.por_dia.get((w, f), ())]
                if not mios:
                    continue
                previos = [s for s in (self.plan.turno_de(w, f) for f in fechas) if s is not None]
                self.m.Add(sum(v for _, v in mios) <= convenio.dias_max_semana - len(previos))   # C5
                self.m.Add(sum(k[2] * v for k, v in mios)                                        # C6
                           <= convenio.horas_max_semana * 60
                           - sum(self.datos.turnos[s].minutos_computo for s in previos))
                # Descanso semanal: dos libres que no sean festivo ni vacaciones, salvo en semanas
                # partidas. Si lo que ya trae el plan no los deja, se tolera: solo no se añade más
                if not self.con_vacaciones(w, fechas):
                    self.m.Add(sum(self.libre(w, f) for f in fechas)
                               >= min(convenio.libres_semana, self.libres_posibles(w, fechas)))
                self.descanso_finde(w, fechas)

    def descanso_finde(self, w, fechas):
        """Sábado Y domingo trabajados -> un par de días seguidos libres entre semana. No se exige
        en semanas partidas por vacaciones."""
        sab, dom = self.trabaja(w, fechas[5]), self.trabaja(w, fechas[6])
        if (isinstance(sab, int) and sab == 0) or (isinstance(dom, int) and dom == 0):
            return
        if self.con_vacaciones(w, fechas):
            return
        libres = [self.libre(w, f) for f in fechas[:5]]
        pares = []
        for a, b, f in zip(libres, libres[1:], fechas):
            if isinstance(a, int) and isinstance(b, int):
                pares.append(a * b)
                continue
            p = self.m.NewBoolVar(f"par_{w}_{f:%m%d}")
            self.m.Add(p <= a)
            self.m.Add(p <= b)
            pares.append(p)
        self.m.Add(sum(pares) >= sab + dom - 1)

    def jornada(self):
        """Techo duro de la jornada anual y el déficit de cada uno contra ella."""
        nuevas = defaultdict(list)
        for (w, f, k), v in self.y.items():
            nuevas[w].append(k[2] * v)
        self.deficit = {}
        for w in self.gente:
            objetivo = self.datos.minutos_objetivo(w)
            hechas = self.plan.minutos(w)
            self.m.Add(hechas + sum(nuevas[w]) <= objetivo)
            d = self.m.NewIntVar(0, objetivo, f"deficit_{w}")
            self.m.Add(d == objetivo - hechas - sum(nuevas[w]))
            self.deficit[w] = d

    def sin_correturnos(self):
        """Líneas que ningún correturno puede hacer: las noches y guardias de otra base, y las que
        tienen cubridores. Si los fijos no las cubren, no las cubre nadie."""
        return {s for s in self.datos.turnos
                if not any(self.datos.trabajadores[w].tipo == TipoTrabajador.CORRETURNO
                           for w in self.datos.pools[s])}

    def calcular_cuotas(self):
        """Cuota de semanas de cada franja dura por fijo que puede elegir. Su bolsa es en la que
        más opciones tiene (un cubridor que alguna semana cubre H sigue siendo de la suya).

        En las franjas que no pueden hacer los correturnos la cuota no se fija aquí: se apuntan sus
        semanas disponibles en `a_repartir` y la cuota sale de las semanas que hagan falta en total
        (ver `desviacion_cuota`)."""
        sin_correturnos = self.sin_correturnos()
        solo_fijos = defaultdict(lambda: True)      # (bolsa, franja) -> si ninguna de sus líneas es para correturnos
        plazas, total = Counter(), Counter()
        for (s, f), n in self.faltan.items():
            b, fr, _ = casilla(self.datos, s)
            plazas[(b, fr)] += n
            total[b] += n
            solo_fijos[(b, fr)] &= s in sin_correturnos
        self.proporcion = {(b, fr): n / total[b] for (b, fr), n in plazas.items()}
        self.plazas_franja = plazas
        opciones_bolsa, semanas, franjas = defaultdict(Counter), defaultdict(set), defaultdict(set)
        for (w, f), ks in self.opciones.items():
            semanas[w].add(lunes_de(f))
            for k in ks:
                if (w, f, k) not in self.especial:
                    opciones_bolsa[w][k[0]] += 1
        for (w, _, (b, fr)) in self.z:
            franjas[(w, b)].add(fr)
        self.bolsa_de, self.cuota, self.a_repartir = {}, {}, {}
        self.disponibles = {w: len(ss) for w, ss in semanas.items()}
        for w, por_bolsa in opciones_bolsa.items():
            b = por_bolsa.most_common(1)[0][0]
            self.bolsa_de[w] = b
            if len(franjas[(w, b)]) < 2:
                continue                            # solo puede una franja: no elige
            for fr in DURAS:
                if fr not in franjas[(w, b)]:
                    continue
                if solo_fijos[(b, fr)]:
                    self.a_repartir[(w, fr)] = len(semanas[w])
                    self.cuota[(w, fr)] = 0             # se rellena tras resolver, para el informe
                else:
                    self.cuota[(w, fr)] = int(self.proporcion[(b, fr)] * len(semanas[w]))

    # -- niveles ------------------------------------------------------------ #
    def semanas_en(self, w, fr):
        """Semanas que hace en esa franja de su bolsa: expresión del modelo."""
        return sum(self.z_por[(w, (self.bolsa_de[w], fr))])

    def desviacion_cuota(self, fr):
        """Lo que se alejan de su cuota de esa franja, por arriba o por abajo, sumado.

        Donde la cuota sale de lo que haga falta (`a_repartir`), la parte de cada uno es
        total × a / S, con `a` sus semanas disponibles y S las de todos: |S·mías − a·total| es
        lineal, y es lo que se aleja de su parte multiplicado por S."""
        devs = []
        libres = [w for (w, f2) in self.a_repartir if f2 == fr]
        if libres:
            total = sum(self.semanas_en(w, fr) for w in libres)
            S = sum(self.a_repartir[(w, fr)] for w in libres)
            for w in libres:
                a = self.a_repartir[(w, fr)]
                dev = self.m.NewIntVar(0, 60 * S, f"dev_{fr}_{w}")
                self.m.Add(dev >= S * self.semanas_en(w, fr) - a * total)
                self.m.Add(dev >= a * total - S * self.semanas_en(w, fr))
                devs.append(dev)
        for (w, f2), cuota in self.cuota.items():
            if f2 != fr or (w, f2) in self.a_repartir:
                continue
            hechas = self.semanas_en(w, fr)
            dev = self.m.NewIntVar(0, 60, f"dev_{fr}_{w}")
            self.m.Add(dev >= hechas - cuota)
            self.m.Add(dev >= cuota - hechas)
            devs.append(dev)
        return sum(devs) if devs else None

    def rotacion(self):
        """Rotación garantizada por construcción, para quien tiene cuota (puede elegir franja):

          * de cada franja dura, como mucho una semana en cada VENTANA de g semanas seguidas, con
            g = semanas disponibles // cuota (y al menos 2): con 7 tardes en 49 semanas, una cada 7
            como poco. Así nunca hay dos semanas seguidas de la misma franja dura; una de tarde
            junto a una de noche sí puede darse

        En las franjas que solo pueden hacer fijos la cuota aún no se sabe: se estima por lo alto
        (las plazas del año entre 4 días por semana, repartidas según las semanas disponibles),
        para que la ventana no deje sin cubrir lo que nadie más puede cubrir."""
        semanas = sorted({lunes_de(f) for f in self.datos.lista_dias_calendario})
        por_semana = defaultdict(dict)              # (w, lunes) -> {zona: variable}
        for (w, lunes, zn), z in self.z.items():
            por_semana[(w, lunes)][zn] = z
        con_cuota = {w for (w, _) in self.cuota}
        self.ventana = {}
        for w in sorted(con_cuota):
            b = self.bolsa_de[w]
            for fr in DURAS:
                if (w, fr) not in self.cuota:
                    continue
                if (w, fr) in self.a_repartir:
                    libres = [x for (x, f2) in self.a_repartir if f2 == fr]
                    S = sum(self.a_repartir[(x, fr)] for x in libres)
                    cuota = -(-self.plazas_franja[(b, fr)] * self.disponibles[w] // (4 * S))   # por lo alto
                else:
                    cuota = self.cuota[(w, fr)]
                if cuota <= 0:
                    continue
                g = max(2, self.disponibles[w] // cuota)
                self.ventana[(w, fr)] = g
                for i in range(len(semanas) - g + 1):
                    vs = [por_semana[(w, l)][(b, fr)] for l in semanas[i:i + g]
                          if (b, fr) in por_semana.get((w, l), {})]
                    if len(vs) > 1:
                        self.m.Add(sum(vs) <= 1)

    def peor_deficit(self):
        peor = self.m.NewIntVar(0, max(self.datos.minutos_objetivo(w) for w in self.gente), "peor_deficit")
        self.m.AddMaxEquality(peor, list(self.deficit.values()))
        return peor

    def resolver(self, segundos, hilos, log, cuotas_primero=False):
        """`cuotas_primero`: resolver las cuotas antes que la jornada (en prueba)."""
        self.un_turno_y_cobertura()
        self.zonas()
        self.descanso()
        self.semanas()
        self.jornada()
        self.calcular_cuotas()
        self.rotacion()
        print(f"  {self.base}: {len(self.gente)} fijos · {sum(self.faltan.values())} plazas L-V · "
              f"{len(self.y)} casillas · {len(self.z)} zonas", flush=True)
        m = self.m
        sin_correturnos = self.sin_correturnos()
        protegidas = [v for (w, f, k), v in self.y.items()
                      if all(s in sin_correturnos for s in self.opciones[(w, f)][k])]
        cobertura = [nivel(m, "0 cobertura de lo que solo pueden hacer fijos",
                           lambda: sum(protegidas) if protegidas else None, True),
                     nivel(m, "1 cobertura", lambda: sum(self.y.values()), True)]
        jornada = lambda: [nivel(m, "2 jornada, suma de déficits (min)", lambda: sum(self.deficit.values()), False),
                           nivel(m, "3 jornada, peor déficit (min)", self.peor_deficit, False)]
        cuotas = lambda: [nivel(m, f"{4 + i} cuota de semanas de {fr}", lambda fr=fr: self.desviacion_cuota(fr), False)
                          for i, fr in enumerate(DURAS)]
        # Cada nivel crea sus variables al construirse: se construyen en el orden en que se resuelven
        niveles = cobertura + (cuotas() + jornada() if cuotas_primero else jornada() + cuotas())
        sol = lexicografico(m, niveles, segundos, hilos, log, decision=[*self.y.values(), *self.z.values()])
        if sol is None:
            return None
        self.hechas = {(w, fr): sum(sol.Value(z) for z in self.z_por[(w, (self.bolsa_de[w], fr))])
                       for (w, fr) in self.cuota}
        self.tipo_semana = {(w, lunes): zn[1] for (w, lunes, zn), z in self.z.items() if sol.Value(z)}
        for fr in DURAS:                            # la cuota que les ha tocado de lo que hizo falta
            libres = [w for (w, f2) in self.a_repartir if f2 == fr]
            total = sum(self.hechas[(w, fr)] for w in libres)
            S = sum(self.a_repartir[(w, fr)] for w in libres)
            for w in libres:
                self.cuota[(w, fr)] = round(total * self.a_repartir[(w, fr)] / S)
        return {(w, f): k for (w, f, k), v in self.y.items() if sol.Value(v)}


# --------------------------------------------------------------------------- #
#  Fase 2: la línea concreta, semana a semana
# --------------------------------------------------------------------------- #
def asignar_lineas(datos, plan, casillas, opciones, pendientes):
    """Dentro de cada casilla elige la línea, sin solver. Semana a semana (el domingo->lunes ya
    está escrito en el plan cuando llega la semana siguiente) y casilla a casilla, primero
    quien menos líneas puede: a cada uno la línea que le valga TODOS sus días de la semana (con
    plaza libre y C4 contra lo que ya tiene en el plan), y entre varias, la que hizo la semana
    anterior. Si ninguna le vale la semana entera, día a día, prefiriendo las que ya lleva esa
    semana. Devuelve los días que se quedan sin línea."""
    por_semana = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))   # lunes -> casilla -> w -> días
    for (w, f), k in casillas.items():
        por_semana[lunes_de(f)][k][w].append(f)
    anterior = {}                                   # (w, casilla) -> línea de la semana pasada
    sueltos = []

    def poner(w, f, s):
        plan.poner(w, f, s)
        pendientes[(s, f)] -= 1

    for lunes in sorted(por_semana):
        for k, gente in por_semana[lunes].items():
            def comunes(w):
                return set.intersection(*(set(opciones[(w, f)][k]) for f in gente[w]))
            for w in sorted(gente, key=lambda w: (len(comunes(w)), -len(gente[w]), w)):
                dias = sorted(gente[w])
                candidatas = sorted(comunes(w), key=lambda s: (s != anterior.get((w, k)), s))
                elegida = next((s for s in candidatas
                                if all(pendientes[(s, f)] > 0 and legal.descanso_ok(datos, plan, w, f, s)
                                       for f in dias)), None)
                if elegida is not None:
                    for f in dias:
                        poner(w, f, elegida)
                    anterior[(w, k)] = elegida
                    continue
                usadas = []
                for f in dias:
                    validas = [s for s in opciones[(w, f)][k]
                               if pendientes[(s, f)] > 0 and legal.descanso_ok(datos, plan, w, f, s)]
                    if not validas:
                        sueltos.append((w, f, k))
                        continue
                    s = min(validas, key=lambda s: (s not in usadas, s != anterior.get((w, k)), s))
                    poner(w, f, s)
                    usadas.append(s)
                if usadas:
                    anterior[(w, k)] = usadas[-1]
    return sueltos


def resolver(datos: Datos, plan: Plan, segundos: int = 120, hilos: int = 8, log: bool = False):
    """Coloca a los fijos de lunes a viernes, base a base. Modifica el plan. Devuelve los
    modelos, para el informe."""
    modelos = []
    for base in sorted({t.base for t in datos.trabajadores.values() if t.tipo == TipoTrabajador.FIJO}):
        modelo = Modelo(datos, plan, base)
        modelos.append(modelo)
        casillas = modelo.resolver(segundos, hilos, log)
        if casillas is None:
            print(f"  {base}: *** sin solución ***")
            continue
        sueltos = asignar_lineas(datos, plan, casillas, modelo.opciones, dict(modelo.faltan))
        print(f"  {base}: asignados {len(casillas) - len(sueltos)} días"
              + (f" · {len(sueltos)} sin línea compatible (C4)" if sueltos else ""), flush=True)
    return modelos


def informe(datos: Datos, plan: Plan, modelos: list[Modelo] = ()) -> None:
    """Huecos de lunes a viernes por semana y por franja, horas finales de los fijos y, si se
    pasan los modelos, cómo han quedado las cuotas de semanas duras."""
    huecos = faltan(datos, plan)
    por_semana = Counter()
    for (s, f), n in huecos.items():
        por_semana[(lunes_de(f), datos.turnos[s].franja.value[0].upper())] += n
    semanas = sorted({l for l, _ in por_semana})
    print(f"\nPlazas L-V sin fijo: {sum(huecos.values())} en {len(semanas)} semanas (las cubrirán los correturnos)")
    for l in semanas[:8]:
        print(f"   {l:%d/%m}: " + " ".join(f"{fr}{n}" for (x, fr), n in sorted(por_semana.items()) if x == l))
    fijos = [w for w, t in datos.trabajadores.items() if t.tipo == TipoTrabajador.FIJO]
    horas = sorted((plan.minutos(w) / 60, w) for w in fijos)
    print(f"Horas de los fijos: mín {horas[0][0]:.0f} · mediana {horas[len(horas)//2][0]:.0f} · "
          f"máx {horas[-1][0]:.0f} (objetivo 1776)")
    cortos = [(h, w) for h, w in horas if h < 1776 - 8]
    if cortos:
        print(f"   {len(cortos)} se quedan a más de un turno de su jornada: "
              + ", ".join(f"{w} {h:.0f} h" for h, w in cortos[:8]))
    print(f"Incumplimientos de C4/C5/C6 en el plan: {len(legal.infracciones(datos, plan))}")

    por_franja = Counter()
    for (s, f), n in huecos.items():
        por_franja[datos.turnos[s].franja.value] += n
    print("Lo que queda para los correturnos, por franja: "
          + " · ".join(f"{fr} {n}" for fr, n in por_franja.most_common()))
    resueltos = [m for m in modelos if hasattr(m, "hechas")]
    if not resueltos:
        return
    modelo = Modelo.__new__(Modelo)               # las cuotas de todas las bases juntas
    modelo.proporcion, modelo.cuota, modelo.bolsa_de, modelo.hechas = {}, {}, {}, {}
    modelo.tipo_semana, modelo.ventana = {}, {}
    for m in resueltos:
        for nombre in ("proporcion", "cuota", "bolsa_de", "hechas", "tipo_semana", "ventana"):
            getattr(modelo, nombre).update(getattr(m, nombre))

    print("\nProporción de cada franja dura en cada bolsa y cuota típica (mediana):")
    for (b, fr), p in sorted(modelo.proporcion.items()):
        cuotas = sorted(c for (w, f2), c in modelo.cuota.items() if f2 == fr and modelo.bolsa_de[w] == b)
        if fr in DURAS and cuotas:
            print(f"   {b:52} {fr:8} {p:4.0%} · {len(cuotas):2} fijos con cuota, mediana {cuotas[len(cuotas)//2]} semanas")
    semanas = sorted({lunes_de(f) for f in datos.lista_dias_calendario})
    seguidas, misma, cortos, total_huecos = 0, 0, [], 0
    for m in resueltos:
        for w in {x for (x, _) in m.cuota}:
            seq = [m.tipo_semana.get((w, l)) for l in semanas]
            seguidas += sum(1 for a, b in zip(seq, seq[1:]) if a in DURAS and b in DURAS)
            misma += sum(1 for a, b in zip(seq, seq[1:]) if a in DURAS and a == b)
            for fr in DURAS:
                idx = [i for i, t in enumerate(seq) if t == fr]
                huecos = [b - a for a, b in zip(idx, idx[1:])]
                total_huecos += len(huecos)
                g = m.ventana.get((w, fr), 0)
                cortos += [(w, fr, h, g) for h in huecos if h < g]
    print(f"Rotación: {misma} pares de semanas seguidas de la misma franja dura · {seguidas} de dos "
          f"duras cualquiera · {len(cortos)} de {total_huecos} "
          f"separaciones por debajo de su ventana" + (f" (p. ej. {cortos[:3]})" if cortos else ""))
    for fr in DURAS:
        devs = sorted(((modelo.hechas[(w, f2)] - c, w, c) for (w, f2), c in modelo.cuota.items() if f2 == fr),
                      key=lambda x: (-abs(x[0]), x[1]))
        if not devs:
            continue
        exactos = sum(1 for d, _, _ in devs if d == 0)
        a_uno = sum(1 for d, _, _ in devs if abs(d) == 1)
        print(f"Cuota de {fr}: {len(devs)} fijos · {exactos} exactos · {a_uno} a una semana · "
              f"{len(devs) - exactos - a_uno} más lejos"
              + (": " + ", ".join(f"{w} {c + d}/{c}" for d, w, c in devs[:6] if abs(d) > 1) if devs[0][0] and abs(devs[0][0]) > 1 else ""))

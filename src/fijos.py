"""
fijos.py — El modelo de lunes a viernes de los FIJOS, con los CORRETURNOS en el pool de su
municipio.

Lo que puede hacer cada fijo lo dice su capacidad (ver `cargar_datos._anadir_capacidades_fijo`):
el que declara una línea L-V solo hace esa, y el que no declara nada puede cualquiera de su
municipio. El modelo decide, por persona y semana, su ZONA (municipio + franja) y los días que
trabaja. La semana es estable por construcción; de una semana a otra puede cambiar.

Sábados, domingos y festivos llegan ya repartidos (findes.py) y aquí son constantes: lo que
queda es construir las semanas de lunes a viernes. Cada semana de cada fijo es de UN tipo: con
finde (ya decidido; se rellena de mañana a poder ser), de mañana, de tarde o de partido.

Los correturnos entran en el pool de su municipio (Valladolid) para que los descansos de todos
se coloquen donde no dejan huecos. Van por detrás en la JORNADA: primero se llena la de los fijos,
y los correturnos se reparten lo que sobre. No entran en la equidad de semanas.

Llega con el esqueleto hecho: patrones reales, vacaciones y traspasos a cubridores designados.
Lo que ya está en el plan es constante: ocupa el día y gasta jornada.

Se resuelve POR MUNICIPIO: cada pool de fijos solo hace líneas de su municipio (más lo que
declare en capacidades.csv). Los correturnos pueden cualquier línea, así que su municipio va el
último y recoge lo que les quede a los pueblos.

Dos fases:

  1. MODELO ANUAL. Por fijo y día, en qué CASILLA trabaja: (municipio, franja, horas). Las horas
     separan las líneas de una misma franja que no computan igual (en Medina hay de 7 y de 8),
     para que la jornada y C6 salgan exactas. No elige la línea concreta.
  2. LÍNEAS. Semana a semana, la línea dentro de cada casilla: la misma toda la semana si se
     puede, con el C4 real entre líneas.

La guardia LOCALIZADA no da forma a la semana: se suma a la semana que toque, como hacía el patrón
de Medina (una semana de mañanas y descansos con la guardia del fin de semana).

Restricciones duras: un turno al día, no sobrecubrir, C4 (optimista por casilla, exacto en la
fase de líneas), C5, C6, domingo_ok, descanso de fin de semana y el TECHO de la jornada anual.

Objetivo LEXICOGRÁFICO, cada nivel clavado antes del siguiente y sembrado con el anterior:

  1. cobertura
  2. jornada de los fijos: la suma de déficits contra el objetivo, para llevarlos a todos lo más
     cerca posible del límite (el máximo solo se conforma con que nadie pase del peor)
  3. jornada de los fijos: el mayor déficit, para que lo que falte se reparta
  4. jornada de los correturnos: el mayor déficit, repartiendo entre ellos lo que sobre
  5..8 equidad del número de semanas de cada tipo entre los fijos, dentro de cada municipio y sin
     contar las que tienen finde: partido > tarde > noche > mañana
  9. rotación: no encadenar semanas DURAS (tarde, partido o noche); la mañana rompe la racha. Con
     los recuentos ya fijados por la equidad, solo reordena las semanas de cada uno
  10. semanas con finde rellenas de mañana
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from ortools.sat.python import cp_model

from cargar_datos import DESCANSOS, turno_de
from modelo import decimas, lunes_de, optimizar, reparto, sembrar

# Orden de los niveles de equidad. Hardcodeado a propósito: se ajustará cuando se sepa qué pesa más.
EQUIDAD = ("partido", "tarde", "noche", "mañana")
# Semanas que queman si se encadenan. La de mañana es la que rompe la racha.
DURAS = ("tarde", "partido", "noche")
LOCALIZADO = "localizado"


def pool(datos):
    return sorted(i for i, t in datos.trabajadores.items() if t.tipo in ("fijo", "correturno"))


def lineas_de_fijos(datos):
    """Líneas con demanda que no cubre un patrón: las que reparte este modelo."""
    de_patron = {celda for filas in datos.patrones.values() for fila in filas for celda in fila.values()}
    return sorted(i for i, t in datos.turnos.items() if t.dem > 0 and i not in de_patron)


def casilla(datos, turno_id):
    t = datos.turnos[turno_id]
    return (t.municipio, datos.franja(turno_id), decimas(t.horas))


def faltan(datos, plan, lineas):
    """(línea, fecha) -> plazas sin cubrir por lo que ya hay en el plan."""
    cubiertas = Counter((s, f) for (_, f), s in plan.items() if s not in DESCANSOS)
    resultado = {}
    for s in lineas:
        for f in datos.lista_dias_calendario:
            if datos.opera(s, f):
                n = datos.turnos[s].dem - cubiertas[(s, f)]
                if n > 0:
                    resultado[(s, f)] = n
    return resultado


def descansa(datos, a, b):
    """¿Entre la línea `a` de un día y la `b` del siguiente hay el descanso mínimo?"""
    ancla = date(2001, 1, 1)
    hueco = datos.intervalo(b, ancla + timedelta(days=1))[0] - datos.intervalo(a, ancla)[1]
    return hueco >= timedelta(hours=datos.config.descanso_minimo)


def descanso_con_lo_fijo(datos, plan, w, f, s):
    """C4 de la línea `s` el día `f` contra lo que ya tiene en el plan la víspera y el día después."""
    previo = turno_de(plan, w, f - timedelta(days=1))
    posterior = turno_de(plan, w, f + timedelta(days=1))
    return ((previo is None or descansa(datos, previo, s))
            and (posterior is None or descansa(datos, s, posterior)))


# --------------------------------------------------------------------------- #
#  Fase 1: el modelo anual por casillas
# --------------------------------------------------------------------------- #
class Modelo:
    def __init__(self, datos, plan, libro, gente):
        self.datos, self.plan, self.libro = datos, plan, libro
        self.m = cp_model.CpModel()
        self.gente = gente
        self.faltan = faltan(datos, plan, lineas_de_fijos(datos))

        # opciones[(w, f)][k] = líneas de la casilla k que puede hacer ese día. Vale también la
        # vía excepcional (v>=1): así el cubridor designado cubre la línea especial de un fijo.
        self.opciones = defaultdict(lambda: defaultdict(list))
        for (s, f) in self.faltan:
            k = casilla(datos, s)
            for w in self.gente:
                if (w, f) in plan or not datos.elegible(w, s, f)[0]:
                    continue
                if descanso_con_lo_fijo(datos, plan, w, f, s):
                    self.opciones[(w, f)][k].append(s)

        self.y = {(w, f, k): self.m.NewBoolVar(f"y_{w}_{f:%m%d}_{'_'.join(map(str, k))}")
                  for (w, f), ks in self.opciones.items() for k in ks}
        self.por_dia = defaultdict(list)            # (w, f) -> [(k, var)]
        for (w, f, k), v in self.y.items():
            self.por_dia[(w, f)].append((k, v))
        self.z = {}

    def trabaja(self, w, f):
        """1/0 si ese día ya está decidido en el plan; si no, la suma de sus casillas."""
        if (w, f) in self.plan:
            return 0 if turno_de(self.plan, w, f) is None else 1
        return sum(v for _, v in self.por_dia.get((w, f), ()))

    # -- restricciones ------------------------------------------------------ #
    def un_turno_y_cobertura(self):
        for vs in self.por_dia.values():
            self.m.AddAtMostOne(v for _, v in vs)
        plazas = Counter()
        for (s, f), n in self.faltan.items():
            plazas[(casilla(self.datos, s), f)] += n
        por_casilla, por_linea = defaultdict(list), defaultdict(list)
        for (w, f, k), v in self.y.items():
            por_casilla[(k, f)].append(v)
            if len(self.opciones[(w, f)][k]) == 1:     # solo puede UNA línea de la casilla
                por_linea[(self.opciones[(w, f)][k][0], f)].append(v)
        for clave, vs in por_casilla.items():
            self.m.Add(sum(vs) <= plazas[clave])
        # Los que solo pueden una línea no pueden pasar de lo que pide esa línea: sin esto el
        # recuento por casilla cuadra pero la fase de líneas no tiene dónde ponerlos.
        for (s, f), vs in por_linea.items():
            if len(vs) > self.faltan[(s, f)]:
                self.m.Add(sum(vs) <= self.faltan[(s, f)])

    def zonas(self):
        """Una zona (municipio + franja) por semana, que es su TIPO; la guardia localizada no la
        cuenta. La zona vale 1 si y solo si esa semana trabaja algún día en ella: sin el "solo si"
        la equidad podría marcar semanas vacías para cuadrar los números."""
        por_semana = defaultdict(lambda: defaultdict(list))
        for (w, f, k), v in self.y.items():
            if k[1] != LOCALIZADO:
                por_semana[(w, lunes_de(f))][k[:2]].append(v)
        for (w, lunes), zs in por_semana.items():
            elegidas = []
            for zn, vs in zs.items():
                z = self.m.NewBoolVar(f"z_{w}_{lunes:%m%d}_{zn[0]}_{zn[1]}")
                self.z[(w, lunes, zn)] = z
                for v in vs:
                    self.m.AddImplication(v, z)
                self.m.Add(z <= sum(vs))
                elegidas.append(z)
            self.m.AddAtMostOne(elegidas)

    def con_finde(self, w, lunes):
        """¿Esa semana ya trae sábado o domingo trabajado del reparto de findes?"""
        return any(turno_de(self.plan, w, lunes + timedelta(days=i)) is not None for i in (5, 6))

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
                        compatibles[clave] = any(descansa(self.datos, a, b) for a in l1 for b in l2)
                    if not compatibles[clave]:
                        self.m.AddBoolOr([self.y[(w, f, k1)].Not(),
                                          self.y[(w, f + timedelta(days=1), k2)].Not()])

    def semanas(self):
        """C5, C6, domingo_ok y descanso de fin de semana, por semana ISO."""
        datos, cfg = self.datos, self.datos.config
        for w in self.gente:
            muni = datos.trabajadores[w].municipio
            for lunes in sorted({lunes_de(f) for f in datos.lista_dias_calendario}):
                fechas = [lunes + timedelta(days=i) for i in range(7)]
                mios = [(k, v) for f in fechas for k, v in self.por_dia.get((w, f), ())]
                if not mios:
                    continue
                previos = [s for s in (turno_de(self.plan, w, f) for f in fechas) if s is not None]
                self.m.Add(sum(v for _, v in mios) <= cfg.dias_max_semana - len(previos))      # C5
                self.m.Add(sum(k[2] * v for k, v in mios)                                      # C6
                           <= decimas(cfg.horas_max_semana - sum(datos.turnos[s].horas for s in previos)))
                if datos.tipo_dia(fechas[6], muni) == "DOM" and (w, fechas[6]) in self.por_dia:
                    self.m.Add(self.trabaja(w, fechas[6]) <= self.trabaja(w, fechas[5]))       # domingo_ok
                self.descanso_finde(w, fechas)

    def descanso_finde(self, w, fechas):
        """Sábado Y domingo trabajados → un par de días seguidos libres entre semana."""
        sab, dom = self.trabaja(w, fechas[5]), self.trabaja(w, fechas[6])
        if (isinstance(sab, int) and sab == 0) or (isinstance(dom, int) and dom == 0):
            return
        libres = [1 - self.trabaja(w, f) for f in fechas[:5]]
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
            objetivo = decimas(self.libro.objetivo(w))
            hechas = decimas(self.libro.horas(w))
            self.m.Add(hechas + sum(nuevas[w]) <= objetivo)
            d = self.m.NewIntVar(0, objetivo, f"deficit_{w}")
            self.m.Add(d == objetivo - hechas - sum(nuevas[w]))
            self.deficit[w] = d

    # -- niveles ------------------------------------------------------------ #
    def deficits(self, tipo):
        return [d for w, d in self.deficit.items() if self.datos.trabajadores[w].tipo == tipo]

    def peor_deficit(self, tipo):
        deficits = self.deficits(tipo)
        if not deficits:
            return None
        peor = self.m.NewIntVar(0, decimas(self.datos.config.horas_objetivo), f"peor_deficit_{tipo}")
        self.m.AddMaxEquality(peor, deficits)
        return peor

    def suma_deficit(self, tipo):
        deficits = self.deficits(tipo)
        return sum(deficits) if deficits else None

    def rotacion(self):
        """Pares de semanas seguidas en que la persona hace las dos de tarde, partido o noche.
        Cuatro tardes seguidas suman 3; tarde, mañana, tarde suma 0. Una semana sin trabajo entre
        semana (vacaciones) también rompe la racha."""
        dura = defaultdict(list)                        # (w, lunes) -> zonas duras de esa semana
        for (w, lunes, zn), z in self.z.items():
            if zn[1] in DURAS:
                dura[(w, lunes)].append(z)
        pares = []
        for (w, lunes), zs in dura.items():
            siguiente = dura.get((w, lunes + timedelta(days=7)))
            if siguiente:
                par = self.m.NewBoolVar(f"racha_{w}_{lunes:%m%d}")
                self.m.Add(par >= sum(zs) + sum(siguiente) - 1)      # AtMostOne: cada suma es 0 o 1
                pares.append(par)
        return sum(pares) if pares else None

    def findes_de_manana(self):
        """Semanas con finde cuyo lunes a viernes es de mañana."""
        vs = [z for (w, lunes, zn), z in self.z.items() if zn[1] == "mañana" and self.con_finde(w, lunes)]
        return sum(vs) if vs else None

    def equidad(self, franja):
        """Lo desigual que se reparte el número de semanas de esa franja dentro de cada municipio,
        entre quienes pueden hacerla, sin contar las semanas con finde."""
        semanas_de = defaultdict(list)
        for (w, lunes, zn), z in self.z.items():
            if (zn[1] == franja and not self.con_finde(w, lunes)
                    and self.datos.trabajadores[w].tipo == "fijo"):
                semanas_de[w].append(z)
        por_muni = defaultdict(list)
        for w, zs in semanas_de.items():
            c = self.m.NewIntVar(0, len(zs), f"n_{franja}_{w}")
            self.m.Add(c == sum(zs))
            por_muni[self.datos.trabajadores[w].municipio].append((c, len(zs)))
        terminos = []
        for muni, cuentas in por_muni.items():
            terminos += reparto(self.m, [c for c, _ in cuentas], max(t for _, t in cuentas), f"{franja}_{muni}")
        return sum(terminos) if terminos else None

    def resolver(self, segundos, hilos, log):
        self.un_turno_y_cobertura()
        self.zonas()
        self.descanso()
        self.semanas()
        self.jornada()
        print(f"  {len(self.gente)} personas · {sum(self.faltan.values())} plazas · "
              f"{len(self.y)} casillas · {len(self.z)} zonas")

        variables = {**self.y, **self.z}
        cubiertas = sum(self.y.values())
        # (etiqueta, expresión, maximizar)
        niveles = [("1 cobertura", cubiertas, True),
                   ("2 jornada fijos, suma de déficits (décimas)", self.suma_deficit("fijo"), False),
                   ("3 jornada fijos, peor déficit (décimas)", self.peor_deficit("fijo"), False),
                   ("4 jornada correturnos, peor déficit (décimas)", self.peor_deficit("correturno"), False)]
        niveles += [(f"{5 + i} equidad semanas de {fr}", self.equidad(fr), False)
                    for i, fr in enumerate(EQUIDAD)]
        niveles.append(("9 rotación: semanas duras encadenadas", self.rotacion(), False))
        niveles.append(("10 semanas con finde de mañana", self.findes_de_manana(), True))
        sol = None
        for etiqueta, objetivo, maximizar in niveles:
            if objetivo is None:
                continue
            sembrar(self.m, variables, sol)
            valor, nueva = optimizar(self.m, objetivo, maximizar, segundos, hilos, log, etiqueta)
            if valor is None:
                if sol is None:
                    return None
                break
            self.m.Add(objetivo >= valor if maximizar else objetivo <= valor)
            sol = nueva

        return {(w, f): k for (w, f, k), v in self.y.items() if sol.Value(v)}


# --------------------------------------------------------------------------- #
#  Fase 2: la línea concreta, semana a semana
# --------------------------------------------------------------------------- #
def asignar_lineas(datos, plan, libro, casillas, opciones, pendientes, hilos):
    """Dentro de cada casilla elige la línea: la misma toda la semana si se puede, con el C4 real.
    Va semana a semana porque solo el domingo→lunes une una semana con la siguiente, y ese lado
    ya está escrito en el plan cuando se resuelve la semana siguiente. Devuelve los días que se
    quedan sin línea compatible."""
    por_semana = defaultdict(list)
    for (w, f), k in casillas.items():
        por_semana[lunes_de(f)].append((w, f, k))
    sueltos = 0
    for lunes in sorted(por_semana):
        m = cp_model.CpModel()
        a = {(w, f, s): m.NewBoolVar(f"a_{w}_{f:%m%d}_{s}")
             for w, f, k in por_semana[lunes] for s in opciones[(w, f)][k]
             if descanso_con_lo_fijo(datos, plan, w, f, s)}
        por_dia, por_plaza, por_trab = defaultdict(list), defaultdict(list), defaultdict(dict)
        for (w, f, s), v in a.items():
            por_dia[(w, f)].append(v)
            por_plaza[(s, f)].append(v)
            por_trab[w][(f, s)] = v
        for vs in por_dia.values():
            m.AddAtMostOne(vs)
        for (s, f), vs in por_plaza.items():
            m.Add(sum(vs) <= pendientes[(s, f)])
        usos = []
        for w, suyas in por_trab.items():
            for (f, s), v in suyas.items():
                manana = f + timedelta(days=1)
                for (g, s2), v2 in suyas.items():
                    if g == manana and not descansa(datos, s, s2):
                        m.AddBoolOr([v.Not(), v2.Not()])
            for s in {s for _, s in suyas}:
                u = m.NewBoolVar(f"u_{w}_{s}")
                for (f, s2), v in suyas.items():
                    if s2 == s:
                        m.AddImplication(v, u)
                usos.append(u)
        # Cada día asignado vale más que cualquier cambio de línea en la semana (siete como mucho).
        m.Maximize(8 * sum(a.values()) - sum(usos))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 20
        solver.parameters.num_search_workers = hilos
        solver.Solve(m)
        hechos = set()
        for (w, f, s), v in a.items():
            if solver.Value(v):
                plan[(w, f)] = s
                libro.apunta(w, s)
                pendientes[(s, f)] -= 1
                hechos.add((w, f))
        for w, f, k in por_semana[lunes]:
            if (w, f) not in hechos:
                sueltos += 1
                ayer = turno_de(plan, w, f - timedelta(days=1))
                manana = turno_de(plan, w, f + timedelta(days=1))
                print(f"    sin línea: {w} ({datos.trabajadores[w].tipo}) el {f:%a %d/%m} en {k[0]} "
                      f"{k[1]} · víspera {ayer or '—'} · día siguiente {manana or '—'} · "
                      f"podía {' '.join(opciones[(w, f)][k])}")
    return sueltos


def resolver(datos, plan, libro, segundos=120, hilos=8, log=False):
    """Coloca a fijos y correturnos de lunes a viernes, municipio a municipio y el de los
    correturnos el último. Modifica plan y libro."""
    grupos = defaultdict(list)
    for w in pool(datos):
        grupos[datos.trabajadores[w].municipio].append(w)
    con_correturnos = {datos.trabajadores[w].municipio for w in pool(datos)
                       if datos.trabajadores[w].tipo == "correturno"}
    for muni in sorted(grupos, key=lambda m: (m in con_correturnos, m)):
        print(f"  — {muni}")
        modelo = Modelo(datos, plan, libro, grupos[muni])
        casillas = modelo.resolver(segundos, hilos, log)
        if casillas is None:
            print("  *** sin solución ***")
            continue
        sueltos = asignar_lineas(datos, plan, libro, casillas, modelo.opciones, dict(modelo.faltan), hilos)
        print(f"  asignados {len(casillas) - sueltos} días"
              + (f" · {sueltos} sin línea compatible (C4)" if sueltos else ""))
        # La jornada se mira en el libro, ya con las líneas puestas: es la que de verdad queda.
        for tipo in ("fijo", "correturno"):
            suyos = [w for w in grupos[muni] if datos.trabajadores[w].tipo == tipo]
            if not suyos:
                continue
            cortos = sorted(((-libro.exceso(w), w) for w in suyos if libro.exceso(w) < -0.01), reverse=True)
            if cortos:
                print(f"  {tipo}: {len(cortos)} de {len(suyos)} por debajo de su jornada; los que más: "
                      + ", ".join(f"{w} −{d:.0f} h" for d, w in cortos[:5]))
            else:
                print(f"  {tipo}: todos en su jornada exacta")

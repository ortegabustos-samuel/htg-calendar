"""
zonas/valladolid/findes.py — Reparto de festivos, domingos y sábados entre fijos y correturnos,
antes de lunes a viernes.

Recorre el año en orden y da cada plaza al candidato que MENOS lleva de esa clase. Es un turno por
cola: quien acaba de trabajar pasa al final y no le vuelve a tocar hasta que los demás le alcanzan,
así que iguala los recuentos (equidad) y a la vez los espacia por el año (rotación) sin tener que
medir ninguna de las dos cosas.

Tres pasadas, cada una sobre lo que deja la anterior:

  1. festivos — incluido el sábado festivo, que así ya tiene gente cuando llega su domingo
  2. domingos — quien coge un domingo y no trabaja ese sábado se lleva también una plaza de
                sábado, en la misma línea si puede: domingo_ok se cumple por construcción, y esos
                sábados ya cuentan cuando empieza la pasada de sábados. Para cada domingo se
                prefiere a quien hace esa misma línea el sábado: así el fin de semana de una
                guardia localizada es de una sola persona sin tratarlo aparte
  3. sábados  — lo que quede de sábado, para los que menos sábados llevan

Los candidatos de cada plaza son los del PRIMER grupo de la línea (titulares, cubridores, pool) en
el que haya alguien a quien se pueda poner legalmente: convenio, reglas de Valladolid y jornada.
Cada día se cubren primero las plazas con menos candidatos, para no gastar en una plaza fácil a
alguien que luego era el único para otra. Entre candidatos: menos de esa clase; a igualdad, el que
lleva más tiempo sin trabajar un fin de semana; después, menos fines de semana y festivos en total.

Las líneas de patrón no entran: las cubren el esqueleto y las libranzas.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from dominio import Datos, Plan, TipoDescanso, TipoDia, TipoTrabajador
from zonas.valladolid import reglas

CLASES = (TipoDia.FESTIVO, TipoDia.DOMINGO, TipoDia.SABADO)


def repartir(datos: Datos, plan: Plan) -> Counter:
    """Los tres repartos en orden. Modifica el plan. Devuelve las plazas sin cubrir por clase."""
    gente = [w for w, t in datos.trabajadores.items()
             if t.tipo in (TipoTrabajador.FIJO, TipoTrabajador.CORRETURNO)]
    de_la_gente = set(gente)
    de_patron = {s for patron in datos.patrones.values() for s in patron.turnos}
    lineas = [s for s in datos.turnos if s not in de_patron]

    # Plazas pendientes por (clase, fecha): {línea: cuántas faltan}
    cubiertas = Counter((s, f) for (_, f), s in plan.items() if not isinstance(s, TipoDescanso))
    pendientes = defaultdict(dict)
    for s in lineas:
        for f in datos.lista_dias_calendario:
            if not datos.opera(s, f):
                continue
            clase = datos.tipo_dia(f, datos.turnos[s].base)
            n = datos.turnos[s].personas - cubiertas[(s, f)]
            if clase in CLASES and n > 0:
                pendientes[(clase, f)][s] = n

    # Recuentos: lo que ya trae el plan (ciclos heredados) cuenta
    cuenta = {c: Counter() for c in CLASES}
    ultimo_finde = {}
    for (w, f), s in plan.items():
        if w in de_la_gente and not isinstance(s, TipoDescanso):
            clase = datos.tipo_dia(f, datos.turnos[s].base)
            if clase in CLASES:
                cuenta[clase][w] += 1
            if f.weekday() >= 5:
                ultimo_finde[w] = max(ultimo_finde.get(w, f), f)

    def legal_ahora(w, f, s):
        """Si se le puede poner esa línea ese día con lo que ya tiene en el plan: convenio,
        reglas de Valladolid y jornada anual."""
        return reglas.permite(datos, plan, w, f, s) and plan.cabe(w, s)

    def candidatos(s, f, prueba):
        """Los del primer grupo (titulares, cubridores, pool) en el que alguien pasa la prueba.
        Se pasa al siguiente grupo solo si en ese no hay nadie."""
        for grupo in datos.grupos(s, f):
            validos = [w for w in grupo if prueba(w)]
            if validos:
                return validos
        return []

    def clave(w, clase, f):
        lejos = (f - ultimo_finde[w]).days if w in ultimo_finde else 10_000
        return (cuenta[clase][w], -lejos, sum(cuenta[c][w] for c in CLASES), w)

    def poner(w, f, s, clase):
        plan.poner(w, f, s)
        pendientes[(clase, f)][s] -= 1
        if not pendientes[(clase, f)][s]:
            del pendientes[(clase, f)][s]
        cuenta[clase][w] += 1
        if f.weekday() >= 5:
            ultimo_finde[w] = max(ultimo_finde.get(w, f), f)

    def sabado_para(w, domingo, linea, sabados):
        """La plaza de sábado que se llevaría quien coge ese domingo y no trabaja ese sábado:
        (fecha, línea, clase), o None si no hay ninguna con la que el domingo sea legal. Se busca
        empezando por la misma línea, y solo entre las plazas en las que él está entre los
        candidatos. `sabados` guarda los candidatos de cada sábado mientras el plan no cambie."""
        sabado = domingo - timedelta(days=1)
        clase = datos.tipo_dia(sabado, datos.turnos[linea].base)
        libres = pendientes.get((clase, sabado), {})
        for s in [linea] + sorted(x for x in libres if x != linea):
            if s not in libres:
                continue
            if s not in sabados:
                sabados[s] = set(candidatos(s, sabado, lambda v: legal_ahora(v, sabado, s)))
            if w not in sabados[s]:
                continue
            plan.poner(w, sabado, s)                # de prueba: el domingo necesita el sábado
            vale = legal_ahora(w, domingo, linea)
            plan.quitar(w, sabado)
            if vale:
                return sabado, s, clase
        return None

    def opciones(s, f, clase, sabados):
        """Candidatos para la plaza `s` el día `f`, cada uno con lo que hay que ponerle además:
        (fecha, línea, clase) del sábado de quien coge un domingo y no trabaja ese sábado, o
        None."""
        if clase != TipoDia.DOMINGO:
            return [(w, None) for w in candidatos(s, f, lambda w: legal_ahora(w, f, s))]

        sabado = f - timedelta(days=1)
        extras = {}

        def prueba(w):
            if plan.turno_de(w, sabado) is not None:        # ya trabaja ese sábado
                extras[w] = None
                return legal_ahora(w, f, s)
            extras[w] = sabado_para(w, f, s, sabados)
            return extras[w] is not None
        return [(w, extras[w]) for w in candidatos(s, f, prueba)]

    def misma_linea(opcion, s, f):
        """Si con esa opción hace la misma línea el sábado y el domingo: porque ya la tiene el
        sábado o porque se la lleva ahora. Solo cuenta los domingos."""
        if f.weekday() != 6:
            return False
        w, extra = opcion
        return (extra or (None, None))[1] == s or plan.get(w, f - timedelta(days=1)) == s

    sin_cubrir = Counter()
    for clase in CLASES:
        for f in sorted(f for (c, f) in pendientes if c == clase):
            while pendientes[(clase, f)]:
                sabados = {}                            # el plan acaba de cambiar: se recalculan
                por_plaza = {s: opciones(s, f, clase, sabados) for s in pendientes[(clase, f)]}
                s = min(por_plaza, key=lambda s: (len(por_plaza[s]), s))
                if not por_plaza[s]:
                    sin_cubrir[clase] += pendientes[(clase, f)].pop(s)
                    continue
                w, extra = min(por_plaza[s], key=lambda o: (not misma_linea(o, s, f), clave(o[0], clase, f)))
                if extra is not None:
                    poner(w, *extra)
                poner(w, f, s, clase)

    resumen(datos, plan, gente, sin_cubrir)
    return sin_cubrir


def resumen(datos: Datos, plan: Plan, gente: list[str], sin_cubrir: Counter) -> None:
    """Cobertura, recuentos por base y espaciado: la racha más larga de fines de semana seguidos
    trabajados y el hueco más largo entre dos, por persona."""
    nombres = {TipoDia.FESTIVO: "festivos", TipoDia.SABADO: "sábados", TipoDia.DOMINGO: "domingos"}
    de_la_gente = set(gente)
    cuenta = {c: Counter() for c in CLASES}
    findes = defaultdict(set)
    for (w, f), s in plan.items():
        if w in de_la_gente and not isinstance(s, TipoDescanso):
            clase = datos.tipo_dia(f, datos.turnos[s].base)
            if clase in CLASES:
                cuenta[clase][w] += 1
            if f.weekday() >= 5:
                findes[w].add(f - timedelta(days=f.weekday()))
    for clase in CLASES:
        print(f"  — {nombres[clase]}: {sin_cubrir[clase]} plazas sin cubrir")
        por_base = defaultdict(list)
        for w in gente:
            por_base[datos.trabajadores[w].base].append(cuenta[clase][w])
        for base, cs in sorted(por_base.items()):
            trabajan = [n for n in cs if n]
            if trabajan:
                print(f"    {base:12} {len(trabajan)} personas, de {min(trabajan)} a {max(trabajan)}")
    rachas, huecos = [], []
    for w, lunes in findes.items():
        semanas = sorted(lunes)
        racha = mejor = 1
        for a, b in zip(semanas, semanas[1:]):
            racha = racha + 1 if (b - a).days == 7 else 1
            mejor = max(mejor, racha)
        rachas.append(mejor)
        huecos.append(max(((b - a).days // 7 for a, b in zip(semanas, semanas[1:])), default=0))
    if rachas:
        print(f"  espaciado: racha máx. de findes seguidos {max(rachas)} (media {sum(rachas)/len(rachas):.1f})"
              f" · hueco máx. entre findes {max(huecos)} semanas (media {sum(huecos)/len(huecos):.1f})")

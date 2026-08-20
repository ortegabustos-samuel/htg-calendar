"""
forma.py — Paso C: darle forma semanal al pool de correturnos y mixtos.

El paso C se diseñó para "cubrir lo forzado", y al medirlo resultó que **no hay nada forzado**: de
los 3.692 huecos que deja el paso B, ninguno tiene un solo candidato y el 90 % tiene diez o más.
La razón es que los 17 del pool llegan aquí con cero horas y capacidad para casi cualquier línea,
así que son intercambiables. El reparto del pool no es el residuo del problema: es el problema.

Lo que sí puede decidir una regla, y que además hace falta por sí mismo, es la FORMA de su semana.
CLAUDE.md lo pide explícitamente: a un correturno hay que garantizarle "una semana con un horario
similar y cierta estabilidad en localización". Así que este paso no asigna turnos — asigna, para
cada persona del pool y cada semana, una FRANJA y una ZONA, y con eso acota el dominio del CP-SAT
del paso D en vez de dejarle 3.692 huecos abiertos a 17 candidatos cada uno.

Todo se deriva de la demanda, sin números inventados:

  * La FRANJA sale del reloj (`Datos.franja`).
  * La ZONA sale de si ese municipio tiene, en esa franja, huecos suficientes para llenarle la
    semana a una persona. Si no llega, se agrupa con los demás en "resto": no tiene sentido fijar
    a alguien una semana entera en Tordesillas para darle medio día de trabajo.
  * CUÁNTA gente hace falta en cada franja/zona sale de los huecos reales de esa semana — agosto
    no se parece a febrero, y con solo un 2,3 % de margen entre demanda y capacidad eso importa.
  * QUIÉN va sale de la equidad acumulada: la plaza es para quien menos haya hecho de esa franja
    en lo que va de año. Así las noches y las tardes se reparten solas.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from v3.cargar_datos import Datos

Plan = dict[tuple[str, date], str]
Bloque = tuple[str, str]                # (franja, zona)
RESTO = "resto"


@dataclass(frozen=True)
class Reparto:
    """Lo que produce el paso C."""
    zonas: dict[tuple[str, str], str]           # (franja, municipio) -> zona
    forma: dict[tuple[str, date], Bloque]       # (trabajador, lunes ISO) -> (franja, zona)
    sin_atender: dict[tuple[date, Bloque], int]  # huecos de una semana sin nadie asignado


def lunes_de(f: date) -> date:
    return f - timedelta(days=f.weekday())


def huecos(datos: Datos, plan: Plan) -> list[tuple[str, date]]:
    """Plazas que operan y no tiene nadie, una entrada por plaza sin cubrir."""
    cubiertas: Counter = Counter()
    for (_, f), s in plan.items():
        cubiertas[(s, f)] += 1
    sueltas: list[tuple[str, date]] = []
    for s, t in datos.turnos.items():
        for f in datos.fechas:
            if datos.opera(s, f):
                sueltas += [(s, f)] * max(0, t.dem - cubiertas[(s, f)])
    return sueltas


def dias_por_semana(datos: Datos) -> float:
    """Días que trabaja a la semana alguien del pool para aterrizar en la jornada anual.

    Con 1776 h, turnos de 8 h y 52 semanas salen 4,3 — no los 6 del tope legal. Es la unidad con
    la que se mide todo lo demás: cuántos huecos semanales justifican una zona propia y cuánta
    gente hace falta en cada franja.
    """
    horas_turno = sum(t.horas for t in datos.turnos.values()) / len(datos.turnos)
    semanas = len(datos.fechas) / 7
    return datos.config.horas_objetivo / semanas / horas_turno


def pool_de(datos: Datos) -> list[str]:
    """El pool rodante son SOLO los correturnos. Los mixtos parecían pool por tener capacidades
    amplias, pero son cuasi-fijos: su línea de lunes a viernes y su cuota de findes las decide el
    esqueleto (`esqueleto.colocar_mixtos`), no una forma semanal."""
    return sorted(w for w, t in datos.trabajadores.items() if t.tipo == "correturno")


# --------------------------------------------------------------------------- #
#  Zonas
# --------------------------------------------------------------------------- #
def derivar_zonas(datos: Datos, sueltas: list[tuple[str, date]]) -> dict[tuple[str, str], str]:
    """(franja, municipio) -> zona. Municipio propio si su demanda semanal en esa franja llena la
    semana de una persona; si no, `resto` de esa franja."""
    semanas = len(datos.fechas) / 7
    cuenta: Counter = Counter()
    for s, _ in sueltas:
        cuenta[(datos.franja(s), datos.turnos[s].municipio)] += 1
    umbral = dias_por_semana(datos)
    return {clave: (clave[1] if n / semanas >= umbral else RESTO) for clave, n in cuenta.items()}


def bloque_de(datos: Datos, zonas: dict[tuple[str, str], str], turno: str) -> Bloque:
    fr = datos.franja(turno)
    return (fr, zonas.get((fr, datos.turnos[turno].municipio), RESTO))


# --------------------------------------------------------------------------- #
#  Reparto semanal
# --------------------------------------------------------------------------- #
def _capacidades_por_bloque(datos: Datos, zonas: dict[tuple[str, str], str],
                            pool: list[str]) -> dict[str, set[Bloque]]:
    puede: dict[str, set[Bloque]] = defaultdict(set)
    for (w, s) in datos.capacidades:
        if w in set(pool) and s in datos.turnos:
            puede[w].add(bloque_de(datos, zonas, s))
    return puede


def repartir(datos: Datos, plan: Plan) -> Reparto:
    sueltas = huecos(datos, plan)
    zonas = derivar_zonas(datos, sueltas)
    pool = pool_de(datos)
    puede = _capacidades_por_bloque(datos, zonas, pool)
    ritmo = dias_por_semana(datos)

    por_semana: dict[date, Counter] = defaultdict(Counter)
    for s, f in sueltas:
        por_semana[lunes_de(f)][bloque_de(datos, zonas, s)] += 1

    hecho: dict[str, Counter] = defaultdict(Counter)     # equidad acumulada por franja
    semanas_totales: Counter = Counter()
    forma: dict[tuple[str, date], Bloque] = {}
    sin_atender: dict[tuple[date, Bloque], int] = {}

    for lunes in sorted(por_semana):
        dias = [lunes + timedelta(days=i) for i in range(7)]
        # Necesidad en PERSONAS, fraccionaria: los huecos de la semana entre lo que trabaja una.
        necesita: dict[Bloque, float] = {b: n / ritmo for b, n in por_semana[lunes].items()}
        libres = [w for w in pool if any(datos.disponible(w, f) for f in dias)]

        while libres:
            # El bloque más necesitado que todavía le falte al menos media persona. Se reevalúa en
            # cada vuelta, así que la gente va donde más falta hace en ese momento.
            opciones = [(n, b) for b, n in necesita.items() if n >= 0.5
                        and any(b in puede[w] for w in libres)]
            if not opciones:
                break
            _, bloque = max(opciones)
            candidatos = [w for w in libres if bloque in puede[w]]
            # Equidad: la plaza es para quien menos haya hecho de esta FRANJA, y a igualdad para
            # quien menos semanas lleve asignadas.
            elegido = min(candidatos,
                          key=lambda w: (hecho[w][bloque[0]], semanas_totales[w], w))
            forma[(elegido, lunes)] = bloque
            hecho[elegido][bloque[0]] += 1
            semanas_totales[elegido] += 1
            libres.remove(elegido)
            necesita[bloque] -= 1

        for bloque, n in por_semana[lunes].items():
            if not any(forma.get((w, lunes)) == bloque for w in pool):
                sin_atender[(lunes, bloque)] = n

    return Reparto(zonas=zonas, forma=forma, sin_atender=sin_atender)


# --------------------------------------------------------------------------- #
#  Informe
# --------------------------------------------------------------------------- #
def etiqueta(bloque: Bloque | None) -> str:
    if bloque is None:
        return ""
    return f"{bloque[0][0].upper()}-{bloque[1][:3].upper()}"


def escribir_csv(datos: Datos, rep: Reparto, ruta: Path) -> None:
    """La rejilla que se revisa de un vistazo: 17 filas × 53 semanas."""
    pool = pool_de(datos)
    semanas = sorted({lunes for (_, lunes) in rep.forma})
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as fichero:
        escritor = csv.writer(fichero)
        escritor.writerow(["id_trab", "tipo"] + [f"{l:%d/%m}" for l in semanas])
        for w in pool:
            escritor.writerow([w, datos.trabajadores[w].tipo]
                              + [etiqueta(rep.forma.get((w, l))) for l in semanas])


def resumen(datos: Datos, rep: Reparto) -> None:
    pool = pool_de(datos)
    semanas = sorted({lunes for (_, lunes) in rep.forma})

    zonas_vistas = sorted({b for b in rep.forma.values()})
    print(f"\nPASO C — forma del pool: {len(pool)} personas × {len(semanas)} semanas, "
          f"{len(zonas_vistas)} bloques ({', '.join(etiqueta(b) for b in zonas_vistas)})")

    print(f"\n{'trabajador':<12} {'tipo':<11} {'semanas':>8} {'mañana':>7} {'tarde':>6} "
          f"{'noche':>6} {'cambios':>8}  bloque más frecuente")
    print("-" * 84)
    for w in pool:
        suyas = [rep.forma.get((w, l)) for l in semanas]
        puestas = [b for b in suyas if b]
        cuenta = Counter(b[0] for b in puestas)
        cambios = sum(1 for a, b in zip(puestas, puestas[1:]) if a != b)
        top = Counter(puestas).most_common(1)
        print(f"{w:<12} {datos.trabajadores[w].tipo:<11} {len(puestas):>8} "
              f"{cuenta['mañana']:>7} {cuenta['tarde']:>6} {cuenta['noche']:>6} {cambios:>8}"
              f"  {etiqueta(top[0][0]) + f' ({top[0][1]})' if top else ''}")
    print("-" * 84)

    if rep.sin_atender:
        total = sum(rep.sin_atender.values())
        peor = sorted(rep.sin_atender.items(), key=lambda kv: -kv[1])[:5]
        print(f"  semanas con un bloque sin NADIE asignado: {len(rep.sin_atender)} "
              f"({total} huecos afectados)")
        print("    " + " · ".join(f"{l:%d/%m} {etiqueta(b)}: {n}" for (l, b), n in peor))
    else:
        print("  todos los bloques con demanda tienen a alguien asignado")

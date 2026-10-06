"""
cargar_datos.py — Lee los ficheros de entrada y construye los objetos del dominio.

PROVISIONAL: lee los CSV que hay hoy en data/input. Cuando se sepa de dónde vienen los datos de
verdad, se cambia este fichero y nada más: el resto del programa solo conoce `dominio.py`.

Ejecutado como script imprime un resumen de lo cargado.
"""
from __future__ import annotations

import csv
import os
import tomllib
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from dominio import (ZONAS, Datos, Franja, Patron, Restriccion, TipoDia, TipoTrabajador,
                     Trabajador, Turno)

RAIZ = Path(__file__).resolve().parents[1]
# La interfaz (interfaz/app.py) apunta aquí la carpeta de cada escenario; sin ella, data/input.
DATA = Path(os.environ.get("HT_DATOS") or RAIZ / "data" / "input")

DIAS = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]  # columnas de patrones.csv


def _leer(nombre: str) -> list[dict[str, str]]:
    with open(DATA / nombre, encoding="utf-8", newline="") as archivo:
        return list(csv.DictReader(archivo))


def _hora(texto: str):
    return datetime.strptime(texto.strip(), "%H:%M").time()


def _fecha(texto: str) -> date:
    return datetime.strptime(texto.strip(), "%d/%m/%Y").date()


def _cargar_turnos() -> dict[str, Turno]:
    columnas = {TipoDia.LV: "lv", TipoDia.SABADO: "sabado", TipoDia.DOMINGO: "domingo",
                TipoDia.FESTIVO: "festivo"}
    turnos = {}
    for fila in _leer("turnos.csv"):
        turnos[fila["id_turno"]] = Turno(
            id_turno=fila["id_turno"],
            base=fila["municipio"],
            franja=Franja(fila["franja"].strip().lower()),
            hora_entrada=_hora(fila["hora_entrada"]),
            hora_salida=_hora(fila["hora_salida"]),
            dias={tipo for tipo, columna in columnas.items() if fila[columna] == "1"},
            minutos_computo=round(float(fila["horas_computadas"]) * 60),
            ayudante=fila.get("ayudante") == "1",
        )
    return turnos


def _cargar_trabajadores() -> dict[str, Trabajador]:
    trabajadores = {}
    for fila in _leer("trabajadores.csv"):
        if fila["tipo"].strip() == TipoTrabajador.PATRON.value and not fila["fila_inicial"].strip():
            raise ValueError(f"trabajadores.csv: {fila['id_trab']} es de patrón y no tiene fila_inicial")
        vacaciones = [_fecha(fila["vac1_inicio"]), _fecha(fila["vac2_inicio"])]
        trabajadores[fila["id_trab"]] = Trabajador(
            id=fila["id_trab"],
            nombre=fila["nombre"].strip(),
            tipo=TipoTrabajador(fila["tipo"].strip()),
            patron=fila["patron"].strip() or None,
            vacaciones=[(inicio, inicio + timedelta(days=14)) for inicio in vacaciones],
            base=fila["municipio"].strip(),
            fila_inicial=int(fila["fila_inicial"]) if fila["fila_inicial"].strip() else None,
        )
    return trabajadores


def _cargar_patrones() -> dict[str, Patron]:
    filas = defaultdict(list)
    for fila in _leer("patrones.csv"):
        filas[fila["patron"]].append((int(fila["fila"]), [fila[dia] for dia in DIAS]))
    return {patron: Patron(patron, [celdas for _, celdas in sorted(lista)])
            for patron, lista in filas.items()}


def _cargar_festivos(bases: set[str]) -> dict[str, set[date]]:
    """base -> sus festivos: los nacionales más los del calendario que sigue esa base."""
    calendario = {fila["municipio"]: fila["calendario_festivos"] for fila in _leer("calendarios_municipio.csv")}
    por_ambito = defaultdict(set)
    for fila in _leer("festivos.csv"):
        por_ambito[fila["ambito"].strip()].add(_fecha(fila["fecha"]))
    return {base: por_ambito["Nacional"] | por_ambito[calendario.get(base, base)] for base in bases}


def _aplicar_capacidades(turnos: dict[str, Turno], trabajadores: dict[str, Trabajador]) -> None:
    """capacidades.csv: una fila por (trabajador, línea).

      * v >= 1                       -> cubridor de esa línea, en orden de v
      * v = 0 y lv = 1               -> titular de esa línea
      * v = 0 y solo sab/dom/fest    -> puede hacer esa línea esos días: esos días, solo las
                                        líneas que tenga así declaradas (una Restriccion)
    """
    columnas = {TipoDia.SABADO: "sab", TipoDia.DOMINGO: "dom", TipoDia.FESTIVO: "fest"}
    capacidad = defaultdict(lambda: defaultdict(set))       # trabajador -> tipo de día -> líneas
    for fila in sorted(_leer("capacidades.csv"), key=lambda f: (int(f["v"]), f["id_trab"])):
        trabajador_id, turno = fila["id_trab"], turnos[fila["id_turno"]]
        if int(fila["v"]) >= 1:
            if trabajador_id not in turno.cubridores:
                turno.cubridores.append(trabajador_id)
        elif fila["lv"] == "1":
            if trabajador_id not in turno.titulares:
                turno.titulares.append(trabajador_id)
        else:
            for tipo, columna in columnas.items():
                if fila[columna] == "1":
                    capacidad[trabajador_id][tipo].add(fila["id_turno"])
    for trabajador_id, por_dia in capacidad.items():
        for tipo, lineas in por_dia.items():
            trabajadores[trabajador_id].restricciones.append(Restriccion({tipo}, lineas=lineas))


def _aplicar_restricciones(turnos: dict[str, Turno], trabajadores: dict[str, Trabajador]) -> None:
    """restricciones.csv: una fila por restricción. `dias` y `lineas` separados por `|`.
    `lineas` vacío = sin límite de líneas; NINGUNA = ninguna línea. `desde`/`hasta` vacíos = sin
    ventana horaria. Si el fichero no está, nadie tiene restricciones de este tipo."""
    if not (DATA / "restricciones.csv").exists():
        return
    for fila in _leer("restricciones.csv"):
        texto = fila["lineas"].strip()
        if texto == "NINGUNA":
            lineas = set()
        elif texto:
            lineas = set(texto.split("|"))
            desconocidas = lineas - turnos.keys()
            if desconocidas:
                raise ValueError(f"restricciones.csv: {fila['id_trab']} cita líneas que no existen: {sorted(desconocidas)}")
        else:
            lineas = None
        if fila["id_trab"] not in trabajadores:
            raise ValueError(f"restricciones.csv: el trabajador {fila['id_trab']} no existe")
        trabajadores[fila["id_trab"]].restricciones.append(Restriccion(
            dias={TipoDia(dia) for dia in fila["dias"].split("|")},
            lineas=lineas,
            desde=_hora(fila["desde"]) if fila["desde"].strip() else None,
            hasta=_hora(fila["hasta"]) if fila["hasta"].strip() else None,
        ))


def cargar() -> Datos:
    with open(DATA / "config.toml", "rb") as archivo:
        config = tomllib.load(archivo)
    turnos = _cargar_turnos()
    trabajadores = _cargar_trabajadores()
    _aplicar_capacidades(turnos, trabajadores)
    _aplicar_restricciones(turnos, trabajadores)
    bases = {t.base for t in turnos.values()} | {t.base for t in trabajadores.values()}
    return Datos(
        anio=config["horizonte"]["anio"],
        zona=ZONAS[config["horizonte"]["zona"]],
        turnos=turnos,
        trabajadores=trabajadores,
        patrones=_cargar_patrones(),
        festivos=_cargar_festivos(bases),
    )


if __name__ == "__main__":
    datos = cargar()
    print(f"{DATA}\nZona {datos.zona.nombre} · año {datos.anio} · {len(datos.turnos)} turnos · "
          f"{len(datos.trabajadores)} trabajadores · {len(datos.patrones)} patrones")
    for base, festivos in sorted(datos.festivos.items()):
        print(f"  festivos de {base}: {len(festivos)}")
    print("Restricciones:")
    for t in datos.trabajadores.values():
        for r in t.restricciones:
            lineas = "" if r.lineas is None else f" solo {', '.join(sorted(r.lineas)) or 'ninguna'}"
            horario = (f" de {r.desde:%H:%M} a {r.hasta:%H:%M}" if r.desde or r.hasta else "")
            print(f"  {t.id} {'/'.join(d.value for d in r.dias)}:{lineas}{horario}")
    print("Ficha de líneas: titulares → cubridores → pool")
    for turno_id, turno in datos.turnos.items():
        pool = datos.pools[turno_id]
        fijos = sum(1 for w in pool if datos.trabajadores[w].tipo == TipoTrabajador.FIJO)
        print(f"  {turno_id:10} {turno.base:10} {turno.hora_entrada:%H:%M}-{turno.hora_salida:%H:%M} "
              f"{'/'.join(d.value for d in sorted(turno.dias, key=lambda d: list(TipoDia).index(d))):24} "
              f"tit [{', '.join(turno.titulares)}] → cub [{', '.join(turno.cubridores)}] → "
              f"pool {fijos} fijos + {len(pool) - fijos} correturnos")

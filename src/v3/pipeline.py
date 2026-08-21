#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — Punto de entrada del generador v3 (híbrido: reglas + CP-SAT sobre el residuo).

Se construye por pasos, y CADA paso deja el pipeline ejecutable de punta a punta y produce el
Excel y el CSV de horas. Lo que todavía no está decidido se ve como hueco: la verificación de un
paso es abrir la salida y mirarla, no un test.

  A. esqueleto  — patrones rotados, fijos y vacaciones                              [hecho]
  A2. mixtos    — cuasi-fijos: su línea L-V más su cuota de fines de semana         [hecho]
  B. libranzas  — ceder el exceso de horas, traspasando las plazas con cubridor      [hecho]
  C. forma      — franja y zona de cada semana del pool: acota el dominio del paso D  [hecho]
  D. residuo    — un solo CP-SAT anual reparte los huecos entre los correturnos  [hecho]
  E. equidad    — iguala findes y festivos dentro de cada grupo               [hecho]

Tarda ~3 minutos el año entero. Uso:
    python3 src/v3/pipeline.py
    python3 src/v3/pipeline.py --sin-validar --segundos 300
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src"))

import salida                                            # noqa: E402
import v3.validar_datos as validar_datos                 # noqa: E402
from v3 import equidad, esqueleto, forma, horas, legal, libranzas, residuo  # noqa: E402
from v3.cargar_datos import cargar                       # noqa: E402

SALIDA = RAIZ / "data" / "output"


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual (pipeline v3)")
    p.add_argument("--sin-validar", action="store_true",
                   help="salta la validación de los CSV de entrada")
    p.add_argument("--segundos", type=int, default=300,
                   help="tiempo de solver por NIVEL del paso D. Con los niveles sembrados ninguno "
                        "pasa de ~115 s, así que este límite no llega a tocarse")
    p.add_argument("--hilos", type=int, default=8, help="hilos del solver")
    p.add_argument("--log", action="store_true", help="log detallado del solver")
    p.add_argument("--sin-nivel2", action="store_true",
                   help="salta el nivel 2 (equidad) del paso D y deja esa tarea entera al paso E")
    a = p.parse_args()

    # Validar primero: un CSV con un espacio de más no da un fallo ruidoso, da un cuadrante que
    # parece bueno y no lo es.
    if not a.sin_validar:
        inf = validar_datos.validar()
        for m in inf.errores:
            print(f"  ERROR  {m}")
        for m in inf.avisos:
            print(f"  aviso  {m}")
        if inf.errores:
            print(f"\n{len(inf.errores)} errores en los datos de entrada. Arréglalos y relanza.")
            return 1

    datos = cargar()
    print(f"\nCuadrante {datos.inicio:%d/%m/%Y} – {datos.fin:%d/%m/%Y} · "
          f"{len(datos.trabajadores)} trabajadores · {len(datos.turnos)} líneas · "
          f"objetivo {datos.config.horas_objetivo} h/año")
    print(f"Rotación anclada al lunes {esqueleto.ancla(datos):%d/%m/%Y}")

    # -- Paso A ------------------------------------------------------------- #
    plan = esqueleto.construir(datos)
    libro = horas.LibroHoras.desde_plan(datos, plan)
    horas.resumen(datos, libro, "PASO A — horas que prescribe el esqueleto")
    # Las formas de incumplimiento que el propio patrón produce. Son las pactadas con los
    # trabajadores, y hay que capturarlas ANTES de tocar nada: la auditoría del final compara
    # contra ellas para separar lo heredado de lo que se inventa el pipeline.
    pactadas = legal.pactadas(datos, plan)

    # -- Paso A2 ------------------------------------------------------------ #
    protegidos, flexibles = esqueleto.colocar_mixtos(datos, plan, libro)
    esqueleto.resumen_mixtos(datos, plan, libro)

    # -- Paso B ------------------------------------------------------------- #
    reg = libranzas.ceder(datos, plan, libro, protegidos)
    libranzas.comprobar(datos, plan, libro, reg)
    libranzas.escribir_csv(reg, SALIDA / "cesiones_v3.csv")
    horas.resumen(datos, libro, "PASO B — horas tras ceder el exceso")

    # -- Paso C ------------------------------------------------------------- #
    rep = forma.repartir(datos, plan)
    forma.resumen(datos, rep)
    forma.escribir_csv(datos, rep, SALIDA / "forma_pool_v3.csv")

    # -- Paso D ------------------------------------------------------------- #
    residuo.resolver(datos, plan, libro, rep, flexibles,
                     segundos=a.segundos, hilos=a.hilos, log=a.log,
                     nivel2=not a.sin_nivel2)
    # El canje va DESPUÉS del relleno para que los refuerzos de los correturnos estén ya puestos y
    # entren en el reparto: si no, lo único que se puede gastar son los refuerzos que prescriben los
    # patrones, que son precisamente los que hay que conservar.
    n = residuo.rellenar_refuerzos(datos, plan, libro)
    # El canje EN CADENA va primero: gasta el relleno de los correturnos, así que deja intacta la
    # rotación del patrón. El simple va después, como último recurso, porque paga con el refuerzo
    # que `patrones.csv` le prescribe al propio candidato.
    cadena = residuo.canjear_en_cadena(datos, plan, libro)
    simple = residuo.canjear_refuerzos(datos, plan, libro)
    print(f"  huecos cerrados canjeando un refuerzo: {cadena} en cadena, {simple} directos")
    print(f"  refuerzos de calendario para completar jornada: {n}")
    residuo.resumen(datos, plan, libro)

    # -- Paso E ------------------------------------------------------------- #
    info = equidad.pulir(datos, plan, libro)
    equidad.resumen(datos, plan, info)
    horas.resumen(datos, libro, "PASO E — horas finales")
    legal.auditar(datos, plan, pactadas)

    horas.escribir_csv(datos, plan, libro, SALIDA / "horas_v3.csv")
    salida.escribir_excel_v3(datos, plan)
    print(f"Horas por trabajador: {(SALIDA / 'horas_v3.csv').relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

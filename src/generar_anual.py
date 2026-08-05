#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_anual.py — Genera el cuadrante de un año completo y lo vuelca a data/output/.

Es el punto de entrada de PRODUCCIÓN: resuelve por horizonte rodante (ventanas de 14 días con
cola de 28) y escribe calendario.xlsx + metricas_trabajadores.csv. El `__main__` de salida.py
NO sirve para esto: resuelve solo enero, en un único modelo, como prueba del solver.

Uso:
    python3 src/generar_anual.py                 # el año que diga data/input/config.toml
    python3 src/generar_anual.py --anio 2027
    python3 src/generar_anual.py --segundos 120 --hilos 8

Tarda ~30 min el año entero (27 ventanas × 60 s + construcción del modelo).

El horizonte es SIEMPRE un año natural, y no es un capricho: `resolver_anual` prorratea el
objetivo de 1776 h sobre el horizonte que se le pase, así que con un tramo corto intentaría
encajar la jornada anual entera en esos días y el modelo sale MODEL_INVALID. Para probar cosas
en un tramo, resuelve el año y mira solo el trozo que te interese.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import salida                                                   # noqa: E402
import validar_datos                                            # noqa: E402
from cargar_datos import cargar                                 # noqa: E402
import pulido                                                    # noqa: E402
from modelo import rango_fechas, resolver_anual                  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual en data/output/")
    p.add_argument("--anio", type=int, default=None,
                   help="año a resolver; por defecto el de config.toml")
    p.add_argument("--segundos", type=int, default=60, help="tiempo de solver por ventana")
    p.add_argument("--hilos", type=int, default=16, help="hilos del solver")
    p.add_argument("--ventana", type=int, default=14, help="días por ventana")
    p.add_argument("--cola", type=int, default=28, help="días de contexto congelado")
    p.add_argument("--datos", default=str(RAIZ / "data" / "input"))
    p.add_argument("--log", action="store_true", help="log detallado del solver")
    p.add_argument("--sin-pulir", action="store_true",
                   help="omite la pasada de equidad (intercambios de semana)")
    p.add_argument("--sin-validar", action="store_true",
                   help="arranca aunque los CSV tengan errores (bajo tu responsabilidad)")
    a = p.parse_args()

    # Validar primero: son 45 minutos de cómputo, y un CSV con un espacio de más no da un fallo
    # ruidoso sino un cuadrante que parece bueno y no lo es. Mejor no arrancar.
    inf = validar_datos.validar(a.datos)
    for m in inf.errores:
        print(f"  ERROR  {m}")
    for m in inf.avisos:
        print(f"  aviso  {m}")
    if inf.errores:
        print(f"\n{len(inf.errores)} errores en los datos de entrada. Arréglalos en los CSV, o "
              f"pasa --sin-validar si sabes lo que haces.")
        if not a.sin_validar:
            return 1
    if inf.errores or inf.avisos:
        print()

    datos = cargar(a.datos, a.anio)
    anio = datos.config.anio
    inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
    print(f"Cuadrante {inicio:%d/%m/%Y} – {fin:%d/%m/%Y} · {len(datos.trabajadores)} trabajadores · "
          f"objetivo {datos.config.horas_objetivo} h/año · {a.segundos}s por ventana, {a.hilos} hilos\n", flush=True)

    plan = resolver_anual(datos, inicio, fin, dias_ventana=a.ventana, dias_cola=a.cola,
                          segundos=a.segundos, hilos=a.hilos, gap=0.0, log=a.log)

    # Horizonte que se CONTABILIZA y se entrega: el año natural. El plan trae además los días del
    # lunes anterior al 1 de enero —el rodante arranca ahí para que las semanas ISO estén completas y
    # los topes semanales cuadren desde el primer día—, pero esos son jornada del año ANTERIOR: no
    # cuentan para las 1776, no se pulen, no se rellenan y no salen en el Excel. Siguen en `plan`
    # porque el descanso entre jornadas del 1 de enero se mide contra el 31 de diciembre.
    fechas = rango_fechas(inicio, fin)
    if not a.sin_pulir:
        # Pasada final: rescate de cobertura con los refuerzos, equidad por intercambio de semanas y
        # coherencia. Ninguna puede empeorar la cobertura ni la jornada (invariantes duros).
        #
        # El orden importa. Primero se rescata cobertura: `aprovechar` canjea el refuerzo por un
        # turno real DEL MISMO DÍA (neutro en horas) y `canjear` lo hace ya contra el año entero,
        # soltando refuerzos de otros meses para que quepa el turno del hueco. Los dos mueven a gente
        # a días —y a findes— que no eran suyos, así que `pulir` va DESPUÉS y absorbe ese desajuste
        # en su única pasada (medido en 2026: con el pulido delante la desigualdad acababa en 57, con
        # él detrás en 54, misma cobertura). `coherencia` cierra ordenando las semanas resultantes.
        pulido.resumen(datos, plan, "EQUIDAD antes del pulido")
        pulido.aprovechar(datos, plan, fechas)
        pulido.canjear(datos, plan, fechas)
        pulido.pulir(datos, plan, inicio, fin)
        pulido.coherencia(datos, plan, fechas=fechas)
        pulido.resumen(datos, plan, "EQUIDAD después del pulido")

    if plan and max(f for _, f in plan) < fin:
        print(f"\n*** AVISO: el rodante se detuvo, el plan acaba en {max(f for _, f in plan)} ***")
    salida.generar_anual(datos, fechas, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

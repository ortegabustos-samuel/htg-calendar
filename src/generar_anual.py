#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_anual.py — Genera el cuadrante de un año completo y lo vuelca a data/output/.

Es el punto de entrada de PRODUCCIÓN: resuelve por horizonte rodante (ventanas de 14 días con
cola de 28) y escribe calendario.xlsx + metricas_trabajadores.csv. El `__main__` de salida.py
NO sirve para esto: resuelve solo enero, en un único modelo, como prueba del solver.

Uso:
    python3 src/generar_anual.py                 # año 2026 con los valores por defecto
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
from modelo import HORAS_OBJETIVO, rango_fechas, resolver_anual  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual en data/output/")
    p.add_argument("--anio", type=int, default=2026, help="año a resolver (por defecto 2026)")
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

    inicio, fin = date(a.anio, 1, 1), date(a.anio, 12, 31)
    datos = cargar(a.datos)
    print(f"Cuadrante {inicio:%d/%m/%Y} – {fin:%d/%m/%Y} · {len(datos.trabajadores)} trabajadores · "
          f"objetivo {HORAS_OBJETIVO} h/año · {a.segundos}s por ventana, {a.hilos} hilos\n", flush=True)

    plan = resolver_anual(datos, inicio, fin, dias_ventana=a.ventana, dias_cola=a.cola,
                          segundos=a.segundos, hilos=a.hilos, gap=0.0, log=a.log)

    if not a.sin_pulir:
        # Pasada final de equidad: intercambia semanas para igualar findes y festivos. No puede
        # tocar la cobertura ni la jornada (invariantes duros), así que es seguro por defecto.
        pulido.resumen(datos, plan, "EQUIDAD antes del pulido")
        pulido.pulir(datos, plan, inicio, fin)
        pulido.coherencia(datos, plan)
        pulido.resumen(datos, plan, "EQUIDAD después del pulido")

    fechas = rango_fechas(inicio - timedelta(days=inicio.weekday()), fin)
    if plan and max(f for _, f in plan) < fin:
        print(f"\n*** AVISO: el rodante se detuvo, el plan acaba en {max(f for _, f in plan)} ***")
    salida.generar_anual(datos, fechas, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

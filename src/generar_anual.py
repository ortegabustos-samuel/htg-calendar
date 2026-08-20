#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_anual.py — Genera el cuadrante de un año completo y lo vuelca a data/output/.

ÚNICO punto de entrada del generador. Lee data/input/ (los 6 CSV y config.toml), resuelve por
horizonte rodante y escribe calendario.xlsx + metricas_trabajadores.csv + informe_cobertura.csv.

Uso:
    python3 src/generar_anual.py
    python3 src/generar_anual.py --segundos 120 --hilos 8

Tarda ~30 min el año entero (27 ventanas × 60 s + construcción del modelo).

El horizonte es SIEMPRE el año natural que declara config.toml: no se pasa por parámetro y no
admite tramos. `resolver_anual` prorratea la jornada anual sobre él, así que un tramo corto
intentaría encajar el año entero en esos días y el modelo saldría MODEL_INVALID.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import salida                                                   # noqa: E402
import v3.validar_datos as validar_datos                        # noqa: E402
from v3.cargar_datos import cargar                              # noqa: E402
import pulido                                                    # noqa: E402
from modelo import resolver_anual                                # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual en data/output/")
    p.add_argument("--segundos", type=int, default=60, help="tiempo de solver por ventana")
    p.add_argument("--hilos", type=int, default=16, help="hilos del solver")
    p.add_argument("--log", action="store_true", help="log detallado del solver")
    a = p.parse_args()

    # Validar primero: son 45 minutos de cómputo, y un CSV con un espacio de más no da un fallo
    # ruidoso sino un cuadrante que parece bueno y no lo es. Mejor no arrancar.
    inf = validar_datos.validar()
    for m in inf.errores:
        print(f"  ERROR  {m}")
    for m in inf.avisos:
        print(f"  aviso  {m}")
    if inf.errores:
        print(f"\n{len(inf.errores)} errores en los datos de entrada. Arréglalos y vuelve a lanzar.")
        return 1
    if inf.avisos:
        print()

    datos = cargar()
    print(f"Cuadrante {datos.inicio:%d/%m/%Y} – {datos.fin:%d/%m/%Y} · "
          f"{len(datos.trabajadores)} trabajadores · objetivo {datos.config.horas_objetivo} h/año · "
          f"{a.segundos}s por ventana, {a.hilos} hilos\n", flush=True)

    plan = resolver_anual(datos, segundos=a.segundos, hilos=a.hilos, log=a.log)

    # Pasada final: rescate de cobertura con los refuerzos, equidad por intercambio de semanas y
    # coherencia. Ninguna puede empeorar la cobertura ni la jornada (invariantes duros).
    #
    # El orden importa. Primero se rescata cobertura: `aprovechar` canjea el refuerzo por un turno
    # real DEL MISMO DÍA (neutro en horas) y `canjear` lo hace ya contra el año entero, soltando
    # refuerzos de otros meses para que quepa el turno del hueco. Los dos mueven a gente a días —y a
    # findes— que no eran suyos, así que `pulir` va DESPUÉS y absorbe ese desajuste en su única
    # pasada (medido en 2026: con el pulido delante la desigualdad acababa en 57, con él detrás en
    # 54, misma cobertura). `coherencia` cierra ordenando las semanas resultantes.
    pulido.resumen(datos, plan, "EQUIDAD antes del pulido")
    pulido.aprovechar(datos, plan)
    pulido.canjear(datos, plan)
    pulido.pulir(datos, plan)
    pulido.coherencia(datos, plan)
    pulido.resumen(datos, plan, "EQUIDAD después del pulido")

    if plan and max(f for _, f in plan) < datos.fin:
        print(f"\n*** AVISO: el rodante se detuvo, el plan acaba en {max(f for _, f in plan)} ***")
    salida.generar_anual(datos, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

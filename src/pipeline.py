"""
pipeline.py — Punto de entrada del generador.

Pasos.

  A. Base         — patrones reales (noches y UVI); vacaciones como ausencia
  B. Libranzas    — los patrones ceden su exceso de horas a sus cubridores designados
  S. Findes       — sábados, domingos y festivos repartidos como turnos, con cobertura antes que
                    equidad por pool (findes.py)
  F. Fijos        — CP-SAT anual de lunes a viernes: zona semanal y días de fijos y correturnos
                    (los correturnos en el pool de su municipio, con la jornada de los fijos por
                    delante), luego la línea (fijos.py)

Uso:
    python3 src/pipeline.py
    python3 src/pipeline.py --segundos 300 --hilos 16
"""
from __future__ import annotations

import argparse
import sys
import salida
import base, findes, fijos, horas, legal, libranzas, modelo
from cargar_datos import cargar
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
SALIDA = RAIZ / "data" / "output"
PASOS = 6


def paso(n: int, texto: str) -> None:
    """Marca de avance `[n/6] texto`. La interfaz (interfaz/ejecucion.py) la lee para la barra de
    progreso; en la terminal sirve para saber por dónde va."""
    print(f"[{n}/{PASOS}] {texto}  ({datetime.now():%H:%M})", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual")
    p.add_argument("--segundos", type=int, default=120,
                   help="tiempo de solver por NIVEL de cada CP-SAT (fijos y correturnos)")
    p.add_argument("--hilos", type=int, default=8, help="hilos del solver")
    p.add_argument("--log", action="store_true", help="log detallado del solver")
    p.add_argument("--siembra", choices=("completa", "decision"), default="completa",
                   help="qué se siembra entre niveles: todo lo ya clavado, o solo las decisiones")
    a = p.parse_args()
    modelo.SIEMBRA = a.siembra

    paso(1, "Cargando datos")
    datos = cargar()
    print(f"\nCuadrante {datos.inicio:%d/%m/%Y} – {datos.fin:%d/%m/%Y} · "
          f"{len(datos.trabajadores)} trabajadores · {len(datos.turnos)} líneas · "
          f"objetivo {datos.config.horas_objetivo} h/año")
    print(f"Rotación anclada al lunes {datos.primer_lunes:%d/%m/%Y}")

    # -- Paso A ------------------------------------------------------------- #
    paso(2, "Esqueleto: patrones y vacaciones")
    plan = base.construir(datos)
    libranzas.cubrir_vacaciones(datos, plan)
    libro = horas.LibroHoras.desde_plan(datos, plan)

    # -- Paso B ------------------------------------------------------------- #
    paso(3, "Libranzas de los patrones")
    libranzas.ceder(datos, plan, libro)

    # -- Paso S ------------------------------------------------------------- #
    paso(4, "Sábados, domingos y festivos")
    findes.repartir(datos, plan, libro, segundos=a.segundos, hilos=a.hilos, log=a.log)

    # -- Paso F ------------------------------------------------------------- #
    paso(5, "Fijos y correturnos: lunes a viernes")
    fijos.resolver(datos, plan, libro, segundos=a.segundos, hilos=a.hilos, log=a.log)
    del_pool = {(w, f): s for (w, f), s in plan.items() if w in set(fijos.pool(datos))}
    rotos = legal.integridad(datos, del_pool) + legal.infracciones(datos, del_pool)
    print(f"  integridad y convenio de fijos y correturnos: {len(rotos)} incidencias"
          + (f" (p.ej. {rotos[0]})" if rotos else ""))

    paso(6, "Escribiendo el Excel")
    salida.escribir_excel(datos, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

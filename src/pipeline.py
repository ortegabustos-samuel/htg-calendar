"""
pipeline.py — Punto de entrada del generador.

Pasos.

  A. Base         — patrones rotados y fijos en su línea L-V; vacaciones como ausencia
  B. Libranzas    — ceder el exceso de horas, traspasando las plazas con cubridor designado
  D. Residuo      — el ÚNICO CP-SAT anual: turno de cada correturno y finde de los ex-mixtos
  E. Equidad      — iguala findes y festivos dentro de cada grupo

El antiguo paso C (`forma.py`, franja y zona semanal del pool) ya no existe: su función se ha
fundido dentro del propio CP-SAT del paso D, como nivel 4 del objetivo.

Uso:
    python3 src/pipeline.py
    python3 src/pipeline.py --segundos 300 --hilos 16
"""
from __future__ import annotations

import argparse
import sys
import salida
import base, horas, legal, libranzas, findes, modelo
from cargar_datos import cargar
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
SALIDA = RAIZ / "data" / "output"


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual")
    p.add_argument("--segundos", type=int, default=300,
                   help="tiempo de solver por NIVEL del paso D. Son cinco niveles, así que el "
                        "paso D tarda como mucho cinco veces esto")
    p.add_argument("--hilos", type=int, default=8, help="hilos del solver")
    p.add_argument("--log", action="store_true", help="log detallado del solver")
    a = p.parse_args()

    datos = cargar()
    print(f"\nCuadrante {datos.inicio:%d/%m/%Y} – {datos.fin:%d/%m/%Y} · "
          f"{len(datos.trabajadores)} trabajadores · {len(datos.turnos)} líneas · "
          f"objetivo {datos.config.horas_objetivo} h/año")
    print(f"Rotación anclada al lunes {datos.primer_lunes:%d/%m/%Y}")

    # -- Paso Base ------------------------------------------------------------- #
    plan = base.construir(datos)
    libranzas.cubrir_vacaciones(datos, plan)
    libro = horas.LibroHoras.desde_plan(datos, plan)

    # -- Paso F ------------------------------------------------------------- #
    findes.repartir(datos, plan, libro)

    # -- Paso B ------------------------------------------------------------- #
    libranzas.ceder(datos, plan, libro)

    # -- Paso D ------------------------------------------------------------- #
    modelo.resolver(datos, plan, libro, segundos=a.segundos, hilos=a.hilos, log=a.log)
    #Relleno de refuerzos
    modelo.cambiar_refuerzos(datos, plan, libro, hilos=a.hilos)
    salida.escribir_excel(datos, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
pipeline.py — Punto de entrada del generador: igual para todas las zonas.

  1. Carga de datos
  2. Esqueleto: patrones y vacaciones (común a todas las zonas)
  3. La receta de la zona de `config.toml` (zonas.PASOS): en Valladolid, libranzas, findes,
     fijos, correturnos y descansos
  4. Comprobación del convenio y salida a Excel

Uso:
    python3 src/pipeline.py
    python3 src/pipeline.py --segundos 60 --hilos 16
    python3 src/pipeline.py --datos ~/pruebas/entrada --salida ~/pruebas/salida
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cargar_datos, cpsat, esqueleto, legal, salida     # noqa: E402
from cargar_datos import cargar                            # noqa: E402
from zonas import PASOS                       # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual")
    p.add_argument("--segundos", type=int, default=300,
                   help="tiempo de solver por NIVEL de cada CP-SAT (se ejecuta una vez al año: "
                        "mejor no recortarlo)")
    p.add_argument("--hilos", type=int, default=8, help="hilos del solver")
    p.add_argument("--log", action="store_true", help="log detallado del solver")
    p.add_argument("--siembra", choices=("completa", "decision"), default="completa",
                   help="qué se siembra entre niveles: todo lo ya clavado, o solo las decisiones")
    p.add_argument("--datos", type=Path, help="carpeta de entrada (por defecto data/input o HT_DATOS)")
    p.add_argument("--salida", type=Path, help="carpeta del Excel (por defecto data/output o HT_SALIDA)")
    a = p.parse_args()
    cpsat.SIEMBRA = a.siembra
    if a.datos:
        cargar_datos.DATA = a.datos.expanduser().resolve()
    if a.salida:
        salida.SALIDA = a.salida.expanduser().resolve()

    datos = cargar()
    receta = PASOS[datos.zona.nombre]
    total = 3 + receta.N_PASOS
    hechos = [0]

    def paso(texto: str) -> None:
        """Marca de avance `[n/total] texto`. La interfaz (interfaz/ejecucion.py) la lee para la
        barra de progreso; en la terminal sirve para saber por dónde va."""
        hechos[0] += 1
        print(f"[{hechos[0]}/{total}] {texto}  ({datetime.now():%H:%M})", flush=True)

    paso("Cargando datos")
    print(f"\nZona {datos.zona.nombre} · {datos.inicio:%d/%m/%Y} – {datos.fin:%d/%m/%Y} · "
          f"{len(datos.trabajadores)} trabajadores · {len(datos.turnos)} líneas · "
          f"jornada {datos.zona.convenio.horas_anuales} h ({datos.zona.convenio.nombre})")
    print(f"Rotación anclada al lunes {datos.primer_lunes:%d/%m/%Y}")

    paso("Esqueleto: patrones y vacaciones")
    plan = esqueleto.construir(datos)

    receta.resolver(datos, plan, a.segundos, a.hilos, a.log, paso)

    rotos = legal.infracciones(datos, plan)
    print(f"\nConvenio (C4/C5/C6): {len(rotos)} incumplimientos" + (f" (p. ej. {rotos[0]})" if rotos else ""))

    paso("Escribiendo el Excel")
    salida.escribir_excel(datos, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

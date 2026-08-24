"""
pipeline.py — Punto de entrada del generador.

Pasos.

  1. Base  — patrones rotados, fijos y vacaciones                              
  2. Mixtos    — cuasi-fijos: su línea L-V más su cuota de fines de semana         
  3. Exceso de horas  — ceder el exceso de horas, traspasando las plazas con cubridor      
  4. Busqueda estabilidad — franja y zona de cada semana del pool: acota el dominio del paso D  
  5. Optimizador  — un solo CP-SAT anual reparte los huecos entre los correturnos mas acotado 
  6. Igualdad    — iguala findes y festivos dentro de cada grupo               

Uso:
    python3 src/pipeline.py
    python3 src/pipeline.py --sin-validar --segundos 300
"""
from __future__ import annotations

import argparse
import sys
import salida                                            
import base, equidad, forma, horas, legal, libranzas, residuo
from cargar_datos import cargar                       
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
SALIDA = RAIZ / "data" / "output"


def main() -> int:
    p = argparse.ArgumentParser(description="Genera el cuadrante anual")
    p.add_argument("--segundos", type=int, default=300,
                   help="tiempo de solver por NIVEL del paso D. Con los niveles sembrados ninguno "
                        "pasa de ~115 s, así que este límite no llega a tocarse")
    p.add_argument("--hilos", type=int, default=8, help="hilos del solver")
    p.add_argument("--log", action="store_true", help="log detallado del solver")
    a = p.parse_args()

    datos = cargar()
    print(f"\nCuadrante {datos.inicio:%d/%m/%Y} – {datos.fin:%d/%m/%Y} · "
          f"{len(datos.trabajadores)} trabajadores · {len(datos.turnos)} líneas · "
          f"objetivo {datos.config.horas_objetivo} h/año")
    print(f"Rotación anclada al lunes {datos.primer_lunes:%d/%m/%Y}")

    horas.balance(datos)

    # -- Paso Base ------------------------------------------------------------- #
    plan = base.construir(datos)
    libro = horas.LibroHoras.desde_plan(datos, plan)
    horas.resumen(datos, libro, "PASO A — horas que prescribe el paso base")
    pactadas = legal.pactadas(datos, plan) #REVISION

    # -- Paso A2 ------------------------------------------------------------ #
    protegidos, flexibles = base.colocar_mixtos(datos, plan, libro)
    base.resumen_mixtos(datos, plan, libro)

    # -- Paso B ------------------------------------------------------------- #
    reg = libranzas.ceder(datos, plan, libro, protegidos)
    libranzas.comprobar(datos, plan, libro, reg)
    libranzas.escribir_csv(reg, SALIDA / "cesiones.csv")
    horas.resumen(datos, libro, "PASO B — horas tras ceder el exceso")

    # -- Paso C ------------------------------------------------------------- #
    rep = forma.repartir(datos, plan)
    forma.resumen(datos, rep)
    forma.escribir_csv(datos, rep, SALIDA / "forma_pool.csv")

    # -- Paso D ------------------------------------------------------------- #
    residuo.resolver(datos, plan, libro, rep, flexibles,
                     segundos=a.segundos, hilos=a.hilos, log=a.log)
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

    horas.escribir_csv(datos, plan, libro, SALIDA / "horas.csv")
    salida.escribir_excel(datos, plan)
    print(f"Horas por trabajador: {(SALIDA / 'horas.csv').relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

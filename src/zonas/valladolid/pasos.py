"""
zonas/valladolid/pasos.py — La receta de Valladolid: los pasos con que se construye su cuadrante,
sobre el esqueleto (patrones y vacaciones), que es común a todas las zonas.
"""
from __future__ import annotations

from zonas.valladolid import correturnos, descansos, findes, fijos, libranzas

N_PASOS = 5     # cuántas veces llama a `paso`: el pipeline lo suma a los suyos para la barra de avance


def resolver(datos, plan, segundos, hilos, log, paso) -> None:
    """Los pasos de Valladolid, en orden. Modifica el plan. `paso(texto)` marca el avance."""
    paso("Libranzas de los patrones")
    libranzas.cubrir_vacaciones(datos, plan)
    libranzas.ceder(datos, plan)

    paso("Sábados, domingos y festivos")
    findes.repartir(datos, plan)

    paso("Fijos: lunes a viernes")
    modelos = fijos.resolver(datos, plan, segundos, hilos, log)
    fijos.informe(datos, plan, modelos)

    paso("Correturnos: lo que dejan los fijos")
    modelo = correturnos.resolver(datos, plan, segundos, hilos, log)
    correturnos.informe(datos, plan, modelo)

    paso("Descanso semanal: DS")
    fallos = descansos.senalar(datos, plan)
    print(f"  descansos sin colocar: {len(fallos)}" + (f" (p. ej. {fallos[0]})" if fallos else ""))

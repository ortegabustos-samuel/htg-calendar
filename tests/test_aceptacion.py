#!/usr/bin/env python3
"""Criterios de aceptacion sobre la salida de una corrida anual completa."""
import csv
import statistics as st
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "data" / "output"

# Cifras de la corrida de referencia (commit 47f2230, 60 s por ventana), antes del cambio.
HUECOS_ANTES, CRITICOS_ANTES = 31, 11
SIGMA_ANTES = {"fijo": 6.0, "patron": 6.8, "correturno": 3.3, "mixto": 23.4}


def main() -> int:
    huecos = list(csv.DictReader(open(SALIDA / "informe_cobertura.csv")))
    criticos = [h for h in huecos if int(h["prioridad"]) >= 2]
    metricas = list(csv.DictReader(open(SALIDA / "metricas_trabajadores.csv")))
    for m in metricas:
        m["h"] = float(m["horas_totales"])

    fallos = []

    # Criterio 2: los huecos criticos bajan de 11.
    if len(criticos) >= CRITICOS_ANTES:
        fallos.append(f"criticos {len(criticos)} >= {CRITICOS_ANTES}")

    # Criterio 2 (por LINEA, no por fecha). Fijarse en dias concretos da falsos positivos: al
    # cambiar la adopcion, los huecos de VADP003 del 5 y 6 de agosto desaparecieron y salieron
    # identicos el 1 y el 2, porque lo que falla es que nadie cubre la plaza cuando el titular esta
    # de vacaciones, y las vacaciones duran quince dias. Lo que tiene que bajar es el RECUENTO.
    por_linea = Counter(h["id_turno"] for h in criticos)
    for linea, antes in {"VADP003": 7, "VADU47127": 4}.items():
        if por_linea[linea] >= antes:
            fallos.append(f"{linea}: {por_linea[linea]} huecos criticos, antes {antes}")

    # Criterio 3: la cobertura global no baja.
    if len(huecos) > HUECOS_ANTES:
        fallos.append(f"huecos totales {len(huecos)} > {HUECOS_ANTES}")

    # Criterio 5: nadie por encima de 1776 y la dispersion por tipo no empeora.
    if any(m["h"] > 1776.5 for m in metricas):
        peor = max(metricas, key=lambda m: m["h"])
        fallos.append(f"{peor['id_trab']} pasa de 1776: {peor['h']}")
    for tipo, antes in SIGMA_ANTES.items():
        g = [m["h"] for m in metricas if m["tipo"] == tipo]
        if g and st.pstdev(g) > antes * 1.5:
            fallos.append(f"sigma de {tipo}: {st.pstdev(g):.1f} vs {antes} antes")

    print(f"huecos {len(huecos)} (antes {HUECOS_ANTES}) · "
          f"criticos {len(criticos)} (antes {CRITICOS_ANTES})")
    if criticos:
        print("  criticos por linea:", Counter(h["id_turno"] for h in criticos).most_common())
    for tipo in ("fijo", "patron", "mixto", "correturno"):
        g = [m["h"] for m in metricas if m["tipo"] == tipo]
        if g:
            print(f"  {tipo:<11} n={len(g):<3} media {st.mean(g):.0f}  sigma {st.pstdev(g):.1f}"
                  f"  (sigma antes {SIGMA_ANTES[tipo]})")

    if fallos:
        print("\nFALLOS:")
        for f in fallos:
            print("  -", f)
        return 1
    print("\nOK  todos los criterios de aceptacion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

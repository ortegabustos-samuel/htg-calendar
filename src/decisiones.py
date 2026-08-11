"""Vuelca el LIBRO DE DECISIONES a CSV. Es lo que hace el cuadrante defendible.

Ante cualquier celda del calendario hay aquí una fila que dice qué paso la decidió, con qué regla,
y quién más podía haberlo hecho y por qué no. Los huecos salen igual, con su motivo: un hueco
justificado es una salida válida."""
from __future__ import annotations

import csv
from pathlib import Path

from plan import Plan

SALIDA = Path(__file__).resolve().parents[1] / "data" / "output"


def escribir(plan: Plan, ruta: Path | None = None) -> Path:
    ruta = ruta or SALIDA / "decisiones.csv"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fecha", "trabajador", "turno", "paso", "regla", "descartados", "liberado"])
        for d in sorted(plan.libro, key=lambda x: (x.fecha, x.trabajador)):
            w.writerow([d.fecha.isoformat(), d.trabajador, d.turno or "LIBRE",
                        d.paso, d.regla, " | ".join(d.descartados), "si" if d.liberado else ""])
        for h in sorted(plan.huecos, key=lambda x: (x.fecha, x.turno)):
            w.writerow([h.fecha.isoformat(), "", h.turno, "HUECO", h.motivo, "", ""])
    return ruta

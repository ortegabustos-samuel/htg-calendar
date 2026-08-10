#!/usr/bin/env python3
"""Plan: registra, cede, libera y cuenta cobertura; y el libro no pierde nada."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana
from plan import Plan


def main() -> int:
    assert semana(date(2026, 1, 1)) == (2026, 1)
    assert semana(date(2026, 12, 31)) == (2026, 53)
    # el 29/12/2025 es lunes de la semana 1 de 2026 en ISO
    assert semana(date(2025, 12, 29)) == (2026, 1)

    p = Plan()
    f = date(2026, 3, 14)

    p.asignar("A", f, "VADN001", "reparto", "menos deuda de sabados", descartados=("B: hmax7",))
    assert p.turno_de("A", f) == "VADN001"
    assert p.ocupado("A", f) is True
    assert p.cubierto(f, "VADN001") == 1
    assert p.cubierto(f, "VADN002") == 0

    # una cesion ocupa el dia pero no cubre nada
    p.ceder("C", f, "libranzas", "exceso de jornada")
    assert p.cedido("C", f) is True
    assert p.ocupado("C", f) is True
    assert p.turno_de("C", f) is None

    # asignar dos veces el mismo dia al mismo trabajador es un bug, no un caso
    try:
        p.asignar("A", f, "VADN002", "reparto", "x")
        raise AssertionError("deberia haber reventado")
    except ValueError:
        pass

    # liberar lo devuelve y lo borra
    assert p.liberar("A", f, "reparacion", "deshacer semana") == "VADN001"
    assert p.turno_de("A", f) is None
    assert p.cubierto(f, "VADN001") == 0

    p.hueco(f, "VADN051", "sin candidatos elegibles")
    assert len(p.huecos) == 1
    assert p.huecos[0].motivo == "sin candidatos elegibles"

    # el libro guarda TODO, incluidas las liberaciones
    pasos = [d.paso for d in p.libro]
    assert pasos == ["reparto", "libranzas", "reparacion"], pasos
    assert p.libro[0].descartados == ("B: hmax7",)

    # asignaciones() es lo que come salida.generar_anual: solo turnos reales
    p.asignar("D", f, "VADN003", "rotacion", "su patron")
    assert p.asignaciones() == {("D", f): "VADN003"}

    print(f"OK  plan · {len(p.libro)} decisiones · {len(p.huecos)} huecos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

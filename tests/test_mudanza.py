#!/usr/bin/env python3
"""La mudanza es literal: los simbolos viven en su casa nueva y valen lo mismo."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import calendario
import metricas
from cargar_datos import cargar


def main() -> int:
    assert calendario.semana(date(2026, 1, 1)) == (2026, 1)
    assert calendario.semana(date(2025, 12, 29)) == (2026, 1)
    assert len(calendario.rango_fechas(date(2026, 1, 1), date(2026, 1, 31))) == 31

    assert metricas.JORNADA_LOCALIZADO_SEMANA == 40
    assert metricas.METRICAS == ("sabado", "domingo", "festivo")
    assert set(metricas.LAMBDA) == set(metricas.METRICAS)

    datos = cargar()
    loc = metricas.lineas_localizadas(datos)
    assert "VADU47127" in loc, loc          # la linea UVI es localizada
    assert "VADN001" not in loc

    uvi = metricas._patrones_uvi(datos)
    assert uvi == {"UVI_VAL", "UVI_PRIV"}, uvi
    assert metricas._patrones_noche(datos), "deberia detectar los patrones de noche"

    # peso_cobertura: prioridad 0 no vale cero, y lo critico pesa mas que lo normal
    assert metricas.peso_cobertura(datos.turnos["VADU47127"]) > \
           metricas.peso_cobertura(datos.turnos["VADN001"])

    # jornada_minutos sigue en la moneda de CONSUMO (informe), no en la legal
    p = {("X", date(2026, 3, 16)): "VADN001"}
    assert metricas.jornada_minutos(datos, p)["X"] > 0

    # NADIE del codigo vivo importa ya de modelo salvo el propio modelo/pulido
    raiz = Path(__file__).resolve().parents[1]
    for f in ("salida.py", "validar_datos.py", "diagnostico.py"):
        txt = (raiz / "src" / f).read_text(encoding="utf-8")
        assert "from modelo import" not in txt, f"{f} sigue importando de modelo"

    print("OK  mudanza · calendario y metricas en su sitio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

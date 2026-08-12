#!/usr/bin/env python3
"""Paso 5: cierra huecos deshaciendo, en el orden pactado, sin romper el convenio."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import reparacion as R
from cargar_datos import cargar
from deuda import Deuda
from generar_anual import cobertura, construir
from legal import Legal
from plan import Plan
from reparacion import huecos_reales, reparar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _step4b(datos, ley, dd, sin) -> None:
    """Ancla contra los cuatro defectos que midio la revision de esta tarea: 1.260 "exitos" que
    solo cerraban 43 huecos netos. Tres comprobaciones puntuales, sin volver a llamar a
    `huecos_reales()` antes/despues de cada intento (eso es cuadratico, ver `reparar`)."""

    # (a) mov.2 NUNCA desaloja un turno real (prioridad >= 1) en el dia destino de la cesion
    # movida. Si `d.turno` no fuese un comodin, ese liberar habria abierto un hueco donde no lo
    # habia — exactamente el bug medido: el 100 % de los "exitos" de la primera version eran de
    # suma cero porque el destino aceptaba cualquier prioridad <= 1.
    for d in sin.libro:
        if d.regla == "reparacion mov.2: la cesion se muda aqui":
            assert d.turno is not None and datos.turnos[d.turno].prioridad == 0, (
                f"mov.2 desalojo un turno REAL en el destino: {d}")

    # (b) mov_canjear_refcal cierra un hueco de juguete cuando el UNICO REF CAL de `w` cae en la
    # semana ISO del hueco y liberarlo es lo unico que desbloquea C5 (tope de dias/semana del
    # pool). Ancla el fix: coger el REF CAL mas antiguo cronologicamente, aunque fuese de otra
    # semana, dejaba el movimiento inerte (0 de 808 intentos sobre el dataset real), porque
    # `Legal` topa por semana ISO, no por año.
    w = "12402433M"                                   # correturno real del dataset
    lun = date(2026, 3, 16)
    dias = [lun + timedelta(days=i) for i in range(5)]  # lun..vie, semana ISO 12
    sab = lun + timedelta(days=5)
    p = Plan()
    for d in dias[:4]:
        p.asignar(w, d, "VADN001", "test", "monta el tope de dias")
    p.asignar(w, dias[4], "REF CAL M", "test", "el unico comodin de la semana")

    ok, motivo = ley.puede(p, w, sab, "VADN001")
    assert not ok and "C5" in motivo, f"el montaje de juguete no bloquea como se esperaba: {motivo}"

    assert R.mov_canjear_refcal(datos, p, ley, dd, sab, "VADN001"), \
        "mov.4 deberia canjear el REF CAL de la misma semana y cubrir el sabado"
    assert p.turno_de(w, sab) == "VADN001"
    assert p.turno_de(w, dias[4]) is None and not p.cedido(w, dias[4]), \
        "el REF CAL canjeado debe quedar simplemente liberado, no cedido"
    assert ley.verificar(p) == [], ley.verificar(p)

    # (c) reparar() no agota las 10 vueltas cuando ya no queda nada que mejorar: relanzarlo sobre
    # el plan que este mismo test acaba de dejar en su punto fijo debe pararse en la PRIMERA
    # vuelta (4 llamadas a huecos_reales: inicial, la del bucle, la de "ahora" y la del return).
    # Es la comprobacion que habria pillado el bug del criterio de parada: con el contador de
    # exitos en vez del recuento de huecos, 8 de las 10 vueltas eran ruido identico y el bucle
    # jamas convergia.
    llamadas = 0
    original = R.huecos_reales

    def _contado(datos, plan):
        nonlocal llamadas
        llamadas += 1
        return original(datos, plan)

    R.huecos_reales = _contado
    try:
        extra = reparar(datos, sin, ley, dd)
    finally:
        R.huecos_reales = original
    assert extra == 0, f"relanzar reparar() sobre un plan ya reparado no deberia cerrar nada: {extra}"
    assert llamadas == 4, (
        f"reparar() debio pararse en la primera vuelta (4 llamadas a huecos_reales); "
        f"hizo {llamadas} -> no esta convergiendo por huecos")


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)

    sin = construir(datos, con_reparacion=False)
    _, _, pct_sin = cobertura(datos, sin)
    huecos_antes = len(huecos_reales(datos, sin))

    cerrados = reparar(datos, sin, ley, dd)
    _, _, pct_con = cobertura(datos, sin)
    huecos_despues = len(huecos_reales(datos, sin))

    assert cerrados >= 0
    assert huecos_despues <= huecos_antes, "la reparacion ha ABIERTO huecos"
    assert pct_con >= pct_sin, f"la reparacion ha empeorado: {pct_sin} -> {pct_con}"
    assert ley.verificar(sin) == [], ley.verificar(sin)[:10]

    _step4b(datos, ley, dd, sin)

    # Determinismo
    a = construir(datos, con_reparacion=True)
    b = construir(datos, con_reparacion=True)
    assert a.asignaciones() == b.asignaciones(), "la reparacion no es determinista"

    # El movimiento 5 existe y esta el ULTIMO: es el mas invasivo.
    from reparacion import MOVIMIENTOS, mov_rehacer_semana
    assert MOVIMIENTOS[-1] is mov_rehacer_semana, "el mov.5 debe ir el ultimo"
    assert len(MOVIMIENTOS) == 5

    # Con los cinco movimientos la cobertura llega al liston
    from generar_anual import construir as _c
    final = _c(datos, con_reparacion=True)
    _, _, pct_final = cobertura(datos, final)
    assert pct_final >= 99.0, f"cobertura {pct_final:.2f} % < 99 %"
    assert ley.verificar(final) == [], ley.verificar(final)[:10]

    print(f"OK  reparacion · {huecos_antes} -> {huecos_despues} huecos "
          f"({cerrados} cerrados) · cobertura {pct_sin:.2f} -> {pct_con:.2f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

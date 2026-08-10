#!/usr/bin/env python3
"""Paso 2: las lineas criticas se cubren antes que nada.

El veto semanal es de la ADOPCION de una fila entera (ronda de correccion 1, Hallazgo 1), no de
cualquier cobertura: un cubridor puede tapar varios dias sueltos -de la misma linea o de otra- en
la misma semana y seguir con su propia rotacion el resto de dias. Solo quien ADOPTA una fila
-porque al titular le falta ENTERA- se queda sin nada mas esa semana y hereda los descansos de la
fila (Plan.ceder). Y H, con demanda 2, se cubre contando cuantas plazas faltan de verdad -titulares
presentes mas lo ya cubierto-, no si hay algun titular (Hallazgo 2)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana, turno_prescrito
from cargar_datos import cargar
from criticos import cubrir, lineas_criticas, principales
from legal import Legal
from libranzas import repartir
from plan import Plan


def main() -> int:
    datos = cargar()
    ley = Legal(datos)

    criticas = lineas_criticas(datos)
    assert set(criticas) >= {"VADU47127", "VADN051", "VADN052", "VADP003", "H"}, criticas
    # ordenadas de mas critica a menos: VADU47127 es prioridad 4
    assert criticas[0] == "VADU47127", criticas

    orden = principales(datos)
    assert orden["VADU47127"], "VADU47127 debe tener cubridor designado"

    p = Plan()
    repartir(datos, p)
    cubrir(datos, p, ley)

    dec = [d for d in p.libro if d.paso == "criticos"]
    asignados = [d for d in dec if d.turno is not None]
    assert asignados, "el paso 2 no ha asignado nada"

    # Todo lo asignado (y lo cedido) es legal
    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    # (i) Sin veto por "tocar" una linea: un cubridor SI puede cubrir varios dias SUELTOS de la
    # MISMA linea en la misma semana -no es una adopcion de fila, son turnos normales-.
    sueltas = [d for d in asignados if d.regla.startswith("cobertura critica suelta")]
    assert sueltas, "no hay coberturas sueltas (dia a dia) que anclar"
    por_w_sem_linea: dict[tuple[str, tuple[int, int], str], list] = {}
    for d in sueltas:
        por_w_sem_linea.setdefault((d.trabajador, semana(d.fecha), d.turno), []).append(d.fecha)
    varios_dias_suelto = {k: v for k, v in por_w_sem_linea.items() if len(v) >= 2}
    assert varios_dias_suelto, \
        "ningun cubridor cubre 2+ dias sueltos de la misma linea en la misma semana"

    # (ii) Adopcion de fila entera: quien adopta hace SOLO los dias que le faltan al titular y
    # CEDE el resto de la semana -hereda el descanso de la fila-. "Nada mas esa semana" es sobre
    # la OCUPACION del dia (trabaja la plaza adoptada o descansa), no sobre que paso lo decidiera:
    # si el paso 1 ya le habia dado ese dia libre por su propio exceso, vale igual -sigue sin
    # trabajar nada mas-, y el paso 2 no lo vuelve a tocar (evita reescribir una decision previa).
    adopciones = [d for d in asignados if d.regla.startswith("adopta plaza")]
    assert adopciones, "ninguna fila se ha adoptado entera"
    semanas_adoptadas = {(d.trabajador, semana(d.fecha)) for d in adopciones}
    for w, sem in semanas_adoptadas:
        dias_sem = [f for f in datos.fechas if semana(f) == sem]
        assert dias_sem, f"semana {sem} fuera del horizonte"
        dias_trabajo = {d.fecha for d in adopciones if d.trabajador == w and semana(d.fecha) == sem}
        for f in dias_sem:
            if f in dias_trabajo:
                assert p.turno_de(w, f) is not None and not p.cedido(w, f), \
                    f"{w} semana {sem}: {f} deberia trabajar la plaza que adopto"
            else:
                assert p.ocupado(w, f) and p.turno_de(w, f) is None, \
                    f"{w} semana {sem}: {f} deberia estar cedido (nada mas esa semana)"

    # (iii) H (dem=2) alcanza sus 2 plazas via cubridores designados los dias que hacen falta de
    # verdad -titulares presentes + cubridores asignados = dem-, no solo "hay algun titular".
    h_asignados = [d for d in asignados if d.turno == "H"]
    assert h_asignados, "H no ha recibido ninguna cobertura de cubridor designado"
    dias_h = sorted({d.fecha for d in h_asignados})
    for f in dias_h:
        titulares = sum(1 for w in datos.trabajadores
                         if turno_prescrito(datos, w, f) == "H"
                         and datos.disponible(w, f) and not p.cedido(w, f))
        cubridores_dia = sum(1 for d in h_asignados if d.fecha == f)
        assert titulares + cubridores_dia == datos.turnos["H"].dem, (
            f"H el {f}: titulares={titulares} + cubridores={cubridores_dia} "
            f"!= dem={datos.turnos['H'].dem}"
        )

    # Determinismo
    p2 = Plan()
    repartir(datos, p2)
    cubrir(datos, p2, ley)
    a = [(d.trabajador, d.fecha, d.turno, d.paso, d.liberado) for d in p.libro if d.paso == "criticos"]
    b = [(d.trabajador, d.fecha, d.turno, d.paso, d.liberado) for d in p2.libro if d.paso == "criticos"]
    assert a == b, "el paso 2 no es determinista"

    cesiones = [d for d in dec if d.turno is None]
    print(f"OK  criticos · {len(asignados)} coberturas · {len(cesiones)} cesiones heredadas de "
          f"adopcion · {len(criticas)} lineas criticas · {len(p.huecos)} huecos criticos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
cpsat.py — Herramientas comunes para los modelos CP-SAT de cualquier zona.

  * lexicografico — resuelve un objetivo por NIVELES: cada nivel se clava como restricción antes
                    del siguiente y se siembra con la solución del anterior. Así un nivel no puede
                    comprarle nada al de encima: la equidad no le quita ni una plaza a la cobertura.
  * nivel         — prepara un nivel para `lexicografico`
  * reparto       — términos que miden lo DESIGUAL que es un reparto entre varias personas
"""
from __future__ import annotations

from datetime import datetime

from ortools.sat.python import cp_model

# Qué se siembra entre niveles: "completa" = decisión + auxiliares de los niveles ya clavados
# (ver `sembrar`); "decision" = solo las variables de decisión.
SIEMBRA = "completa"


def optimizar(modelo, expresion, maximizar, segundos, hilos, log, etiqueta):
    """Resuelve UN nivel. Devuelve (valor, solver), o (None, None) si no hay solución.
    Con `segundos = None` no se le pone tope: se le deja demostrar el óptimo."""
    if maximizar:
        modelo.Maximize(expresion)
    else:
        modelo.Minimize(expresion)
    solver = cp_model.CpSolver()
    if segundos is not None:
        solver.parameters.max_time_in_seconds = segundos
    solver.parameters.num_search_workers = hilos
    solver.parameters.log_search_progress = log
    estado = solver.Solve(modelo)
    if estado not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"  nivel {etiqueta}: {solver.StatusName(estado)} ({solver.WallTime():.0f}s)"
              f"  [{datetime.now():%H:%M}]", flush=True)
        return None, None
    valor = int(round(solver.ObjectiveValue()))
    print(f"  nivel {etiqueta}: {valor}  ({solver.StatusName(estado)}, {solver.WallTime():.0f}s)"
          f"  [{datetime.now():%H:%M}]", flush=True)
    return valor, solver


def sembrar(modelo, solucion, hasta=None):
    """Arranca el siguiente nivel desde la solución del anterior.

    No es un truco de rendimiento: con los niveles anteriores ya clavados, encontrar CUALQUIER
    solución válida desde cero es durísimo, y la del nivel anterior ya está dentro de la región.

    Se siembran las variables del modelo por orden de creación hasta `hasta`: las de decisión y
    las auxiliares de los niveles YA clavados, que con muchos niveles el solver no conseguía
    deducir solo. Las del nivel que se optimiza y las de los siguientes NO: en la solución
    anterior valen cualquier cosa, porque aún no contaban, y sembrarlas arranca el nivel desde su
    peor punto.
    """
    if solucion is None:
        return
    modelo.ClearHints()
    n = min(len(modelo.Proto().variables), len(solucion.ResponseProto().solution))
    if hasta is not None:
        n = min(n, hasta)
    for i in range(n):
        var = modelo.GetIntVarFromProtoIndex(i)
        modelo.AddHint(var, solucion.Value(var))


def nivel(modelo, etiqueta, construir, maximizar):
    """Un nivel del objetivo lexicográfico: (etiqueta, expresión, maximizar, primera variable
    propia). Se anota cuántas variables tenía el modelo ANTES de construir la expresión, que es
    lo que separa las auxiliares de este nivel de las anteriores (ver `sembrar`)."""
    inicio = len(modelo.Proto().variables)
    return etiqueta, construir(), maximizar, inicio


def lexicografico(modelo, niveles, segundos, hilos, log, sangria="", decision=()):
    """Resuelve los niveles en orden: cada uno se clava como restricción antes del siguiente y se
    siembra con la solución del anterior. Si un nivel no encuentra solución no se clava y se sigue
    con el siguiente. Devuelve la última solución, o None si no hubo ninguna. `decision` son las
    variables de decisión, para la siembra "decision"."""
    sol = None
    for etiqueta, objetivo, maximizar, inicio in niveles:
        if objetivo is None:
            continue
        if SIEMBRA == "decision" and sol is not None:
            modelo.ClearHints()
            for var in decision:
                modelo.AddHint(var, sol.Value(var))
        else:
            sembrar(modelo, sol, hasta=inicio)
        valor, nueva = optimizar(modelo, objetivo, maximizar, segundos, hilos, log, sangria + etiqueta)
        if valor is None:
            continue
        modelo.Add(objetivo >= valor if maximizar else objetivo <= valor)
        sol = nueva
    return sol


def reparto(modelo, cuentas, techo, etiqueta):
    """Términos que miden lo DESIGUAL que es un reparto, sobre contadores del modelo.

    Dos medidas sumadas, porque cada una sola es ciega a algo:
      * la desviación de cada uno respecto a la media, escalada por N para no usar racionales
        (N*n - S, con S la suma, que es exactamente N*|n - media|);
      * el RANGO, el mayor menos el menor, también escalado por N.

    Con la desviación sola da igual dos personas muy por encima que cuatro un poco por encima.
    Con el rango solo, solo cuentan los dos extremos.
    """
    n = len(cuentas)
    if n < 2:
        return []
    total = sum(cuentas)
    terminos = []
    for i, cuenta in enumerate(cuentas):
        desviacion = modelo.NewIntVar(0, n * techo, f"dev_{etiqueta}_{i}")
        modelo.Add(desviacion >= n * cuenta - total)
        modelo.Add(desviacion >= total - n * cuenta)
        terminos.append(desviacion)
    alto = modelo.NewIntVar(0, techo, f"max_{etiqueta}")
    bajo = modelo.NewIntVar(0, techo, f"min_{etiqueta}")
    modelo.AddMaxEquality(alto, cuentas)
    modelo.AddMinEquality(bajo, cuentas)
    terminos.append(n * (alto - bajo))
    return terminos

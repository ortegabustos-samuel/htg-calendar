# Cobertura de líneas críticas — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que las líneas críticas se cubran cuando hay alguien capaz de hacerlo, cambiando la adopción de plaza para que se dispare por lo que el titular deja sin hacer, y convirtiendo el orden de cubridores en una restricción dura.

**Architecture:** Se extrae a una función compartida el cálculo de qué días de una línea crítica se queda sin titular —hoy vive enterrado dentro de `reserva_cubridores`— y se expresa como una lista de ausencias con dos casos: fila entera (adopción, se heredan los descansos) o parcial (turnos normales). Los cuatro sitios que hoy replican la regla vieja pasan a consultarla. Encima de eso se añade una restricción que obliga al cubridor principal a cubrir salvo vacaciones o compromiso con una línea de prioridad igual o superior.

**Tech Stack:** Python 3.12, OR-Tools CP-SAT, `openpyxl`. Entorno conda `ortools_env` en `/home/samu/anaconda3/envs/ortools_env/bin/python3`.

## Global Constraints

- **No hay pytest ni framework de tests.** Los tests son scripts con `assert` y una función `main()`, ejecutables con `python3 tests/<fichero>.py`. No instalar dependencias nuevas.
- **Usar siempre el intérprete del entorno:** `/home/samu/anaconda3/envs/ortools_env/bin/python3`. Activar el conda con `conda activate` falla en esta máquina (`~/miniconda3` no existe) y produce un aborto de protobuf con código 134.
- **`data/` está en `.gitignore`** y contiene DNI reales. Nunca añadir ficheros de `data/` a un commit ni copiar su contenido a un fichero versionado.
- **La rotación de un trabajador es** `filas[(datos.offsets[w] + (f - ancla).days // 7) % len(filas)][DIAS[f.weekday()]]`, con `ancla = datos.inicio - timedelta(days=datos.inicio.weekday())`. Es la única forma válida de calcularla; `datos.offsets` es la fuente única.
- **Cifras de referencia de la corrida actual** (commit `47f2230`), para comparar al final: 27/27 ventanas, cobertura 18264/18295 (99,8 %), 31 huecos de los que 11 críticos. Horas: fijos μ1773 σ6,0 · patrones μ1774 σ6,8 · correturnos μ1771 σ3,3 · mixtos μ1733 σ23,4, nadie por encima de 1776.
- **La corrida anual tarda ~30 minutos.** Lanzarla en segundo plano, nunca en primer plano con timeout.

---

### Task 1: Extraer el cálculo de ausencias críticas

Hoy `reserva_cubridores` calcula, en un bucle interno, qué días de cada línea crítica se quedan sin su titular. Ese mismo cálculo lo necesitan las dos reglas nuevas. Se extrae sin cambiar nada de comportamiento: al final de la tarea la reserva debe dar exactamente los mismos números.

**Files:**
- Modify: `src/modelo.py` (añadir `AusenciaCritica` y `ausencias_criticas` junto a `reserva_cubridores`, hacia la línea 1292; reescribir el cuerpo de `reserva_cubridores` para consumirla)
- Test: `tests/test_ausencias.py` (crear)

**Interfaces:**
- Consumes: `Datos` de `cargar_datos`; `calendario_cesiones(datos) -> set[tuple[str, int]]` ya existente.
- Produces:
  - `AusenciaCritica`, dataclass congelada con campos `linea: str`, `titular: str`, `semana: tuple[int, int]`, `prescritos: frozenset[date]`, `faltan: frozenset[date]`, `libres: frozenset[date]`, y propiedad `entera: bool`.
  - `ausencias_criticas(datos: Datos, cesiones: set[tuple[str, int]]) -> list[AusenciaCritica]`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_ausencias.py`:

```python
#!/usr/bin/env python3
"""Comprueba ausencias_criticas contra los casos reales de 2026 que motivaron el cambio."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from modelo import ausencias_criticas, calendario_cesiones


def main() -> int:
    datos = cargar()
    aus = ausencias_criticas(datos, calendario_cesiones(datos))
    por_clave = {(a.linea, a.titular, a.semana): a for a in aus}

    # Semana del 3 al 9 de agosto: 12427762B esta de vacaciones del 1 al 15 y le toca
    # la fila 0 de UVI_PRIV, que prescribe SOLO miercoles y jueves. Faltan los dos ->
    # es la fila entera -> ADOPCION.
    a = next(x for x in aus if x.linea == "VADP003" and x.titular == "12427762B"
             and date(2026, 8, 6) in x.faltan)
    assert a.prescritos == frozenset({date(2026, 8, 5), date(2026, 8, 6)}), a.prescritos
    assert a.faltan == a.prescritos, a.faltan
    assert a.entera is True
    assert date(2026, 8, 3) in a.libres and date(2026, 8, 7) in a.libres, a.libres

    # Semana del 12 al 18 de octubre: 72918050T esta de vacaciones HASTA EL 15 y le toca
    # la fila 1, que prescribe lunes, martes, viernes, sabado y domingo. Solo faltan el
    # lunes 12 y el martes 13 -> PARCIAL.
    a = next(x for x in aus if x.linea == "VADP003" and x.titular == "72918050T"
             and date(2026, 10, 12) in x.faltan)
    assert a.faltan == frozenset({date(2026, 10, 12), date(2026, 10, 13)}), a.faltan
    assert len(a.prescritos) == 5, a.prescritos
    assert a.entera is False

    # Invariantes generales
    for x in aus:
        assert x.faltan, "una ausencia sin dias que falten no deberia existir"
        assert x.faltan <= x.prescritos
        assert not (x.libres & x.prescritos), "libres y prescritos son disjuntos"
        assert datos.turnos[x.linea].prioridad >= 2

    print(f"OK  {len(aus)} ausencias criticas · "
          f"{sum(1 for x in aus if x.entera)} enteras · "
          f"{sum(1 for x in aus if not x.entera)} parciales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_ausencias.py
```

Esperado: `ImportError: cannot import name 'ausencias_criticas' from 'modelo'`.

- [ ] **Step 3: Implementar `AusenciaCritica` y `ausencias_criticas`**

En `src/modelo.py`, justo antes de `def reserva_cubridores(`:

```python
@dataclass(frozen=True)
class AusenciaCritica:
    """Días de una línea crítica que su titular no puede hacer en una semana ISO.

    `prescritos` son los días que la fila de la rotación le asigna esa semana; `faltan`, el
    subconjunto que no puede hacer (vacaciones o bloque cedido); `libres`, los días de la semana
    que su fila deja LIBRE — el descanso que viene con la plaza.

    La distinción que gobierna las dos reglas: si faltan TODOS los días prescritos, quien cubra
    ADOPTA la plaza y hereda `libres`; si falta solo parte, son turnos normales."""
    linea: str
    titular: str
    semana: tuple[int, int]
    prescritos: frozenset[date]
    faltan: frozenset[date]
    libres: frozenset[date]

    @property
    def entera(self) -> bool:
        return bool(self.faltan) and self.faltan == self.prescritos


def ausencias_criticas(datos: Datos, cesiones: set[tuple[str, int]]) -> list[AusenciaCritica]:
    """Recorre el año y devuelve, por titular y semana ISO, qué días de su línea crítica se
    quedan sin él. Es dato conocido antes de resolver: las vacaciones vienen en trabajadores.csv
    y las cesiones las acaba de decidir `calendario_cesiones`.

    Fuente ÚNICA de la regla de adopción: la consultan la restricción del modelo, el relleno de
    refuerzos y las dos pasadas de pulido, que antes la replicaban cada una por su cuenta."""
    ancla = datos.inicio - timedelta(days=datos.inicio.weekday())
    fechas = rango_fechas(ancla, datos.fin)
    criticas = {s for s, t in datos.turnos.items() if t.prioridad >= 2}
    if not criticas:
        return []

    # (titular, semana, linea) -> {"prescritos": set, "faltan": set, "libres": set}
    acum: dict[tuple[str, tuple[int, int], str], dict[str, set]] = {}
    for p, filas in datos.patrones.items():
        T = len(filas)
        for w, t in datos.trabajadores.items():
            if t.patron != p:
                continue
            off = datos.offsets.get(w, 0)
            for f in fechas:
                fila = filas[(off + (f - ancla).days // 7) % T]
                s = fila[DIAS[f.weekday()]]
                if s not in criticas:
                    continue
                if not datos.opera(s, f):
                    continue
                clave = (w, semana(f), s)
                reg = acum.setdefault(clave, {"prescritos": set(), "faltan": set(), "libres": set()})
                reg["prescritos"].add(f)
                cede = (w, (f - ancla).days // 14) in cesiones
                if not datos.disponible(w, f) or cede:
                    reg["faltan"].add(f)

    # Los LIBRE de la fila: días de esa semana ISO en que la rotación no le asigna nada.
    for (w, sem, s), reg in acum.items():
        alguno = min(reg["prescritos"])
        lunes = alguno - timedelta(days=alguno.weekday())
        filas = datos.patrones[datos.trabajadores[w].patron]
        T = len(filas)
        off = datos.offsets.get(w, 0)
        for i in range(7):
            f = lunes + timedelta(days=i)
            if f < datos.inicio or f > datos.fin:
                continue
            fila = filas[(off + (f - ancla).days // 7) % T]
            if fila[DIAS[f.weekday()]] == LIBRE:
                reg["libres"].add(f)

    return [AusenciaCritica(linea=s, titular=w, semana=sem,
                            prescritos=frozenset(reg["prescritos"]),
                            faltan=frozenset(reg["faltan"]),
                            libres=frozenset(reg["libres"]))
            for (w, sem, s), reg in sorted(acum.items()) if reg["faltan"]]
```

Añadir `from dataclasses import dataclass` a los imports de `modelo.py` si no está.

- [ ] **Step 4: Ejecutar el test y ver que pasa**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_ausencias.py
```

Esperado: `OK  N ausencias criticas · X enteras · Y parciales`.

- [ ] **Step 5: Test de caracterización de la reserva ANTES de refactorizar**

Crear `tests/test_reserva.py`. Estos son los valores que produce el código actual; el refactor no puede cambiarlos:

```python
#!/usr/bin/env python3
"""La reserva de Nivel 0 no puede cambiar al refactorizar reserva_cubridores."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from modelo import calendario_cesiones, reserva_cubridores

ESPERADO = {"Y0945237C": 495.0, "71174480Z": 506.0, "01860358A": 194.3,
            "18029935M": 194.3, "12427762B": 137.1, "72918050T": 182.9}


def main() -> int:
    datos = cargar()
    res = reserva_cubridores(datos, calendario_cesiones(datos))
    obtenido = {w: round(max(c.values()), 1) for w, c in res.items()}
    assert obtenido == ESPERADO, f"\n esperado {ESPERADO}\n obtenido {obtenido}"
    print(f"OK  reserva sin cambios para {len(obtenido)} cubridores")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Ejecutarlo y comprobar que **pasa con el código actual** antes de tocar `reserva_cubridores`:

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_reserva.py
```

- [ ] **Step 6: Reescribir `reserva_cubridores` para consumir `ausencias_criticas`**

Sustituir el bloque que calcula `prescrito` y el bucle `for s in sorted(criticas)` por:

```python
    faltan_por_linea: dict[str, list[date]] = defaultdict(list)
    for a in ausencias_criticas(datos, cesiones):
        faltan_por_linea[a.linea].extend(a.faltan)

    pendiente: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    total: dict[str, float] = defaultdict(float)
    for s in sorted(faltan_por_linea):
        cubridores = sorted(((c.v, w) for (w, ss), c in datos.capacidades.items()
                             if ss == s and c.v >= 1))
        if not cubridores:
            continue
        horas = datos.turnos[s].horas_consumo
        for f in sorted(faltan_por_linea[s]):
            # Se reparte entre los disponibles dando el día al que MENOS lleve acumulado, con el
            # orden `v` como desempate. Adjudicárselo siempre al primero daría una reserva irreal.
            libres = [(total[w], v, w) for v, w in cubridores if datos.disponible(w, f)]
            if libres:
                _, _, w = min(libres)
                pendiente[w][f] += horas
                total[w] += horas
```

Dejar intacto el bloque final que acumula hacia atrás.

- [ ] **Step 7: Ejecutar los dos tests**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_ausencias.py
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_reserva.py
```

Esperado: los dos OK. Si `test_reserva.py` falla, el refactor cambió el comportamiento y hay que arreglarlo, no actualizar los números esperados.

- [ ] **Step 8: Commit**

```bash
git add tests/ src/modelo.py
git commit -m "extrae ausencias_criticas: quien falta en cada linea critica y si es la fila entera"
```

---

### Task 2: Adopción de plaza en lugar de handover por contacto

**Files:**
- Modify: `src/modelo.py` (`Modelo.__init__` hacia la línea 291 para guardar las ausencias; `_handover_critico` en la 1060, que se renombra a `_adopcion_plaza`; la llamada en la 355)
- Test: `tests/test_adopcion.py` (crear)

**Interfaces:**
- Consumes: `ausencias_criticas` y `AusenciaCritica` de la Task 1.
- Produces: `Modelo._adopcion_plaza()`, y el atributo `self.adopciones: list[AusenciaCritica]` (solo las que tienen `entera == True`).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_adopcion.py`:

```python
#!/usr/bin/env python3
"""La adopcion se dispara por fila entera, no por tocar una linea critica."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from modelo import ausencias_criticas, calendario_cesiones


def main() -> int:
    datos = cargar()
    aus = ausencias_criticas(datos, calendario_cesiones(datos))
    enteras = [a for a in aus if a.entera]
    parciales = [a for a in aus if not a.entera]

    # Las dos deben existir en estos datos, o el cambio no se estaria probando.
    assert enteras, "no hay ninguna adopcion: el test no prueba nada"
    assert parciales, "no hay ninguna cobertura parcial: el test no prueba nada"

    # Agosto: fila entera -> adopcion, y arrastra descansos.
    ago = next(a for a in enteras if a.linea == "VADP003" and date(2026, 8, 6) in a.faltan)
    assert ago.libres, "una adopcion sin descansos que heredar no tiene sentido"

    # Octubre: parcial -> NO arrastra nada, se cubre como turno normal.
    oct_ = next(a for a in parciales if a.linea == "VADP003" and date(2026, 10, 12) in a.faltan)
    assert not oct_.entera

    # Los dias que faltan en una parcial son estrictamente menos que los prescritos.
    for a in parciales:
        assert len(a.faltan) < len(a.prescritos)

    print(f"OK  {len(enteras)} adopciones · {len(parciales)} coberturas parciales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutarlo**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_adopcion.py
```

Esperado: PASA ya, porque solo usa la Task 1. Sirve de red antes de tocar el modelo — si falla aquí, el problema está en la Task 1.

- [ ] **Step 3: Guardar las adopciones en el modelo**

En `Modelo.__init__`, después de `self.cesiones = cesiones` (línea ~291):

```python
        # Semanas en que un titular de línea crítica falta ENTERA: quien la cubra adopta la plaza
        # con sus descansos. Si falta solo parte, son turnos normales (ver AusenciaCritica).
        self.adopciones = [a for a in ausencias_criticas(datos, cesiones) if a.entera]
```

- [ ] **Step 4: Sustituir `_handover_critico` por `_adopcion_plaza`**

Reemplazar el cuerpo entero del método (líneas 1060-1099) por:

```python
    def _adopcion_plaza(self) -> None:
        """DURA: quien cubre una plaza crítica cuya fila falta ENTERA la hace completa y no hace
        nada más esa semana.

        Cubrir una plaza de noche o de localizado no es coger unos turnos sueltos: es asumir la
        plaza, y la plaza viene con sus DESCANSOS. Si el titular falta toda su fila, quien entre
        hereda también sus LIBRE — que quedan vacíos de verdad, también de otras líneas críticas.

        La regla NO se aplica cuando falta solo parte de la fila (o un día suelto): ahí son turnos
        normales, se cubren como cualquier otro y el cubridor sigue su propia rotación el resto de
        la semana. La versión anterior se disparaba por TOCAR una línea crítica, con lo que tapar
        un jueves suelto costaba la semana entera del cubridor y salía más caro que el hueco.

        C4 sigue garantizando el descanso INMEDIATO por su cuenta: estas líneas son 22:00→22:00,
        así que tras un turno el día siguiente es imposible. Lo que aporta esta regla es la
        protección frente a la carga ACUMULADA de una semana entera."""
        for a in self.adopciones:
            dias_sem = [f for f in self.fechas
                        if f not in self.cola and semana(f) == a.semana]
            if not dias_sem:
                continue
            hace = [self.x[(w, f, a.linea)] for w in self.datos.trabajadores
                    for f in a.faltan if (w, f, a.linea) in self.x]
            if not hace:
                continue
            for w in self.datos.trabajadores:
                mios = [self.x[(w, f, a.linea)] for f in a.faltan
                        if (w, f, a.linea) in self.x]
                if not mios:
                    continue
                adopta = self.m.new_bool_var(f"adopta_{w}_{a.linea}_{a.semana[0]}w{a.semana[1]}")
                # tocar un día de la plaza => adopta
                for var in mios:
                    self.m.add(var <= adopta)
                # adoptar => hacerla ENTERA
                for var in mios:
                    self.m.add(var >= adopta)
                # adoptar => nada más esa semana, ni siquiera otra línea crítica
                otras = [self.x[(w, f, s)] for f in dias_sem
                         for s in self.turnos_wd.get((w, f), [])
                         if (w, f, s) in self.x and not (s == a.linea and f in a.faltan)]
                if otras:
                    self.m.add(sum(otras) <= len(otras) * (1 - adopta))
```

- [ ] **Step 5: Actualizar la llamada**

En la línea ~355 cambiar:

```python
        self._handover_critico()                  # cubrir una crítica = adoptar la plaza y sus descansos
```

por:

```python
        self._adopcion_plaza()                    # fila entera = adoptar la plaza y sus descansos
```

Y en el comentario de cabecera del fichero (línea ~31) sustituir `_handover_critico` por `_adopcion_plaza`.

- [ ] **Step 6: Comprobar que el modelo se construye y una ventana resuelve**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 -c "
import sys; sys.path.insert(0,'src')
from cargar_datos import cargar
from modelo import resolver_anual
import modelo
modelo.DIAS_VENTANA = 14
d = cargar()
d2 = type(d)(**{**d.__dict__, 'config': d.config})
plan = resolver_anual(d, segundos=30, hilos=8)
print('celdas del plan:', len(plan))
"
```

Esperado: arranca, imprime el progreso de las ventanas y no lanza excepción. Esto tarda ~30 min; si solo se quiere comprobar que construye, interrumpir tras la primera ventana y verificar que dijo FEASIBLE u OPTIMAL, no INFEASIBLE.

- [ ] **Step 7: Commit**

```bash
git add src/modelo.py tests/test_adopcion.py
git commit -m "la adopcion de plaza se dispara por fila entera, no por tocar la linea"
```

---

### Task 3: El orden de cubridores como restricción dura

**Files:**
- Modify: `src/modelo.py` (nuevo método `_obligacion_principal`, llamado junto a `_adopcion_plaza` en la línea ~355)
- Test: `tests/test_principal.py` (crear)

**Interfaces:**
- Consumes: `self.adopciones` y `ausencias_criticas` de las tareas anteriores.
- Produces: `Modelo._obligacion_principal()`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_principal.py`:

```python
#!/usr/bin/env python3
"""El orden v de cubridores: quien es principal de cada linea critica."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from modelo import principales


def main() -> int:
    datos = cargar()
    p = principales(datos)

    # Los dos cubridores estan CRUZADOS a proposito: cada uno es principal de una linea
    # y suplente de la otra. Es exactamente lo que el plan actual no respeta.
    assert p["VADP003"][0] == "01860358A", p["VADP003"]
    assert p["VADU47127"][0] == "18029935M", p["VADU47127"]

    # El orden es completo y creciente en v.
    for linea, orden in p.items():
        assert orden, f"{linea} sin cubridores designados"
        vs = [datos.capacidades[(w, linea)].v for w in orden]
        assert vs == sorted(vs), f"{linea}: orden no creciente {vs}"

    print(f"OK  orden de cubridores para {len(p)} lineas: "
          + " · ".join(f"{k}->{v[0]}" for k, v in sorted(p.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_principal.py
```

Esperado: `ImportError: cannot import name 'principales' from 'modelo'`.

- [ ] **Step 3: Implementar `principales`**

En `src/modelo.py`, junto a `ausencias_criticas`:

```python
def principales(datos: Datos) -> dict[str, list[str]]:
    """{línea -> cubridores designados, del principal al último suplente}.

    El gestor designa un principal (v=1) porque considera que hace mejor esa línea, y suplentes
    (v=2, 3…) que solo deberían entrar si el principal no puede. Ojo: los cubridores pueden estar
    CRUZADOS —en estos datos cada uno es principal de una línea y suplente de la otra—, así que el
    orden es por línea, nunca por persona."""
    orden: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (w, s), cap in datos.capacidades.items():
        if cap.v >= 1:
            orden[s].append((cap.v, w))
    return {s: [w for _, w in sorted(pares)] for s, pares in orden.items()}
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_principal.py
```

Esperado: `OK  orden de cubridores para 5 lineas: H->... · VADN051->... · VADN052->... · VADP003->01860358A · VADU47127->18029935M`.

- [ ] **Step 5: Implementar la restricción**

Añadir a `Modelo`, después de `_adopcion_plaza`:

```python
    def _obligacion_principal(self) -> None:
        """DURA: el cubridor PRINCIPAL de una línea crítica la cubre los días que su titular falta,
        aunque tenga que dejar su propio turno —que queda como hueco y lo recoge un correturno—.
        Solo cuando el principal no puede entra el siguiente en el orden `v`.

        "No puede" son exactamente dos casos: estar de vacaciones (dato, `datos.disponible`) o
        estar ya haciendo otra línea crítica de prioridad IGUAL O SUPERIOR (variable). Sin la
        segunda condición se sacaría a alguien de una línea de prioridad 4 para taparle una de 3,
        al revés de lo que se quiere.

        NO hace falta escape por bloqueo legal: al ser dura, C4 propaga hacia atrás por sí sola y
        el modelo no puede asignarle al principal, el día anterior, nada incompatible con lo que
        está obligado a cubrir. Ese turno se libera y queda como hueco.

        Antes esto vivía en `_preferencia_cubridor` como término blando dentro de W3, sumado junto
        a la desviación de jornada —que se mide en minutos y es de otro orden de magnitud—, así que
        no decidía nunca: el plan ponía a un suplente a cubrir mientras el principal de esa misma
        línea se quedaba en su patrón."""
        orden = principales(self.datos)
        for a in ausencias_criticas(self.datos, self.cesiones):
            cubridores = orden.get(a.linea)
            if not cubridores:
                continue
            prio = self.datos.turnos[a.linea].prioridad
            for f in sorted(a.faltan):
                if f in self.cola:
                    continue
                for w in cubridores:
                    if not self.datos.disponible(w, f):
                        continue              # de vacaciones: le toca al siguiente del orden
                    var = self.x.get((w, f, a.linea))
                    if var is None:
                        continue              # sin capacidad ese día: le toca al siguiente
                    # Escape: ese día ya hace otra crítica de prioridad >= la de esta línea.
                    ocupado = [self.x[(w, f, s)] for s in self.turnos_wd.get((w, f), [])
                               if s != a.linea and (w, f, s) in self.x
                               and self.datos.turnos[s].prioridad >= prio]
                    self.m.add(var + sum(ocupado) >= 1)
                    break                     # la obligación es del PRIMERO que puede
```

- [ ] **Step 6: Llamarla**

En la línea ~355, justo después de `self._adopcion_plaza()`:

```python
        self._obligacion_principal()              # el principal cubre salvo vacaciones o crítica mayor
```

- [ ] **Step 7: Comprobar que la primera ventana sigue siendo factible**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 src/generar_anual.py --segundos 30 2>&1 | head -20
```

Esperado: la primera ventana dice FEASIBLE u OPTIMAL. **Si dice INFEASIBLE, parar aquí**: es el riesgo que la especificación anticipa. Anotar qué ventana y qué fechas, y consultar antes de añadir escapes.

- [ ] **Step 8: Commit**

```bash
git add src/modelo.py tests/test_principal.py
git commit -m "el cubridor principal cubre su linea critica salvo vacaciones o critica mayor"
```

---

### Task 4: Propagar la adopción al relleno y al pulido

Tres sitios fuera del modelo replican la regla vieja («esta semana toca una crítica, luego adoptó la plaza entera»). Si no se actualizan, deshacen el arreglo en el post-proceso: seguirían tratando como intocable la semana de un cubridor que solo tapó un día suelto.

**Files:**
- Modify: `src/modelo.py` (`rellenar_refuerzos`, bloque `sem_bloqueada` hacia la línea 1707)
- Modify: `src/pulido.py:519` (`aprovechar`, exclusión de las plazas de localizado) y `src/pulido.py:729-732` (`canjear`, salto por semana con crítica)
- Test: `tests/test_adopcion.py` (ampliar)

**Interfaces:**
- Consumes: `ausencias_criticas`, `calendario_cesiones` de las tareas anteriores.
- Produces: `semanas_adoptadas(datos) -> set[tuple[str, tuple[int, int]]]`, el conjunto de `(línea, semana ISO)` en que hay adopción.

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/test_adopcion.py`, antes de `print(...)` en `main()`:

```python
    from modelo import semanas_adoptadas
    sa = semanas_adoptadas(datos)
    # La semana del 3 al 9 de agosto de VADP003 es adopcion; la del 12 al 18 de octubre no.
    from modelo import semana
    assert ("VADP003", semana(date(2026, 8, 6))) in sa
    assert ("VADP003", semana(date(2026, 10, 12))) not in sa
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_adopcion.py
```

Esperado: `ImportError: cannot import name 'semanas_adoptadas' from 'modelo'`.

- [ ] **Step 3: Implementar `semanas_adoptadas`**

En `src/modelo.py`, junto a `principales`:

```python
def semanas_adoptadas(datos: Datos) -> set[tuple[str, tuple[int, int]]]:
    """{(línea, semana ISO)} en que la fila del titular falta ENTERA y por tanto quien la cubra
    adopta la plaza con sus descansos. Lo consultan `rellenar_refuerzos` y las dos pasadas de
    `pulido`, que corren fuera del modelo y no lo sabrían por su cuenta."""
    return {(a.linea, a.semana)
            for a in ausencias_criticas(datos, calendario_cesiones(datos)) if a.entera}
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_adopcion.py
```

Esperado: OK.

- [ ] **Step 5: Actualizar `rellenar_refuerzos`**

Sustituir en `src/modelo.py` (hacia la línea 1705):

```python
    # Semanas en que alguien cubre una línea de noche o UVI: ahí adoptó la plaza entera y sus
    # descansos (ver Modelo._handover_critico), así que el relleno tampoco puede meterle un
    # refuerzo. Esta pasada corre fuera del modelo y no lo sabría por su cuenta.
    lineas_criticas = {s for p_ in (_patrones_noche(datos) | _patrones_uvi(datos))
                       for fila in datos.patrones.get(p_, []) for s in fila.values()
                       if s and s != LIBRE and s in datos.turnos}
    sem_bloqueada = {(w, semana(f)) for (w, f), s in plan.items() if s in lineas_criticas}
```

por:

```python
    # Semanas en que alguien ADOPTÓ una plaza crítica: se llevó la plaza con sus descansos, así que
    # el relleno no puede meterle un refuerzo. Solo cuenta la adopción de fila entera; si tapó días
    # sueltos, su semana es normal y sí admite relleno. Esta pasada corre fuera del modelo.
    adoptadas = semanas_adoptadas(datos)
    sem_bloqueada = {(w, semana(f)) for (w, f), s in plan.items()
                     if (s, semana(f)) in adoptadas}
```

- [ ] **Step 6: Actualizar `pulido.aprovechar`**

En `src/pulido.py:517-519` sustituir:

```python
        # Las plazas de LOCALIZADO no se tapan con un refuerzo suelto: quien las coge asume la
        # semana entera con sus descansos (modelo._handover_critico), y aquí solo se cambia un día.
        huecos = [s for s in _huecos_dia(datos, ocupado, f) if s not in loc]
```

por:

```python
        # Una plaza de localizado solo es intocable la semana en que se ADOPTA entera: entonces
        # quien la coge se lleva sus descansos y aquí solo se cambia un día. Si el hueco es de una
        # semana de cobertura parcial, es un turno normal y sí se puede tapar.
        huecos = [s for s in _huecos_dia(datos, ocupado, f)
                  if s not in loc or (s, semana(f)) not in adoptadas]
```

Y al principio de `aprovechar`, junto a las demás precomputaciones, añadir:

```python
    adoptadas = semanas_adoptadas(datos)
```

Añadir `semanas_adoptadas` a la lista de importaciones desde `modelo` en la línea 37.

- [ ] **Step 7: Actualizar `pulido.canjear`**

En `src/pulido.py:728-732` sustituir:

```python
            if actual is None and any(datos.turnos[x].prioridad >= 2
                                      for x in sem_turnos[(w, sm)]):
                continue    # esa semana cubre una línea CRÍTICA: asumió la plaza entera con sus
                            # descansos (modelo._handover_critico), y sus días libres son parte de
                            # ella. Cambiarle un refuerzo por otro turno del mismo día sí vale.
```

por:

```python
            if actual is None and any((x, sm) in adoptadas for x in sem_turnos[(w, sm)]):
                continue    # esa semana ADOPTÓ una plaza crítica entera: se llevó la plaza con sus
                            # descansos y sus días libres son parte de ella. Si solo tapó días
                            # sueltos, su semana es normal. Cambiar un refuerzo del mismo día sí vale.
```

Y al principio de `canjear` añadir:

```python
    adoptadas = semanas_adoptadas(datos)
```

- [ ] **Step 8: Comprobar que todo importa y compila**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 -m py_compile src/*.py && echo "compila"
/home/samu/anaconda3/envs/ortools_env/bin/python3 -c "
import sys; sys.path.insert(0,'src'); import pulido, modelo, salida
print('importa todo')"
for t in tests/test_*.py; do /home/samu/anaconda3/envs/ortools_env/bin/python3 "$t" || exit 1; done
```

Esperado: `compila`, `importa todo`, y los cuatro tests en OK.

- [ ] **Step 9: Commit**

```bash
git add src/modelo.py src/pulido.py tests/test_adopcion.py
git commit -m "el relleno y el pulido distinguen adopcion de cobertura parcial"
```

---

### Task 5: Corrida anual y criterios de aceptación

**Files:**
- Modify: `CLAUDE.md` (corregir la afirmación de que el objetivo es lexicográfico; describir la adopción de plaza y la obligación del principal)
- Modify: `src/modelo.py:1202` (borrar el encabezado huérfano `# -- Resolución LEXICOGRÁFICA`)
- Test: `tests/test_aceptacion.py` (crear)

**Interfaces:**
- Consumes: `data/output/informe_cobertura.csv` y `data/output/metricas_trabajadores.csv`, que genera `salida.generar_anual`.
- Produces: nada que consuman otras tareas.

- [ ] **Step 1: Guardar la salida actual para poder comparar**

```bash
mkdir -p /tmp/claude-1000/-home-samu-Documents-Universidad-HT-GROUP/3642a41d-dae3-4ac6-a028-d5addf322650/scratchpad/antes
cp data/output/*.csv data/output/*.xlsx \
   /tmp/claude-1000/-home-samu-Documents-Universidad-HT-GROUP/3642a41d-dae3-4ac6-a028-d5addf322650/scratchpad/antes/
```

- [ ] **Step 2: Lanzar la corrida anual en segundo plano**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 src/generar_anual.py --segundos 60 --hilos 16
```

Lanzarlo con `run_in_background: true`. Tarda ~30 minutos. **No** ponerle timeout en primer plano.

- [ ] **Step 3: Escribir el test de aceptación mientras corre**

Crear `tests/test_aceptacion.py`:

```python
#!/usr/bin/env python3
"""Criterios de aceptacion sobre la salida de una corrida anual completa."""
import csv
import statistics as st
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "data" / "output"

# Cifras de la corrida de referencia (commit 47f2230), antes del cambio.
HUECOS_ANTES, CRITICOS_ANTES = 31, 11


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

    # Criterio 2 (caso concreto): desaparecen los huecos de VADP003 del 5 y 6 de agosto.
    ago = [h for h in criticos
           if h["id_turno"] == "VADP003" and h["fecha"] in ("05/08/2026", "06/08/2026")]
    if ago:
        fallos.append(f"siguen los huecos de agosto: {[h['fecha'] for h in ago]}")

    # Criterio 3: la cobertura global no baja.
    if len(huecos) > HUECOS_ANTES:
        fallos.append(f"huecos totales {len(huecos)} > {HUECOS_ANTES}")

    # Criterio 5: nadie por encima de 1776 y la dispersion por tipo no empeora.
    if any(m["h"] > 1776.5 for m in metricas):
        peor = max(metricas, key=lambda m: m["h"])
        fallos.append(f"{peor['id_trab']} pasa de 1776: {peor['h']}")
    sigma_antes = {"fijo": 6.0, "patron": 6.8, "correturno": 3.3, "mixto": 23.4}
    for tipo, antes in sigma_antes.items():
        g = [m["h"] for m in metricas if m["tipo"] == tipo]
        if g and st.pstdev(g) > antes * 1.5:
            fallos.append(f"sigma de {tipo}: {st.pstdev(g):.1f} vs {antes} antes")

    print(f"huecos {len(huecos)} (antes {HUECOS_ANTES}) · "
          f"criticos {len(criticos)} (antes {CRITICOS_ANTES})")
    print("  por linea:", Counter(h["id_turno"] for h in criticos).most_common())
    for tipo in ("fijo", "patron", "mixto", "correturno"):
        g = [m["h"] for m in metricas if m["tipo"] == tipo]
        if g:
            print(f"  {tipo:<11} n={len(g):<3} media {st.mean(g):.0f}  sigma {st.pstdev(g):.1f}")

    if fallos:
        print("\nFALLOS:")
        for f in fallos:
            print("  -", f)
        return 1
    print("\nOK  todos los criterios de aceptacion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Comprobar el criterio 1 en la salida de la corrida**

Cuando termine, en su salida debe aparecer que **las 27 ventanas se resolvieron**. Buscar cualquier `INFEASIBLE` o el aviso `*** AVISO: el rodante se detuvo`. Si aparece alguno, el criterio 1 falla: parar y consultar antes de tocar nada más.

- [ ] **Step 5: Ejecutar el test de aceptación**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 tests/test_aceptacion.py
```

Esperado: `OK  todos los criterios de aceptacion`. Si falla algún criterio, no ajustar los números esperados: entender por qué.

- [ ] **Step 6: Comprobar el criterio 4 — cada cubridor en su papel**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python3 - <<'EOF'
import sys; sys.path.insert(0,'src')
from openpyxl import load_workbook
from datetime import date, timedelta
wb = load_workbook('data/output/calendario.xlsx', data_only=True); ws = wb.active
col = {}
for c in range(3, ws.max_column + 1):
    v = ws.cell(4, c).value
    if v and '/' in str(v):
        dd, mm = str(v).split('\n')[1].split('/')
        col[date(2026, int(mm), int(dd))] = c
fila = {str(ws.cell(r,1).value): r for r in range(5, ws.max_row+1) if ws.cell(r,1).value}
print("Semana del 3 al 9 de agosto:")
for w in ('18029935M', '01860358A'):
    sem = [(ws.cell(fila[w], col[date(2026,8,d)]).value or 'LIBRE') for d in range(3,10)]
    print(f"  {w}: " + "  ".join(sem))
print("\nEsperado: 18029935M en VADU47127 (es su principal) y 01860358A en VADP003.")
EOF
```

- [ ] **Step 7: Corregir `CLAUDE.md`**

Dos cambios. Primero, en el párrafo del objetivo, sustituir la afirmación falsa:

> **The objective is lexicographic, not a weighted sum** (`MODELO.md` §6): P1 coverage … Each level is solved and pinned (`obj_i ≤ best_i`) before the next is optimized

por:

> **The objective is a single weighted sum with escalating weights** (`modelo.py:1197`) that *emulates* a lexicographic order: `W1·(coverage + pattern deviation) + W2·(night/weekend/holiday fairness) + W3·(hours + location stability + cubridor order)`, with `W1 ≫ W2 ≫ W3` computed from conservative bounds on each level. It is NOT solved level by level — a true lexicographic solve lived in `resolver_lexicografico`, removed in `47f2230`. The consequence to keep in mind: terms *within* W3 compete against each other in one sum, so a small term there (the cubridor preference order) is drowned out by a large one (hours deviation, measured in minutes).

Segundo, en el paso 3 del pipeline, añadir tras la mención de `reserva_cubridores`:

> Both are driven by `ausencias_criticas`, which precomputes — per titular and ISO week — which days of a critical line lose their holder. If the *whole* row is missing, whoever covers **adopts the plaza**: they work it entire and inherit its rest days (`_adopcion_plaza`). If only part is missing, those are ordinary shifts. On top of that, `_obligacion_principal` makes the declared principal cover their line unless on holiday or already on an equal-or-higher priority critical line.

Y borrar el encabezado huérfano de `src/modelo.py:1202`:

```python
    # -- Resolución LEXICOGRÁFICA (por pasadas; no desborda a ningún horizonte) ---------- #
```

- [ ] **Step 8: Commit**

```bash
git add CLAUDE.md src/modelo.py tests/test_aceptacion.py
git commit -m "corrida anual verificada y CLAUDE.md corregido: el objetivo no es lexicografico"
```

---

## Autorrevisión

**Cobertura de la especificación.** Adopción de plaza → Tasks 1 y 2. Orden de cubridores como restricción dura → Task 3. Los cuatro sitios que replican la regla vieja → Tasks 2 y 4. Criterios de aceptación 1 a 5 → Task 5, pasos 4, 5 y 6. La nota sobre `CLAUDE.md` y el objetivo lexicográfico → Task 5, paso 7. «Qué NO se cambia» se respeta: no se tocan `_solo_rotacion_uvi`, los pesos, ni la estructura de suma ponderada.

**Riesgo señalado en la especificación.** El criterio 1 (27 ventanas factibles) es el primer paso de comprobación de la Task 3 y se repite en la Task 5, con instrucción explícita de parar y consultar en vez de improvisar escapes.

**Consistencia de nombres.** `ausencias_criticas` y `AusenciaCritica` (Task 1) se consumen en las Tasks 2, 3 y 4. `principales` (Task 3) y `semanas_adoptadas` (Task 4) se definen antes de usarse. `_adopcion_plaza` sustituye a `_handover_critico` en la Task 2 y sus tres menciones en comentarios se limpian en las Tasks 2 y 4.

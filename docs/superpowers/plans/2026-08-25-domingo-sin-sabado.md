# Nunca domingo suelto: implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ningún trabajador queda asignado a un domingo sin que también trabaje el sábado de ese
mismo fin de semana — en los cinco mecanismos del pipeline que hoy deciden sábado y domingo por
separado.

**Architecture:** una única función `legal.domingo_ok()` es la fuente de verdad; se pliega en
`legal.permite()` (cubre gratis los dos canjes de `residuo.py`, que ya usan `permite()` como
puerta), y se añade explícitamente donde `permite()` no llega: el CP-SAT de `residuo.py` (nueva
restricción dura) y el intercambio de día suelto de `equidad.py` (nuevo guard que reutiliza
`domingo_ok`). `base.py` además pre-filtra candidatos para no desperdiciar el reparto uniforme de
la cuota en domingos inviables.

**Tech Stack:** Python 3, OR-Tools CP-SAT. Sin framework de tests — el proyecto verifica
ejecutando y auditando la salida (`CLAUDE.md`). Cada tarea lleva su propio script de verificación
en Python, ejecutado con `python3 -c` contra los datos reales de `data/input/`.

**Spec:** `docs/superpowers/specs/2026-08-25-domingo-sin-sabado-design.md`

## Global Constraints

- La regla no aplica a festivos: solo a la pareja SAB/DOM (`tipo_dia(...) == "DOM"` es la única
  condición que la dispara).
- Los trabajadores de tipo `patron` quedan exentos por construcción: `legal.py` nunca se aplica
  sobre un ciclo de patrón heredado, y ningún cambio de este plan toca `libranzas.py` ni
  `base.construir()`.
- En el CP-SAT (`residuo.py`) la regla es **restricción dura e inviolable**, al mismo nivel que
  C4/C5/C6 — antes del nivel 1 (cobertura). Nunca se modela como coste ni como nivel del
  objetivo.
- Todos los comandos de verificación se ejecutan desde la raíz del repo
  (`/home/samu/Documents/Universidad/HT-GROUP`) con el entorno conda `ortools_env` activado y
  `PYTHONPATH=src`.

---

### Task 1: `legal.py` — `domingo_ok`, plegada en `permite()`, y chequeo en `integridad()`

**Files:**
- Modify: `src/legal.py:65` (nueva función, tras `horas_semana_ok`)
- Modify: `src/legal.py:73-75` (`permite`, añadir la nueva condición)
- Modify: `src/legal.py:166-173` (`integridad`, añadir el chequeo)

**Interfaces:**
- Produces: `domingo_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool` — usada por Task 4 (`equidad.py`).

- [ ] **Step 1: Escribir el script de verificación (debe fallar: `domingo_ok` no existe)**

Ejecutar:
```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
import sys
from datetime import timedelta
from cargar_datos import cargar
import legal

datos = cargar()
turno = 'VADN022'   # lv=1 sab=1 dom=1 fes=1, 09:00-19:00 — normal, no localizado
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'DOM')
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)

# 1. Sin el sábado en el plan: se rechaza.
assert legal.domingo_ok(datos, {}, 'TEST', domingo, turno) is False, 'debería rechazar sin sábado'

# 2. Con el sábado en el plan: se acepta.
plan = {('TEST', sabado): turno}
assert legal.domingo_ok(datos, plan, 'TEST', domingo, turno) is True, 'debería aceptar con sábado'

# 3. Un día que no es domingo: siempre True (no aplica).
assert legal.domingo_ok(datos, {}, 'TEST', lunes, turno) is True, 'un lunes nunca debe bloquear'

# 4. permite() hereda la regla.
assert legal.permite(datos, {}, 'TEST', domingo, turno) is False, 'permite() debe rechazar el domingo suelto'
plan = {('TEST', sabado): turno}
assert legal.permite(datos, plan, 'TEST', domingo, turno) is True, 'permite() debe aceptar con sábado ya puesto'

print('legal.domingo_ok OK')
"
```

Expected: `AttributeError: module 'legal' has no attribute 'domingo_ok'`

- [ ] **Step 2: Añadir `domingo_ok` tras `horas_semana_ok` (línea 65 actual)**

```python
def domingo_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """El domingo solo se trabaja si también se trabaja el sábado de ese mismo fin de semana.
    No es del convenio (no lleva número de artículo): es una regla de reparto, pero se define
    aquí porque la consumen los mismos sitios que C4/C5/C6."""
    if datos.tipo_dia(fecha, datos.turnos[turno_id].municipio) != "DOM":
        return True
    return (trabajador_id, fecha - timedelta(days=1)) in plan
```

- [ ] **Step 3: Plegarla en `permite()` (líneas 73-75 actuales)**

Antes:
```python
    return (descanso_ok(datos, plan, trabjador_id, fecha, turno_id, exento_localizado)
            and dias_semana_ok(datos, plan, trabjador_id, fecha)
            and horas_semana_ok(datos, plan, trabjador_id, fecha, turno_id))
```

Después:
```python
    return (descanso_ok(datos, plan, trabjador_id, fecha, turno_id, exento_localizado)
            and dias_semana_ok(datos, plan, trabjador_id, fecha)
            and horas_semana_ok(datos, plan, trabjador_id, fecha, turno_id)
            and domingo_ok(datos, plan, trabjador_id, fecha, turno_id))
```

- [ ] **Step 4: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `legal.domingo_ok OK` sin ningún `AssertionError`.

- [ ] **Step 5: Añadir el chequeo a `integridad()` (líneas 166-173 actuales)**

Localizar dentro de `integridad()` el bucle:
```python
    for (w, f), s in plan.items():
        cuenta[(s, f)] = cuenta.get((s, f), 0) + 1
        if not datos.disponible(w, f):
            fallos.append(f"{w} asignado a {s} el {f:%d/%m} estando de vacaciones")
        elif not datos.opera(s, f):
            fallos.append(f"{w} hace {s} el {f:%d/%m}, día en que esa línea no opera")
        elif not datos.elegible(w, s, f)[0]:
            fallos.append(f"{w} hace {s} el {f:%d/%m} sin capacidad declarada")
```

Añadir una rama más al `elif` (sin tocar las tres existentes):
```python
    for (w, f), s in plan.items():
        cuenta[(s, f)] = cuenta.get((s, f), 0) + 1
        if not datos.disponible(w, f):
            fallos.append(f"{w} asignado a {s} el {f:%d/%m} estando de vacaciones")
        elif not datos.opera(s, f):
            fallos.append(f"{w} hace {s} el {f:%d/%m}, día en que esa línea no opera")
        elif not datos.elegible(w, s, f)[0]:
            fallos.append(f"{w} hace {s} el {f:%d/%m} sin capacidad declarada")
        elif not domingo_ok(datos, plan, w, f, s):
            fallos.append(f"{w} hace {s} el {f:%d/%m} (domingo) sin el sábado de ese fin de semana")
```

- [ ] **Step 6: Verificar el nuevo chequeo de integridad con un plan sintético**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal

datos = cargar()
turno = 'VADN022'
w = '71225031B'   # id real: integridad() llama datos.disponible(w,...), que necesita un id que
                  # exista en datos.trabajadores (con un id inventado da KeyError, no False)
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'DOM'
               and datos.disponible(w, f) and datos.disponible(w, f - timedelta(days=1)))
sabado = domingo - timedelta(days=1)

# Domingo suelto: debe aparecer en integridad().
plan = {(w, domingo): turno}
fallos = legal.integridad(datos, plan)
assert any('sin el sábado' in x for x in fallos), fallos

# Con el sábado: no debe aparecer.
plan = {(w, domingo): turno, (w, sabado): turno}
fallos = legal.integridad(datos, plan)
assert not any('sin el sábado' in x for x in fallos), fallos

print('integridad() detecta domingo suelto OK')
"
```

Expected: `integridad() detecta domingo suelto OK`

- [ ] **Step 7: Commit**

```bash
git add src/legal.py
git commit -m "$(cat <<'EOF'
Añade la regla de domingo sin sábado a legal.py

domingo_ok() se pliega en permite() (cubre gratis los canjes de
residuo.py que ya la usan como puerta) y se audita en integridad().

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M1adkAVEc5zTk5U3WQL4yn
EOF
)"
```

---

### Task 2: `base.py` — filtro de candidatos DOM en `colocar_mixtos`

**Files:**
- Modify: `src/base.py:234-240`

**Interfaces:**
- Consumes: nada nuevo (usa solo `plan`, ya disponible en el ámbito de `colocar_mixtos`).

- [ ] **Step 1: Escribir el script de verificación (debe fallar: hoy puede haber domingos sueltos)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import base, horas, legal

datos = cargar()
plan = base.construir(datos)
libro = horas.LibroHoras.desde_plan(datos, plan)
base.colocar_mixtos(datos, plan, libro)

sueltos = [(w, f) for (w, f), s in plan.items()
           if not legal.domingo_ok(datos, plan, w, f, s)]
assert not sueltos, f'{len(sueltos)} domingos sueltos, ej: {sueltos[:3]}'
print('colocar_mixtos: sin domingos sueltos OK')
"
```

Expected: `AssertionError: 17 domingos sueltos, ej: [('09325377G', datetime.date(2026, 5, 31)), ...]`
(cifra confirmada contra los datos de 2026 al escribir este plan — si tu instancia de datos es
distinta, el número puede variar, pero debe fallar).

- [ ] **Step 2: Añadir el filtro para la clase DOM (líneas 234-240 actuales)**

Antes:
```python
        for clase in FINDE:
            candidatos = [fecha for fecha in datos.lista_dias_calendario
                          if datos.disponible(trabajador_id, fecha)
                          and (trabajador_id, fecha) not in plan
                          and any(datos.tipo_dia(fecha, datos.turnos[s].municipio) == clase
                                  and datos.elegible(trabajador_id, s, fecha) == (True, False)
                                  and _libre(datos, cubiertas, s, fecha) for s in findes)]
```

Después:
```python
        for clase in FINDE:
            candidatos = [fecha for fecha in datos.lista_dias_calendario
                          if datos.disponible(trabajador_id, fecha)
                          and (trabajador_id, fecha) not in plan
                          and (clase != "DOM"
                               or (trabajador_id, fecha - timedelta(days=1)) in plan)
                          and any(datos.tipo_dia(fecha, datos.turnos[s].municipio) == clase
                                  and datos.elegible(trabajador_id, s, fecha) == (True, False)
                                  and _libre(datos, cubiertas, s, fecha) for s in findes)]
```

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `colocar_mixtos: sin domingos sueltos OK`

- [ ] **Step 4: Commit**

```bash
git add src/base.py
git commit -m "$(cat <<'EOF'
colocar_mixtos: el domingo de un mixto exige el sábado ya concedido

Filtra los candidatos de DOM antes de repartir la cuota uniforme, para
no desperdiciar picks en domingos que permite() acabaría rechazando.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M1adkAVEc5zTk5U3WQL4yn
EOF
)"
```

---

### Task 3: `residuo.py` — restricción dura en el CP-SAT

**Files:**
- Modify: `src/residuo.py:257-258` (nuevo bloque, entre el fin del bucle de C4 y el `for w in pool:` de C9/C5/C6)

**Interfaces:**
- Consumes: `pool`, `por_trab`, `x` (ya construidos antes de la línea 240 en `resolver()`).

Esta tarea no admite un test aislado del resto de `resolver()` — es un fragmento dentro de una
función de ~500 líneas que construye un único modelo CP-SAT, y extraerlo a una función propia
para testearlo suelto no es proporcional (el proyecto no tiene tests, y `resolver()` ya sigue ese
patrón para C4/C5/C6). La verificación real de esta tarea es la ejecución completa del pipeline
en la Task 5. Aquí solo se comprueba que el fragmento no rompe la construcción del modelo.

- [ ] **Step 1: Añadir el bloque nuevo (tras la línea 257 actual, antes de `for w in pool: # C9`)**

Localizar el final del bloque de C4:
```python
                    elif (a, b) in exentos:
                        # Permitido, pero contado: `e` se activa si se usan los dos, y el nivel 2
                        # lo minimiza. No hace falta forzar e=0 cuando no: se está minimizando.
                        e = modelo.NewBoolVar(f"loc_{w}_{f:%m%d}")
                        modelo.Add(va + vb - 1 <= e)
                        exenciones.append(e)

    for w in pool:
        # C9 — jornada anual. Los correturnos llegan a cero, así que su presupuesto es entero.
```

Insertar el bloque nuevo entre las dos:
```python
                    elif (a, b) in exentos:
                        # Permitido, pero contado: `e` se activa si se usan los dos, y el nivel 2
                        # lo minimiza. No hace falta forzar e=0 cuando no: se está minimizando.
                        e = modelo.NewBoolVar(f"loc_{w}_{f:%m%d}")
                        modelo.Add(va + vb - 1 <= e)
                        exenciones.append(e)

    for w in pool:                          # domingo sin sábado: restricción dura, nunca se paga
        vars_por_fecha_tipo: dict[tuple[date, str], list] = defaultdict(list)
        for f, s in por_trab[w]:
            tipo = datos.tipo_dia(f, datos.turnos[s].municipio)
            if tipo in ("SAB", "DOM"):
                vars_por_fecha_tipo[(f, tipo)].append(x[(w, f, s)])
        for (f, tipo), vs in vars_por_fecha_tipo.items():
            if tipo != "DOM":
                continue
            sab_vars = vars_por_fecha_tipo.get((f - timedelta(days=1), "SAB"), [])
            modelo.Add(sum(vs) <= sum(sab_vars))

    for w in pool:
        # C9 — jornada anual. Los correturnos llegan a cero, así que su presupuesto es entero.
```

- [ ] **Step 2: Verificar que el módulo sigue importando sin errores de sintaxis**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
import residuo
print('residuo.py importa sin errores de sintaxis: OK')
"
```

Expected: `residuo.py importa sin errores de sintaxis: OK`

- [ ] **Step 3: Commit**

```bash
git add src/residuo.py
git commit -m "$(cat <<'EOF'
CP-SAT: domingo sin sábado como restricción dura del pool

Nueva restricción junto a C4/C5/C6, antes del nivel 1 de cobertura:
para cada correturno, trabajar un domingo exige tener variables de
sábado activables ese mismo fin de semana.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M1adkAVEc5zTk5U3WQL4yn
EOF
)"
```

---

### Task 4: `equidad.py` — guard en el intercambio de día suelto

**Files:**
- Modify: `src/equidad.py:295` (nuevo helper `_semana_respeta_domingo`, antes de `_clase`)
- Modify: `src/equidad.py:282-292` (`_valido_dia`, añadir la comprobación)

**Interfaces:**
- Consumes: `legal.domingo_ok` (Task 1).
- Produces: `_semana_respeta_domingo(datos: Datos, plan: Plan, trab: str, lunes: date) -> bool` — uso interno de `equidad.py`.

- [ ] **Step 1: Escribir el script de verificación del helper (debe fallar: no existe)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import equidad

datos = cargar()
turno = 'VADN022'
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'DOM')
lunes = domingo - timedelta(days=6)
sabado = domingo - timedelta(days=1)

# Domingo sin sábado -> semana inválida.
plan = {('TEST', domingo): turno}
assert equidad._semana_respeta_domingo(datos, plan, 'TEST', lunes) is False

# Domingo con sábado -> válida.
plan = {('TEST', domingo): turno, ('TEST', sabado): turno}
assert equidad._semana_respeta_domingo(datos, plan, 'TEST', lunes) is True

# Solo sábado -> válida.
plan = {('TEST', sabado): turno}
assert equidad._semana_respeta_domingo(datos, plan, 'TEST', lunes) is True

print('_semana_respeta_domingo OK')
"
```

Expected: `AttributeError: module 'equidad' has no attribute '_semana_respeta_domingo'`

- [ ] **Step 2: Añadir el helper, antes de `_clase` (línea 295 actual)**

```python
def _semana_respeta_domingo(datos: Datos, plan: Plan, trab: str, lunes: date) -> bool:
    for i in range(7):
        f = lunes + timedelta(days=i)
        s = plan.get((trab, f))
        if s is not None and not legal.domingo_ok(datos, plan, trab, f, s):
            return False
    return True
```

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `_semana_respeta_domingo OK`

- [ ] **Step 4: Usarlo dentro de `_valido_dia` (líneas 282-292 actuales)**

Antes:
```python
    desde, hasta = min(da, db), max(da, db)
    antes = legal.formas(datos, plan, a, desde, hasta) + legal.formas(datos, plan, b, desde, hasta)
    del plan[(a, da)], plan[(b, db)]
    plan[(b, da)], plan[(a, db)] = sa, sb
    if _empeora(datos, plan, a, b, desde, hasta, antes, pactadas):
        del plan[(b, da)], plan[(a, db)]
        plan[(a, da)], plan[(b, db)] = sa, sb
        return False
    libro.borra(a, sa); libro.apunta(a, sb)
    libro.borra(b, sb); libro.apunta(b, sa)
    return True
```

Después:
```python
    desde, hasta = min(da, db), max(da, db)
    antes = legal.formas(datos, plan, a, desde, hasta) + legal.formas(datos, plan, b, desde, hasta)
    del plan[(a, da)], plan[(b, db)]
    plan[(b, da)], plan[(a, db)] = sa, sb
    lunes = _lunes(da)
    rompe_domingo = (not _semana_respeta_domingo(datos, plan, a, lunes)
                      or not _semana_respeta_domingo(datos, plan, b, lunes))
    if rompe_domingo or _empeora(datos, plan, a, b, desde, hasta, antes, pactadas):
        del plan[(b, da)], plan[(a, db)]
        plan[(a, da)], plan[(b, db)] = sa, sb
        return False
    libro.borra(a, sa); libro.apunta(a, sb)
    libro.borra(b, sb); libro.apunta(b, sa)
    return True
```

Nota: `da` y `db` caen siempre en la misma semana ISO (invariante ya exigido por `pulir_dias`
antes de llamar a `_valido_dia`), así que `_lunes(da)` es la semana de los dos.

- [ ] **Step 5: Verificar que el guard bloquea un intercambio que dejaría un domingo huérfano**

Usa dos correturnos reales (capacidad ya confirmada por `grep` sobre `capacidades.csv`:
`71225031B` y `71136820M` solo tienen fila explícita en `VADN022`, sab=1/dom=1 — ahí NO tienen
`lv`, así que el día que se intercambia entre semana usa `VADN001`, donde ninguno de los dos
tiene fila explícita y por tanto heredan la capacidad automática de correturno, lv=1 incluido).
Así el intercambio solo puede fallar por la regla nueva, no por falta de capacidad:

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import equidad

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
a, b = '71225031B', '71136820M'

domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and datos.disponible(a, f) and datos.disponible(b, f)
               and datos.disponible(a, f - timedelta(days=1))
               and datos.disponible(b, f - timedelta(days=4)))
sabado = domingo - timedelta(days=1)
miercoles = domingo - timedelta(days=4)

class LibroFalso:
    def horas(self, w): return 0.0
    def objetivo(self, w): return 999999.0
    def borra(self, w, s): pass
    def apunta(self, w, s): pass

# a tiene sábado+domingo; b tiene un miércoles cualquiera de esa semana. Intercambiar el
# domingo de a por el miércoles de b dejaría a b con domingo sin sábado: debe rechazarse.
plan = {(a, domingo): turno_finde, (a, sabado): turno_finde, (b, miercoles): turno_lv}
ok = equidad._valido_dia(datos, plan, LibroFalso(), a, domingo, b, miercoles, set())
assert ok is False, 'debe rechazar: b se quedaría con domingo sin sábado'
assert (a, domingo) in plan and (b, miercoles) in plan, 'el plan no debe quedar mutado'
print('_valido_dia respeta domingo_ok OK')
"
```

Expected: `_valido_dia respeta domingo_ok OK`. Si falla con un `AssertionError` distinto (p.ej.
`StopIteration` de `next(...)`, que significaría que ninguna fecha del año cumple las cinco
condiciones de disponibilidad a la vez) prueba con otro par de correturnos de
`data/input/trabajadores.csv` (tipo `correturno`) — la lógica del script no depende de estos dos
IDs en concreto, solo de que compartan capacidad de la forma descrita.

- [ ] **Step 6: Commit**

```bash
git add src/equidad.py
git commit -m "$(cat <<'EOF'
pulir_dias: el intercambio de día suelto no crea domingos huérfanos

_semana_respeta_domingo comprueba, tras el intercambio tentativo, que
ni a ni b quedan con un domingo sin el sábado de esa semana — reusa
legal.domingo_ok. pulir() (intercambio de semana completa) no lo
necesita: mueve la semana como unidad atómica.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M1adkAVEc5zTk5U3WQL4yn
EOF
)"
```

---

### Task 5: Verificación end-to-end

**Files:** ninguno (solo ejecución)

**Interfaces:** ninguna — integra las cuatro tareas anteriores.

- [ ] **Step 1: Ejecutar el pipeline completo**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP
source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null
conda activate ortools_env
python3 src/pipeline.py 2>&1 | tee /tmp/claude-1000/-home-samu-Documents-Universidad-HT-GROUP/59b8ca0f-309b-4512-b1b0-4963b90517cd/scratchpad/pipeline_domingo.log
```

Expected: termina con código 0 y con la línea `AUDITORÍA — integridad: correcta` (si aparece
`FALLOS`, revisar si mencionan "sin el sábado" — sería la señal de que algún camino no
identificado en el spec sigue decidiendo sábado/domingo por su cuenta).

- [ ] **Step 2: Confirmar en el log que no hay ningún domingo huérfano en el plan final**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from cargar_datos import cargar
import base, equidad, forma, horas, legal, libranzas, residuo

datos = cargar()
plan = base.construir(datos)
libro = horas.LibroHoras.desde_plan(datos, plan)
protegidos, flexibles = base.colocar_mixtos(datos, plan, libro)
reg = libranzas.ceder(datos, plan, libro, protegidos)
rep = forma.repartir(datos, plan)
residuo.resolver(datos, plan, libro, rep, flexibles, segundos=300, hilos=8)
residuo.rellenar_refuerzos(datos, plan, libro)
residuo.canjear_en_cadena(datos, plan, libro)
residuo.canjear_refuerzos(datos, plan, libro)
equidad.pulir(datos, plan, libro)

sueltos = [(w, f) for (w, f), s in plan.items() if not legal.domingo_ok(datos, plan, w, f, s)]
assert not sueltos, f'{len(sueltos)} domingos sueltos tras el pipeline completo: {sueltos[:5]}'
print(f'Pipeline completo: 0 domingos sueltos en {len(plan)} asignaciones. OK')
"
```

Expected: `Pipeline completo: 0 domingos sueltos en NNNNN asignaciones. OK`. Esta ejecución tarda
lo mismo que el pipeline completo (~4 min) porque repite el mismo trabajo — es intencional: es
la única forma de comprobar el invariante sobre el plan real completo sin depender de que
`pipeline.py` exponga el `plan` final como variable inspeccionable.

- [ ] **Step 3: Comparar cobertura y cuotas de domingo contra la ejecución de referencia**

Revisar en `pipeline_domingo.log`:
- `PASO D — cobertura final: N/18295` — comparar contra `18284/18295 (99.9%)` de
  `pipeline_run2.log` (la ejecución de referencia de esta sesión, sin la regla). Una bajada es
  esperada y aceptable (ver spec, «Consecuencias esperadas»); anota la cifra nueva.
- Tabla de `PASO A2` (mixtos): columna `dom` — comparar contra la cuota de cada mixto. Puede
  quedar por debajo si no hay sábados suficientes para emparejar.
- `nivel 1 cobertura`, `nivel 2 apoyos en localizado`, `nivel 3 equidad de findes`,
  `nivel 4 forma semanal` — confirmar que los cuatro siguen en `OPTIMAL` (no `UNKNOWN` ni
  `INFEASIBLE`) y que los tiempos no se disparan muy por encima de los ~115 s de antes.

Esto es un paso de lectura, no de comando — deja constancia en la conversación de las cifras
antes/después.

- [ ] **Step 4: Reportar el resultado**

No hay commit en este paso (no se toca código). Resume en la conversación: cobertura final,
diferencia con la referencia, y si algún nivel del CP-SAT dejó de ser `OPTIMAL`.

---

### Task 6 (amendment, descubierta en Task 5): `libranzas.py` — `candidatas()` no cede un sábado que deja huérfano el domingo

**Descubierto en Task 5** (verificación end-to-end): 22 de 23 domingos sueltos en el plan final son
nuevos, todos de trabajadores `tipo=patron`, y todos trazables a `libranzas.ceder()` — un sexto
mecanismo que no estaba en la lista de cinco del spec original. Ver el Addendum en
`docs/superpowers/specs/2026-08-25-domingo-sin-sabado-design.md` para el diagnóstico completo.

**Files:**
- Modify: `src/libranzas.py:143-165` (`candidatas`, añadir función `_huerfano_domingo` justo antes
  y filtrar sus dos `return`)

**Interfaces:**
- Consumes: nada nuevo (usa solo `datos`/`plan`, ya disponibles en el ámbito de `candidatas`).

- [ ] **Step 1: Escribir el script de verificación (debe fallar: hoy `candidatas` puede ofrecer un
  sábado que deja huérfano el domingo)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal, ritmo

datos = cargar()
import libranzas

# Construye un escenario mínimo: un trabajador con sábado Y domingo trabajados, y sin nada más
# alrededor que pueda confundir la ventana de descanso.
turno = 'VADN022'   # sab=1/dom=1, ver capacidades.csv
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'DOM')
sabado = domingo - timedelta(days=1)
trab = '71225031B'   # id real, correturno (da igual el tipo real: candidatas() no debe mirarlo)

plan = {(trab, sabado): turno, (trab, domingo): turno}

class RitmoFalso:
    rigido = False
    ratio = 0.0

candidatas = libranzas.candidatas(datos, plan, RitmoFalso(), trab, exceso=100.0, protegidos=set())
sueltas = {u.dias[0] for u in candidatas if len(u.dias) == 1}
assert sabado not in sueltas, f'candidatas() sigue ofreciendo el sábado {sabado} suelto, dejaría huérfano el domingo {domingo}'
assert domingo in sueltas, 'el domingo SÍ debe poder cederse suelto (ceder el domingo nunca deja huérfano nada)'
print('candidatas(): no ofrece el sábado suelto cuando el domingo sigue trabajado — OK')
"
```

Expected (antes del fix): `AssertionError: candidatas() sigue ofreciendo el sábado ... suelto,
dejaría huérfano el domingo ...`

- [ ] **Step 2: Añadir `_huerfano_domingo` y filtrar los dos `return` de `candidatas` (líneas
  143-165 actuales)**

Antes:
```python
def candidatas(datos: Datos, plan: Plan, rit: Ritmo, trab: str, exceso: float,
               protegidos: set[date]) -> list[Unidad]:
    """Qué se le puede quitar a este trabajador, según lo rígida que sea su plaza."""
    bloques = [b for b in ritmo_mod.bloques(plan, trab)
               if not (protegidos & set(b))]
    if not bloques:
        return []

    def unidad(dias: list[date]) -> Unidad:
        return Unidad(dias=dias,
                      descanso=_descanso_de(dias, rit, plan, trab),
                      horas=sum(datos.turnos[plan[(trab, f)]].horas for f in dias))

    if rit.rigido:
        # No se fracciona: se cede el ciclo entero aunque pase de largo del exceso. Lo que sobre
        # deja al titular por debajo del objetivo, y esa holgura la aprovechan los pasos C y D.
        return [unidad(b) for b in bloques]

    # En las plazas flexibles se cede SIEMPRE día a día, nunca el bloque entero. Ceder una semana
    # de golpe abre cinco días seguidos de la misma línea, y taparlos exige encontrar a alguien
    # libre los cinco; repartidos por el año, cada uno se tapa por separado y con mucha más gente
    # disponible. El exceso típico de un patrón (+71 h) son nueve días sueltos, no dos semanas.
    return [unidad([f]) for b in bloques for f in b]
```

Después:
```python
def _huerfano_domingo(datos: Datos, plan: Plan, trab: str, dias: list[date]) -> bool:
    """Ceder estos días juntos, ¿deja huérfano un domingo que el titular seguiría trabajando?

    Un día `f` lo deja huérfano si el siguiente tiene turno en `plan`, ese siguiente NO está
    también en `dias` (si lo estuviera, se ceden ambos juntos: no hay orfandad) y es domingo. No
    se exige que `f` mismo sea tipo SAB: un sábado festivo debe seguir contando como sábado
    trabajado si el titular lo trabaja — el criterio es posicional (el día natural anterior), no
    por etiqueta de tipo (mismo criterio que ya corrigió la restricción del CP-SAT en residuo.py)."""
    conjunto = set(dias)
    for f in dias:
        siguiente = f + timedelta(days=1)
        if siguiente in conjunto:
            continue
        turno_siguiente = plan.get((trab, siguiente))
        if turno_siguiente is None:
            continue
        if datos.tipo_dia(siguiente, datos.turnos[turno_siguiente].municipio) == "DOM":
            return True
    return False


def candidatas(datos: Datos, plan: Plan, rit: Ritmo, trab: str, exceso: float,
               protegidos: set[date]) -> list[Unidad]:
    """Qué se le puede quitar a este trabajador, según lo rígida que sea su plaza."""
    bloques = [b for b in ritmo_mod.bloques(plan, trab)
               if not (protegidos & set(b))]
    if not bloques:
        return []

    def unidad(dias: list[date]) -> Unidad:
        return Unidad(dias=dias,
                      descanso=_descanso_de(dias, rit, plan, trab),
                      horas=sum(datos.turnos[plan[(trab, f)]].horas for f in dias))

    if rit.rigido:
        # No se fracciona: se cede el ciclo entero aunque pase de largo del exceso. Lo que sobre
        # deja al titular por debajo del objetivo, y esa holgura la aprovechan los pasos C y D.
        return [unidad(b) for b in bloques if not _huerfano_domingo(datos, plan, trab, b)]

    # En las plazas flexibles se cede SIEMPRE día a día, nunca el bloque entero. Ceder una semana
    # de golpe abre cinco días seguidos de la misma línea, y taparlos exige encontrar a alguien
    # libre los cinco; repartidos por el año, cada uno se tapa por separado y con mucha más gente
    # disponible. El exceso típico de un patrón (+71 h) son nueve días sueltos, no dos semanas.
    return [unidad([f]) for b in bloques for f in b
            if not _huerfano_domingo(datos, plan, trab, [f])]
```

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `candidatas(): no ofrece el sábado suelto cuando el domingo sigue trabajado — OK`

- [ ] **Step 4: Verificar que un sábado SÍ se puede ceder cuando el domingo NO está trabajado (no
  sobre-restringir)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import libranzas

datos = cargar()
turno = 'VADN022'
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'DOM')
sabado = domingo - timedelta(days=1)
trab = '71225031B'

# Solo el sábado trabajado, el domingo NO — cederlo suelto no deja huérfano nada, debe seguir
# ofreciéndose.
plan = {(trab, sabado): turno}

class RitmoFalso:
    rigido = False
    ratio = 0.0

candidatas = libranzas.candidatas(datos, plan, RitmoFalso(), trab, exceso=100.0, protegidos=set())
sueltas = {u.dias[0] for u in candidatas if len(u.dias) == 1}
assert sabado in sueltas, 'el sábado sin domingo trabajado SÍ debe poder cederse suelto'
print('candidatas(): no sobre-restringe cuando no hay domingo que proteger — OK')
"
```

Expected: `candidatas(): no sobre-restringe cuando no hay domingo que proteger — OK`

- [ ] **Step 5: Verificar que un bloque rígido que incluye sábado+domingo consecutivos se sigue
  cediendo entero (no se rompe la Task de patrones rígidos)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import libranzas

datos = cargar()
turno = 'VADN022'
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'DOM')
sabado = domingo - timedelta(days=1)
viernes = sabado - timedelta(days=1)
trab = '71225031B'

# Bloque rígido de 3 días consecutivos que incluye el sábado y el domingo juntos: debe cederse
# entero, no filtrarse por huerfano_domingo (van juntos en la misma unidad).
plan = {(trab, viernes): turno, (trab, sabado): turno, (trab, domingo): turno}

class RitmoFalso:
    rigido = True
    ratio = 1.0

candidatas = libranzas.candidatas(datos, plan, RitmoFalso(), trab, exceso=100.0, protegidos=set())
assert len(candidatas) == 1, f'se esperaba 1 unidad (bloque entero), salieron {len(candidatas)}'
assert set(candidatas[0].dias) == {viernes, sabado, domingo}, candidatas[0].dias
print('candidatas(): bloque rígido con sábado+domingo se cede entero — OK')
"
```

Expected: `candidatas(): bloque rígido con sábado+domingo se cede entero — OK`

- [ ] **Step 6: Commit**

```bash
git add src/libranzas.py
git commit -m "$(cat <<'EOF'
libranzas: no cede un sábado suelto que deje huérfano el domingo

candidatas() es la única fuente de unidades cedibles (fase1 y fase2 la
comparten): un sexto mecanismo que el spec original no había listado.
_huerfano_domingo() bloquea ceder un día cuando el siguiente sigue
trabajado y es domingo, sin exigir que el día cedido sea tipo SAB (un
sábado festivo cuenta igual) y sin distinguir bloque rígido de día
suelto: si el domingo va en la misma unidad, se cede junto y no hay
orfandad.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Re-verificación end-to-end tras la Task 6

**Files:** ninguno (solo ejecución)

- [ ] **Step 1: Ejecutar el pipeline completo y confirmar 0 domingos sueltos**

Repetir exactamente el Step 1 y Step 2 de la Task 5 (pipeline completo + réplica inline con
`legal.integridad`), sobre el código ya con la Task 6 aplicada. Esperado:
`AUDITORÍA — integridad: correcta` (o, si quedan fallos, que ninguno mencione "sin el sábado") y
`Pipeline completo: 0 domingos sueltos en NNNNN asignaciones. OK`.

Si aparece CUALQUIER domingo suelto nuevo, repetir el diagnóstico de la Task 5 (clasificar por
tipo de trabajador, comparar contra el plan justo tras el Paso A) para identificar si es un
séptimo mecanismo no contemplado — no asumir que la Task 6 lo cierra todo sin comprobarlo.

- [ ] **Step 2: Reportar el resultado**

No hay commit en este paso. Resume en la conversación: si el invariante ya se sostiene end-to-end,
y la cobertura final comparada con la Task 5 (referencia: 18284/18295, 99.9%). El rendimiento del
CP-SAT (niveles 1 y 4 en FEASIBLE en vez de OPTIMAL) queda fuera de alcance de esta
re-verificación — se investiga por separado, no bloquea el cierre de este plan.

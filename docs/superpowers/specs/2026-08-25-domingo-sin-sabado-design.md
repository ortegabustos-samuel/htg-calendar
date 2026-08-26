# Nunca domingo suelto: el domingo exige el sábado de su fin de semana

Fecha: 2026-08-25 · Estado: Tasks 1-7 implementadas y revisadas; Task 5 (verificación end-to-end)
encontró un sexto mecanismo no contemplado (`libranzas.ceder`), la Task 6 lo cerró y la Task 7
confirmó el invariante end-to-end — solo queda el caso benigno del esqueleto, tolerado por diseño.
Ver «Addendum» al final.

## El problema

Regla de negocio nueva, no derivada del convenio: un trabajador solo puede hacer sábado y
domingo juntos, o solo sábado — nunca un domingo sin el sábado de ese mismo fin de semana. Debe
aplicarse a todos los trabajadores; los de patrón quedan a salvo por construcción, porque
`legal.py` nunca se aplica sobre un ciclo de patrón heredado (solo donde el pipeline INVENTA una
secuencia — ver `CLAUDE.md`).

La regla no existe hoy en ningún sitio, y hay **cinco mecanismos** que deciden, cada uno por su
cuenta, si alguien trabaja un día concreto de fin de semana sin saber nada de los demás (un sexto,
`libranzas.ceder`, apareció más tarde en la verificación end-to-end — ver «Addendum»):

1. `base.colocar_mixtos` (fase 2, paso A2) — reparte la cuota de SAB/DOM/FEST de cada mixto por
   separado, un `for clase in FINDE` independiente por clase.
2. `residuo.resolver` (paso D, CP-SAT) — las variables `x` del pool de correturnos no tienen
   ninguna restricción que ligue sábado y domingo.
3. `residuo.canjear_refuerzos` — puede asignarle a cualquiera (también a un trabajador de patrón)
   un hueco real nuevo en cualquier fecha del año, incluido un domingo suelto.
4. `residuo.canjear_en_cadena` — mismo riesgo, por la vía del canje a tres bandas.
5. `equidad.pulir_dias` (paso E) — intercambia días sueltos de la misma semana ISO entre dos
   compañeros; puede darle a alguien un domingo sin darle el sábado que lo acompañaba.

`equidad.pulir` (intercambio de semana ISO completa) **no** está en la lista: mueve la semana
entera como unidad atómica, así que si el invariante ya se cumplía antes del intercambio, se
sigue cumpliendo después para las dos personas implicadas.

Investigación relevante ya hecha en esta sesión:

- `rellenar_refuerzos` no puede romper la regla: REF CAL opera solo de lunes a viernes
  (`residuo.py:556-557`), así que nunca reparte en fin de semana.
- Las variables `y` (canje mismo día) y `z` (día flexible del mixto) tampoco pueden: `y` no
  cambia QUÉ día se trabaja, solo qué turno; `z` está acotada a `range(5)` (L-V) desde el lunes de
  la semana flexible.
- `grep` confirma que **todo** uso actual de `legal.permite()` en el repo es exactamente "el
  pipeline inventa una secuencia" — nunca un traspaso de patrón heredado (`libranzas.py` no lo
  llama nunca). Eso es lo que permite plegar la regla nueva en `permite()` sin tocar tres de los
  cinco sitios.

## Qué se cambia

### 1. `legal.py` — nueva función `domingo_ok`, plegada en `permite()`

```python
def domingo_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """El domingo solo se trabaja si también se trabaja el sábado de ese mismo fin de semana.
    No es del convenio (no lleva número de artículo): es una regla de reparto, pero se define
    aquí porque la consumen los mismos sitios que C4/C5/C6."""
    if datos.tipo_dia(fecha, datos.turnos[turno_id].municipio) != "DOM":
        return True
    return (trabajador_id, fecha - timedelta(days=1)) in plan
```

Se añade al `and` de `permite()`, junto a `descanso_ok`/`dias_semana_ok`/`horas_semana_ok`. No
toca festivos: un `FEST` puede caer cualquier día de la semana y la regla es solo sobre la pareja
SAB/DOM.

Esto cubre, sin tocarlos, los puntos 3 y 4 del problema (`canjear_refuerzos`,
`canjear_en_cadena`), porque ambos ya usan `legal.permite()` como puerta antes de asignar el
hueco nuevo. También entra en vigor dentro de `_elegir_linea` (punto 1, mixtos), como red de
seguridad — ver siguiente sección para el porqué no basta por sí sola ahí.

### 2. `base.py` — `colocar_mixtos`, fase 2: filtro explícito en los candidatos de DOM

En el bucle `for clase in FINDE`, cuando `clase == "DOM"`, el list comprehension de `candidatos`
gana una condición más: `(trabajador_id, fecha - timedelta(days=1)) in plan`.

Motivo para no confiar solo en la red de seguridad de `permite()`: `_uniformes(candidatos, cuota)`
elige un subconjunto de fechas **antes** de saber cuáles son viables, repartido uniformemente por
el año. Si de las candidatas sin filtrar la mayoría cae en fines de semana donde el sábado no
tiene por qué haber sido concedido, `_uniformes` puede gastar sus picks casi todos en domingos que
`permite()` acabará rechazando uno a uno — la cuota de domingo del mixto queda peor cubierta de lo
necesario, con `_uniformes` "desperdiciando" elecciones en huecos inviables en vez de repartir
entre los domingos que sí son alcanzables. Filtrar antes de `_uniformes` evita ese desperdicio.

`FINDE = ("SAB", "DOM", "FEST")` ya procesa SAB antes que DOM, así que en el momento de construir
los candidatos de DOM, cualquier sábado concedido esa semana ya está en `plan`.

### 3. `residuo.py` — nueva restricción dura en el CP-SAT, al nivel de C4/C5/C6

Confirmado: restricción **dura e inviolable**, antes que el nivel 1 (cobertura) — un domingo que
solo pudiera cubrir un correturno sin sábado esa semana se queda sin cubrir antes que romper la
regla.

Para cada trabajador `w` del pool, se agrupan sus variables `x` por `(fecha, tipo_dia)`:

```python
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
```

Se añade como bloque propio, junto al bloque de C4 (línea ~242-257 actual), antes del bucle que
mete C5/C6/C9. `sab_vars` vale `[]` (suma 0) cuando no existe ninguna variable de sábado
clasificada para ese trabajador ese día — en ese caso la restricción bloquea cualquier variable de
domingo de esa fecha en `1`.

No hace falta tocar las variables `y` ni `z` en el modelo: `y` nunca cambia de día (mismo `f` que
el REF CAL que sustituye, y REF CAL es L-V) y `z` está acotada a L-V por construcción (ver
Investigación).

### 4. `equidad.py` — guarda en `pulir_dias` (intercambio de día suelto)

Nuevo helper, junto a `_clase`:

```python
def _semana_respeta_domingo(datos: Datos, plan: Plan, trab: str, lunes: date) -> bool:
    for i in range(7):
        f = lunes + timedelta(days=i)
        s = plan.get((trab, f))
        if s is not None and not legal.domingo_ok(datos, plan, trab, f, s):
            return False
    return True
```

Se llama dentro de `_valido_dia`, para `a` y `b`, sobre la semana de `lunes` — en el mismo punto
donde ya se aplica el intercambio tentativo y se comprueba `_empeora` (línea ~284-289 actual):
si cualquiera de los dos queda con un domingo huérfano, se deshace el intercambio igual que
cuando `_empeora` devuelve `True`.

Se recorre la semana entera y no solo `da`/`db`, porque el domingo huérfano puede ser uno que la
persona **ya tenía** de antes y que el intercambio deja sin su sábado (p.ej. `b` cede su propio
sábado como `db` a cambio del domingo `da` de `a`: `b` gana un domingo y pierde el sábado que lo
sostenía, en la misma operación).

`equidad.pulir` (intercambio de semana ISO completa) no se toca — ver «El problema».

### 5. Bonus — `legal.integridad()`

Se añade una comprobación más a la lista de "esto nunca debería pasar" en el plan final:

```python
for (w, f), s in plan.items():
    if not domingo_ok(datos, plan, w, f, s):
        fallos.append(f"{w} hace {s} el {f:%d/%m} (domingo) sin el sábado de ese fin de semana")
```

Encaja con la filosofía del proyecto — "no hay tests, la verificación es la salida" — y deja el
invariante comprobado automáticamente en cada ejecución futura, reutilizando `domingo_ok` sin
coste de diseño adicional.

## Consecuencias esperadas

- La cuota de domingos de algún mixto o correturno puede quedar por debajo de su referencia de
  equidad si no hay suficientes sábados disponibles para emparejarlos — es el resultado aceptado,
  preferible a romper la regla.
- El nivel 1 (cobertura) del paso D puede bajar respecto a la ejecución anterior si algún domingo
  con demanda real solo podía cubrirlo un correturno sin sábado libre esa semana.
- Tiempo de resolución del CP-SAT: la restricción nueva es lineal en el número de días de fin de
  semana del pool (pequeño respecto a las ~23.600 variables actuales), no se espera impacto
  perceptible. **Contradicho en la práctica** (spike del 2026-08-26, tras cerrar el plan): aislado
  en esta máquina, con el mismo dataset y setup, el nivel 1 pasa de OPTIMAL en 35 s (valor 2677)
  a FEASIBLE agotando el tope de 300 s (valor 2661) solo por esta restricción — confirmado
  comparando con y sin ella, dos reconstrucciones limpias seguidas para descartar ruido de
  máquina. La causa más probable: el nivel 1 es el único de los cuatro que arranca sin semilla
  (`AddHint`), y el acoplamiento nuevo entre las variables de sábado y domingo de cada correturno
  endurece justo ese espacio de búsqueda sin ayuda de arranque. Aceptado como coste conocido — la
  cobertura FINAL del pipeline completo (tras refuerzos, canjes y equidad) no empeoró en la
  práctica (18287/18295 tras la Task 6 frente a 18284/18295 de referencia); si el tiempo de
  resolución llega a ser un problema real, las palancas a explorar son dar más segundos solo al
  nivel 1 o sembrarlo con una heurística propia (hoy es el único nivel sin `AddHint`).

## Verificación

No hay tests en el proyecto: la verificación es la salida (`CLAUDE.md`). Plan:

1. Ejecutar `python3 src/pipeline.py` completo.
2. Confirmar que `legal.auditar` no reporta ningún fallo de integridad nuevo (el chequeo del
   punto 5 debe salir limpio).
3. Revisar en el log del paso A2, paso D (niveles 1-4) y paso E si la cuota real de domingos de
   mixtos/correturnos baja respecto a la ejecución de referencia de esta sesión (ver
   `pipeline_run2.log`), y si el nº de huecos sin cubrir del paso D sube.
4. Confirmar que el tiempo de resolución del CP-SAT no se degrada de forma notable.

## Addendum (2026-08-26): sexto mecanismo — `libranzas.ceder`

La Task 5 (verificación end-to-end, tras implementar y revisar las Tasks 1-4) ejecutó el pipeline
completo y encontró **23 domingos sueltos** en el plan final — el invariante no se sostiene todavía.
Diagnóstico completo en `.superpowers/sdd/2026-08-25-domingo-sin-sabado/task-5-report.md`; resumen:

- Los 23 son, sin excepción, trabajadores `tipo=patron`. Cero en `mixto`/`correturno`: las Tasks 2
  (mixtos), 3 (CP-SAT del pool) y 4 (`pulir_dias`) cierran correctamente los cinco mecanismos que
  este documento identificó.
- Solo 1 de los 23 coincide con un domingo suelto que ya existía justo tras el Paso A puro (el caso
  benigno, aceptado por construcción). **Los otros 22 son nuevos.**
- Causa: `libranzas.ceder()` (Paso B) — no listado como mecanismo en la sección «El problema» —
  cede días sueltos (típicamente un sábado) del ciclo de un patrón para devolver su exceso de
  horas, sin comprobar nunca si eso deja huérfano el domingo de ese mismo fin de semana que el
  titular sigue trabajando. `libranzas.py` no importa `legal` en ningún punto; es deliberado para
  la legalidad del convenio (`_recortar`: "No hay comprobación legal, y es deliberado" — ahí habla
  de C4/C5/C6 sobre un traspaso de ciclo heredado, un caso distinto), pero nadie había puesto esta
  regla nueva de reparto en su radar.
- Por la filosofía del proyecto ("legal se aplica donde el pipeline INVENTA una secuencia"), esto
  sí debería estar sujeto a la regla: **qué día se cede** es una decisión que toma el pipeline en
  el Paso B, no algo heredado intacto del patrón.

### La corrección: filtrar en `candidatas()`

`candidatas()` (`libranzas.py:143-165`) es la única función que genera las unidades cedibles, tanto
para la rama de exceso de `fase1` (plazas designadas) como para `fase2` (el resto) — filtrar ahí
cubre ambos caminos sin sesgo por fase ni por tipo de trabajador.

Nueva función `_huerfano_domingo`: dado un conjunto de días que se cederían juntos, ¿deja huérfano
algún domingo que el titular seguiría trabajando? Un día `f` lo deja huérfano si el día siguiente
(`f + 1`) tiene turno asignado en `plan`, ese día siguiente NO está también en el conjunto que se
cede (si lo estuviera, se ceden ambos juntos: no hay orfandad), y `tipo_dia` de ese día siguiente es
`"DOM"`. No se exige que `f` mismo sea `tipo_dia == "SAB"` — igual que la Task 3 tuvo que corregir,
un sábado festivo (`tipo_dia` devuelve `"FEST"`) debe seguir contando como "sábado trabajado" si el
titular lo trabaja; el criterio correcto es puramente posicional (el día natural anterior), no por
etiqueta de tipo.

Aplica igual a las plazas rígidas (ciclo entero) que a las flexibles (día suelto): en las rígidas,
si el bloque cedido incluye el sábado y domingo consecutivos, ambos se quitan juntos y
`_huerfano_domingo` no los marca (están en el mismo conjunto); solo protege el caso donde el
sábado se cede solo y el domingo del titular queda huérfano — que es exactamente el patrón
confirmado en los 22 casos nuevos, todos plazas flexibles cediendo día a día.

Si excluir una unidad dado dejaría a un titular sin nada que ceder ese ciclo, el camino ya existente
(`fase1`/`fase2` imprimen un aviso y siguen: "no le queda nada que ceder") absorbe el caso sin
cambios — es el mismo compromiso que ya aceptan las Tasks 2-4: coherente con «Consecuencias
esperadas» de este documento (preferible a romper la regla).

Ver Task 6 en `docs/superpowers/plans/2026-08-25-domingo-sin-sabado.md` para la implementación.

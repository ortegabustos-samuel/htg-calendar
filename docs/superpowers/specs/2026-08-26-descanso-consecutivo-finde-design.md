# Descanso consecutivo tras un fin de semana completo

Fecha: 2026-08-26 · Estado: Tasks 1-7 implementadas y revisadas; la Task 8 (verificación
end-to-end) encontró tres mecanismos de `residuo.py` sin cubrir — ver «Addendum» al final.

## El problema

Regla de negocio nueva, no derivada del convenio: si un trabajador hace sábado Y domingo de la
misma semana ISO, esa semana debe tener un par de días **consecutivos** libres entre semana
(lunes a viernes) — no vale con tener dos días sueltos no consecutivos.

Es configurable: `config.toml` puede fijar qué dos días concretos de lunes a viernes deben ser
ese par (algún día la empresa quiere estandarizarlo). Con el parámetro vacío — el caso de este
dataset — vale cualquier par adyacente (lunes-martes, martes-miércoles, miércoles-jueves o
jueves-viernes); lo único obligatorio es que sean consecutivos.

**A quién aplica** (decidido con el usuario, sesión de brainstorming):

- **Mixtos y correturnos**: sí, siempre — el pipeline decide activamente sus turnos.
- **Patrones "rígidos"** (`ritmo.rigido`, ratio descanso/trabajo ≥ `ratio_rigido`: UVI_PRIV,
  UVI_VAL, VAL_NOCHES, VAL_NOCHES2) — **exentos por naturaleza de su patrón**. Su ciclo es
  trabajo-en-bloque/descanso-en-bloque (p.ej. el binomio de noche: 7 días de trabajo, 7 de
  descanso), y el concepto de "un par de días sueltos entre semana" no existe en esa estructura.
  Esto no es una excepción por *tipo* de trabajador — es la misma frontera estructural que ya usa
  `libranzas.candidatas()` para decidir quién cede el ciclo entero y quién cede día a día.
- **Patrones "flexibles"** (el resto: PAT_GRANDE_VALL, PAT_ISCAR, PAT_MAYORGA, PAT_MEDINA,
  PAT_TORDESILLAS) — sí deben cumplirla, pero **solo donde el pipeline inventa algo**: su matriz
  original de `patrones.csv` no se audita (igual que el resto de reglas legales — "los patrones se
  dan por válidos como están", `CLAUDE.md`). Si una semana ya viene mal formada del propio patrón
  y nadie la toca, se tolera. Si el pipeline decide algo sobre esa semana (le cede un día en el
  Paso B, o un intercambio de equidad la modifica), el resultado sí debe cumplir la regla.

**Fuerza de la regla, a diferencia de `domingo_ok`**: aquí no basta con "no rompas lo que había"
— es una obligación positiva. Si el pipeline toca una semana de sábado+domingo trabajado y no
tiene el par consecutivo, hay que **crearlo**, aunque cueste una cesión de más de lo que pediría
solo el ajuste de horas. Es el mismo compromiso que ya acepta el proyecto ("esa holgura la
aprovechan los pasos C y D").

**Diferencia estructural con `domingo_ok`**: `domingo_ok` es un predicado de UN día contra el
anterior, así que se pudo plegar en `permite()` y evaluarse incrementalmente. Esta regla depende
de la semana ISO completa (¿hay un par consecutivo libre en cinco días?), así que no se puede
evaluar a medida que se van decidiendo turnos uno a uno — se evalúa **después** de que una semana
queda decidida, igual que ya hace `_semana_respeta_domingo` en `equidad.py`.

## Qué se cambia

### 1. `config.toml` / `cargar_datos.py` — parámetro `dias_descanso_finde`

```toml
[reparto]
# Regla de reparto (no del convenio, como domingo_ok): si se trabajan sábado y domingo de la
# misma semana ISO, hace falta un par de días consecutivos libres entre semana (lunes-viernes).
# Vacío = libre elección de qué par, con tal de que sean consecutivos. Con dos días en español,
# consecutivos y de lunes a viernes ("martes", "miercoles"), fija exactamente cuáles.
dias_descanso_finde = []
```

En `Config` (`cargar_datos.py`):

```python
dias_descanso_finde: tuple[str, ...] = ()   # p.ej. ("martes", "miercoles"); vacío = libre elección
```

`_cargar_config()` no necesita cambios: ya acepta cualquier campo declarado en `Config` desde
cualquier sección del TOML.

### 2. `legal.py` — nueva función `descanso_finde_ok`

No se pliega en `permite()` (ver «Diferencia estructural» arriba). Predicado de semana completa,
mismo estilo que ya usa `equidad._semana_respeta_domingo` pero sin necesitar un bucle envolvente,
porque aquí la unidad natural YA es la semana:

```python
DIAS_LV = ("lunes", "martes", "miercoles", "jueves", "viernes")


def descanso_finde_ok(datos: Datos, plan: Plan, trabajador_id: str, lunes: date) -> bool:
    """Si esa semana ISO (`lunes`..`lunes+6`) se trabajan sábado Y domingo, exige un par de días
    consecutivos libres entre semana: el que fije config.dias_descanso_finde si está fijado, o
    cualquier par adyacente si no. No es del convenio: es una regla de reparto, como domingo_ok."""
    dias = [lunes + timedelta(days=i) for i in range(7)]
    if (trabajador_id, dias[5]) not in plan or (trabajador_id, dias[6]) not in plan:
        return True
    libres = {d for d in dias[:5] if (trabajador_id, d) not in plan}
    fijos = datos.config.dias_descanso_finde
    if fijos:
        idx = {nombre: i for i, nombre in enumerate(DIAS_LV)}
        return all(dias[idx[nombre]] in libres for nombre in fijos)
    return any(dias[i] in libres and dias[i + 1] in libres for i in range(4))
```

Se consume desde `equidad.py` (sección 6) y desde la auditoría (sección 7). No hace falta que
`base.py`/`libranzas.py` la llamen directamente: sus mecanismos son de construcción proactiva (ver
secciones 4 y 5), aunque conviene usarla también ahí para el chequeo final de cada intento.

### 3. `residuo.py` — restricción dura en el CP-SAT del pool, al nivel de C4/C5/C6/domingo_ok

Reutiliza el `vars_por_fecha` (por fecha, SIN filtrar por tipo — la lección de la Task 3 de
`domingo_ok` aplica igual aquí: "día libre" no distingue si esa fecha es festivo, laborable, etc.,
solo si hay algún turno `x` activo) que ya construye el bloque de `domingo_ok`. Para cada
correturno `w` y cada semana (`lunes`) donde tiene variables candidatas:

```python
for w in pool:
    ...  # (bloque ya existente de domingo_ok, construye vars_por_fecha sin filtrar tipo)
    for lunes in {forma.lunes_de(f) for f, s in por_trab[w]}:  # semanas ISO con alguna candidata
        dias = [lunes + timedelta(days=i) for i in range(7)]
        sab_vars = vars_por_fecha.get(dias[5], [])
        dom_vars = vars_por_fecha.get(dias[6], [])
        if not sab_vars or not dom_vars:
            continue                                   # esta semana no tiene ambos días en juego
        ambos = modelo.NewBoolVar(f"ambosfinde_{w}_{lunes:%m%d}")
        modelo.Add(ambos >= sum(sab_vars) + sum(dom_vars) - 1)

        libres = []
        for i in range(5):
            dia_vars = vars_por_fecha.get(dias[i], [])
            libre = modelo.NewBoolVar(f"libre_{w}_{dias[i]:%m%d}")
            modelo.Add(sum(dia_vars) + libre == 1)      # como mucho un turno/día (C2) ya lo permite
            libres.append(libre)

        fijos = datos.config.dias_descanso_finde
        if fijos:
            idx = {nombre: i for i, nombre in enumerate(DIAS_LV)}
            i1, i2 = sorted(idx[n] for n in fijos)
            modelo.Add(libres[i1] + libres[i2] >= 2 * ambos)
        else:
            pares = []
            for i in range(4):
                par = modelo.NewBoolVar(f"par_{w}_{dias[i]:%m%d}")
                modelo.Add(par <= libres[i])
                modelo.Add(par <= libres[i + 1])
                pares.append(par)
            modelo.Add(sum(pares) >= ambos)
```

**Riesgo conocido de antemano** (por el spike de rendimiento de ayer): esto añade del orden de
10 variables/restricciones auxiliares por trabajador del pool por semana — pequeño en número,
pero el nivel 1 ya demostró ser sensible a acoplamientos nuevos entre variables de días
distintos. Se documentará el impacto real medido, no una expectativa a priori (ver
«Consecuencias esperadas»).

**Punto a vigilar en la implementación** (no en el diseño): el bloque de `domingo_ok` (Task 3)
también agrupa por fecha dentro de este mismo `for w in pool:` — hay que decidir si esta nueva
restricción reutiliza literalmente el `vars_por_fecha` ya construido ahí (mismo bloque, una sola
pasada) o si construye el suyo propio; reutilizarlo es lo más DRY y evita divergencias entre las
dos reglas sobre qué cuenta como "ese día tiene un turno activo".

### 4. `base.py` (`colocar_mixtos`) — `_soltar_dia_lv` busca el día adyacente

Decisión tomada: opción A (secuencial), no decisión conjunta. Encaja con el orden ya existente
(`FINDE = ("SAB", "DOM", "FEST")`, se procesa TODO el año de `SAB` antes de pasar a `DOM`), así que
para cuando se concede un domingo, `plan` ya refleja los sábados concedidos ese año — incluido,
gracias al filtro de la Task 2 de `domingo_ok`, el de esa misma semana.

Cambio en `_soltar_dia_lv`: antes de aplicar el criterio "peor" de siempre, comprobar si esa misma
semana ya se soltó un día (por un finde anterior de la misma semana) y, si lo hay, preferir el día
**adyacente** a él entre los que sigue teniendo asignados (`suyos`):

```python
def _soltar_dia_lv(datos, plan, libro, cubiertas, trab, f):
    lunes = f - timedelta(days=f.weekday())
    suyos = [lunes + timedelta(days=i) for i in range(5) if (trab, lunes + timedelta(days=i)) in plan]
    if not suyos:
        return None
    ya_libre = [lunes + timedelta(days=i) for i in range(5)
                if (trab, lunes + timedelta(days=i)) not in plan]
    adyacentes = [d for d in suyos
                  if any(abs((d - libre).days) == 1 for libre in ya_libre)]
    candidatos = adyacentes or suyos          # si hay adyacente disponible, prioriza; si no, cae al criterio de siempre
    peor = max(candidatos, key=lambda g: sum(1 for (_, ss) in datos.capacidades if ss == plan[(trab, g)]))
    s = plan.pop((trab, peor))
    libro.borra(trab, s)
    cubiertas[(s, peor)] -= 1
    return peor, s
```

Si no hay ningún adyacente disponible (caso raro: vacaciones o festivo ocupan justo esa posición),
cae al criterio de siempre — sin garantía de par consecutivo esa semana. Igual que en la regla de
domingo, este no es el único punto de defensa: el CP-SAT (sección 3) y la guarda de equidad
(sección 6) actúan de red de seguridad para lo que decide el resto del pipeline, pero para un
mixto ya colocado en el Paso A2 no hay un mecanismo posterior que lo intente arreglar — queda
para que la auditoría lo señale si de verdad ocurre.

### 5. `libranzas.py` (patrones flexibles) — pase de reparación tras ceder

Nueva función `_forzar_descanso_finde(datos, plan, libro, reg, ritmos)`, llamada al final de
`ceder()` (después de `fase1` y `fase2`) — recibe `ritmos` como parámetro en vez de medirlo de
nuevo (ver «Nota transversal», al final de este apartado «Qué se cambia»):

```python
def _forzar_descanso_finde(datos, plan, libro, reg, ritmos):
    def lunes_de(f):
        return f - timedelta(days=f.weekday())

    tocadas = {(c.titular, lunes_de(f)) for c in reg.cesiones for f in c.dias}
    for titular in {c.titular for c in reg.cesiones}:
        if ritmos[ritmo_mod.grupo_de(datos, titular)].rigido:  # exento, ver «El problema»
            continue
        for lunes in semanas_con_findes_completos(datos, plan, titular):
            if legal.descanso_finde_ok(datos, plan, titular, lunes):
                continue
            if (titular, lunes) not in tocadas:
                continue                                  # no lo tocó el pipeline: se tolera
            # Busca UN día más que ceder, adyacente a un día ya libre esa semana, con el mismo
            # criterio de coste que ya usa _coste_fase2 (quién puede cubrirlo).
            ...  # candidato = día laborable trabajado, adyacente a un libre, mejor coste
            if candidato is None:
                print(f"  aviso  {titular} semana del {lunes:%d/%m}: sábado+domingo sin par "
                      f"consecutivo y nadie puede cubrir el día que lo completaría")
                continue
            libro.borra(titular, plan.pop((titular, candidato)))
            reg.cesiones.append(Cesion(fase=2, titular=titular, dias=[candidato], horas=...,
                                       cubridor=None, desalojadas=0, motivo="descanso de finde"))
```

Detalles a resolver en el plan de implementación (no bloquean el diseño): el criterio exacto de
"qué candidato adyacente elegir" reutiliza `_coste_fase2`/`_libres`, y `tocadas` debe construirse a
partir del registro real de cesiones de AMBAS fases (`fase1` también puede ceder de un titular con
plaza designada). El escaneo de "semanas con findes completos" es sobre el `plan` ya final, tras
`fase1`+`fase2`.

### 6. `equidad.py` — guarda en `pulir_dias`; `pulir` no se toca

**`pulir` (intercambio de semana ISO completa) no necesita cambios**, mismo argumento que con
`domingo_ok`: mueve la semana entera (qué días se trabajan, cuáles no) como unidad atómica de un
trabajador a otro — si esa forma concreta ya cumplía o no cumplía la regla, sigue exactamente
igual tras el intercambio, solo cambia de dueño.

**`pulir_dias` (día suelto) sí necesita guarda.** Como `descanso_finde_ok` ya evalúa la semana
completa, no hace falta un bucle envolvente como `_semana_respeta_domingo`: se llama directo.

```python
def _valido_dia(datos, plan, libro, a, da, b, db, pactadas, ritmos):
    ...
    lunes = _lunes(da)
    rompe_domingo = (...)                                 # ya existente
    rompe_descanso = (
        (not ritmo_mod.es_rigido(datos, ritmos, a)
         and not legal.descanso_finde_ok(datos, plan, a, lunes))
        or (not ritmo_mod.es_rigido(datos, ritmos, b)
            and not legal.descanso_finde_ok(datos, plan, b, lunes))
    )
    if rompe_domingo or rompe_descanso or _empeora(...):
        ...  # deshacer, igual que ahora
```

`ritmos` llega como parámetro (ver «Nota transversal»), no se remide aquí.

`ritmo.rigido` es la misma frontera que ya usa `libranzas.candidatas()` —
**no es un sesgo por tipo** en el sentido que se descartó para `domingo_ok` (ahí el criterio
rechazado era "eres patrón → exento", una etiqueta): aquí el criterio es "tu patrón es un ciclo de
bloque entero, donde el concepto de días sueltos entre semana no existe", un hecho estructural del
ritmo, igual de aplicable a un `mixto` o `correturno` si algún día tuviera esa forma (hoy no la
tienen, por eso solo importa para patrón).

`pulir_dias` agrupa hoy por `grupo_de()` para todos los tipos por igual — no hace falta cambiar
eso, solo la guarda dentro de `_valido_dia`.

### 7. `legal.integridad()` / `auditar()` — auditoría con el mismo patrón de línea base tolerada

Aplicando la lección de la revisión final de `domingo_ok` (el fix de "domingos heredados del
esqueleto"): no vale con "avisar si falla" sin distinguir lo tolerado de lo nuevo, o la auditoría
queda roja para siempre.

- Los grupos **rígidos** quedan completamente fuera del chequeo — ni se evalúan ni cuentan como
  tolerados, para ellos no aplica, punto.
- Para el resto, `integridad()` gana un bucle nuevo (por trabajador × semana ISO, no por
  asignación individual como los demás chequeos de esta función):
  ```python
  for w in trabajadores_con_semanas_a_revisar:          # mixto, correturno, patrón flexible
      for lunes in semanas_de(w, plan):
          if not descanso_finde_ok(datos, plan, w, lunes):
              fallos.append(f"{w} semana del {lunes:%d/%m}: sábado y domingo sin un par de días "
                            f"consecutivos libres entre semana")
  ```
- En `pipeline.py`, igual que `domingos_esqueleto`, se calcula una vez tras `base.construir()` el
  conjunto de semanas de patrón flexible que **ya** incumplen esto en el esqueleto puro
  (`descansos_esqueleto`), y se pasa a `auditar()`. Solo las violaciones que NO estén en ese
  conjunto (mixto/correturno siempre; patrón flexible si la semana venía bien y algo la rompió)
  cuentan como FALLOS reales — el resto se reporta como tolerado, mismo formato que ya se dejó
  para `domingos_esqueleto`.

### 8. `validar_datos.py` — validación del parámetro nuevo

Sección 0 (CONFIG) gana una comprobación: `dias_descanso_finde` debe ser una lista vacía, o
exactamente 2 nombres de `DIAS_LV` distintos y consecutivos (índices consecutivos en
`("lunes","martes","miercoles","jueves","viernes")`). Cualquier otra cosa (1 día, 3+, nombres no
reconocidos, no consecutivos) es un error de validación, mismo estilo que las comprobaciones ya
existentes de `descanso_minimo`/`horas_max_semana`/`ratio_rigido`.

### Nota transversal — `ritmos` se calcula una sola vez, en `pipeline.py`

Tres sitios distintos necesitan saber si el grupo de un trabajador es rígido o flexible:
`libranzas.ceder` (ya lo mide hoy, al principio de la función), la guarda nueva de
`equidad.pulir`/`pulir_dias` (sección 6), y la auditoría de `legal.integridad`/`auditar` (sección
7). Medirlo tres veces por separado —y en distintos momentos del pipeline, sobre un `plan` cada vez
más mutado— arriesga que la rigidez de un grupo "varíe" según cuándo se mida, cuando en realidad es
una propiedad estructural fija del patrón (ritmo descanso/trabajo de su matriz en `patrones.csv`).

Se calcula **una sola vez**, en `pipeline.py`, en el mismo punto donde `libranzas.ceder()` ya la
mide hoy internamente: justo después de `colocar_mixtos` (Paso A2), no justo tras
`base.construir()` — a esa altura los mixtos ya tienen su línea L-V y su cuota de finde en `plan`,
así que su grupo ("mixto") sí entra en la medición; justo tras el Paso A puro no tendrían ninguna
asignación todavía y `ritmos["mixto"]` ni existiría.

```python
ritmos = ritmo_mod.medir(datos, plan)          # plan = salida de base.construir() + colocar_mixtos
```

y se pasa como parámetro a `libranzas.ceder(datos, plan, libro, protegidos, ritmos)` (sustituyendo
su medición interna actual, que hacía exactamente esto en el mismo punto), a
`equidad.pulir(datos, plan, libro, ritmos=ritmos)` y a
`legal.auditar(datos, plan, pactadas, domingos_esqueleto, descansos_esqueleto, ritmos)`. Mismo
patrón que ya se siguió con `pactadas_esqueleto`/`domingos_esqueleto`: una medición de referencia
temprana, threaded a través de las funciones que la necesitan, en vez de recalculada por cada una.

**El correturno sigue sin tener grupo medible** (cero asignaciones hasta el paso D): cualquier
lookup `ritmos[grupo]` debe tratarlo como "no rígido" en vez de reventar con `KeyError` — nunca es
rígido de todas formas, por decisión ya tomada ("mixtos y correturnos: sí, siempre"). Para no
repetir ese `.get(...)` defensivo en tres archivos, `ritmo.py` gana un helper:

```python
def es_rigido(datos: Datos, ritmos: dict[str, Ritmo], trab: str) -> bool:
    """Como ritmos[grupo].rigido, pero seguro para un grupo sin medición (p.ej. correturno antes
    del paso D): sin datos, se trata como flexible — nunca es rígido de todas formas."""
    r = ritmos.get(grupo_de(datos, trab))
    return bool(r and r.rigido)
```

`libranzas.py` (que ya mide `ritmos` en el mismo punto) puede seguir indexando directamente para
sus propios titulares (siempre de patrón, con grupo medible); `equidad.py` (secciones 5-6, que
opera sobre cualquier tipo) y `legal.py` (auditoría, sección 7) deben usar `ritmo.es_rigido()`.

`pulir()` ya tiene hoy un `pactadas: set | None = None` con fallback a
`legal.pactadas(datos, base.construir(datos))` cuando se llama sin ese argumento (p.ej. desde un
guion de verificación suelto). `ritmos` gana el mismo `ritmos: dict | None = None` con fallback a
`ritmo_mod.medir(datos, base.construir(datos))`, y `pulir()` se lo pasa a `pulir_dias()` igual que
ya hace con `pactadas` — así cualquier guion que llame a `pulir_dias`/`_valido_dia` directamente
(como ya hizo el plan de `domingo_ok` en su Task 4) sigue funcionando sin tener que construir
`ritmos` a mano primero.

## Consecuencias esperadas

- **Rendimiento del CP-SAT — medido (Task 16, spike A/B aislado, dos réplicas con orden invertido
  para descartar ruido de máquina)**: al contrario de lo que el precedente de `domingo_ok` hacía
  temer, esta restricción no penaliza el nivel 1 — lo mejora. **CON** la restricción: nivel 1
  llega a **OPTIMAL** en 116s y 203s (dos réplicas) con cobertura 2661. **SIN** ella: el solver
  agota el tope de 300s y se queda en **FEASIBLE** (no probado óptimo) con cobertura 2664 — solo
  3 plazas más (0,1%) de las ~26.000 en juego, y sin garantía de que sea el verdadero óptimo. La
  explicación más plausible: la restricción poda simetría del espacio de soluciones (menos formas
  equivalentes de repartir sábado+domingo entre el pool), lo que le facilita al solver CERRAR el
  hueco de optimalidad — el efecto contrario al de `domingo_ok`, que añadía complejidad sin podar
  nada. No hace falta subir el tope de segundos ni buscar una formulación más barata: el plan se da
  por cerrado en este punto sin ajustes adicionales.
- **Cobertura y horas**: forzar un par consecutivo cuando no existe puede dejar a algún patrón
  flexible por debajo de su objetivo de horas más de lo estrictamente necesario (sección 5), y
  puede reducir ligeramente la cuota de fin de semana de algún mixto si `_soltar_dia_lv` no
  encuentra nunca un adyacente disponible. Aceptado, mismo compromiso que ya rige en el proyecto.
- **Combinación con `domingo_ok`**: ambas reglas actúan sobre las mismas semanas de sábado+domingo
  trabajado. No deberían interferir entre sí (una mira si domingo tiene su sábado; la otra mira si
  hay un par libre entre semana), pero conviene verificarlo explícitamente en la Tarea de
  verificación end-to-end — el propio `domingo_ok` nos enseñó que un sexto mecanismo puede aparecer
  donde no se esperaba.

## Verificación

Mismo método que la regla anterior: no hay tests, la verificación es la salida.

1. Por cada mecanismo (2-8), guion de verificación aislado con datos reales antes de tocar el
   pipeline completo — mismo patrón que ya siguió el plan de `domingo_ok`.
2. Pipeline completo end-to-end, comprobando `legal.auditar` limpio (o solo con lo tolerado) y
   comparando cobertura/cuotas de findes/tiempos de CP-SAT contra la referencia post-`domingo_ok`
   (`pipeline_task7.log`).
3. Spike de rendimiento del CP-SAT (A/B con y sin esta restricción, mismo método que el de ayer)
   antes de considerar el plan cerrado — no se documenta como "sin impacto" a priori.

## Addendum (2026-08-26): tres mecanismos de `residuo.py` sin cubrir

La Task 8 (verificación end-to-end, tras implementar y revisar las Tasks 1-7) ejecutó el pipeline
completo y encontró **130 semanas nuevas** sin par de días consecutivos libres — el invariante no
se sostiene. Diagnóstico completo en
`.superpowers/sdd/2026-08-26-descanso-consecutivo-finde/task-8-report.md`; resumen:

- 0 de las 130 estaban en `descansos_esqueleto` (el esqueleto puro, tras `base.construir()`): las
  130 son nuevas, introducidas por el pipeline. 0 casos en grupos rígidos: la exención estructural
  funciona bien donde se aplicó.
- Reparto: correturno 103 (los 11 del pool), mixto 19 (4 de 6), patrón flexible 8 (una semana cada
  uno). El patrón de reparto —el pool entero, no casos aislados— apunta a un fallo sistémico de
  cobertura, no a un caso raro.
- Causa raíz, por inspección de código: la Task 3 (residuo.py) implementó la restricción dura solo
  para las variables `x` (asignación directa del pool a huecos reales). Tres mecanismos más, todos
  en el mismo archivo, **nunca comprueban `descanso_finde_ok`**:
  1. `canjear_refuerzos` (`residuo.py:444-508`) y `_cadena`/`canjear_en_cadena`
     (`residuo.py:511-587`) — corren DESPUÉS del modelo, iteran sobre `datos.trabajadores`
     completo (no solo el pool) y asignan un día nuevo (`plan[(w, f)] = s`) a cualquier trabajador,
     sábado o domingo incluido. Llevan un comentario explícito razonando que son "seguros frente
     al domingo-sin-sábado" porque el REF CAL que sueltan es siempre `lv=1` — cierto para
     `domingo_ok`, pero **ese razonamiento no se traslada a esta regla**: soltar o rellenar un día
     entre semana es precisamente el mecanismo que crea o destruye el par consecutivo libre.
  2. `rellenar_refuerzos` (`residuo.py:593-638`) — solo coloca turnos L-V, así que no puede crear
     un sábado+domingo nuevo, pero sí puede ocupar exactamente el día que formaba el único par
     consecutivo libre de una semana ya en regla, rompiéndolo. Usa `legal.permite`, que no incluye
     `descanso_finde_ok` (por diseño: es de semana completa, no de un día).
  3. Las variables `z` del CP-SAT (semana flexible del mixto, `residuo.py:153-174`) — el modelo
     está obligado a reactivar exactamente `cuantos` días de los que el paso A2 le soltó al mixto
     (`modelo.Add(sum(suyas) == min(cuantos, len(suyas)))`), pero es libre de elegir CUÁLES,
     siempre que respete C4. Puede reactivar un día distinto al que la Task 4 (base.py) eligió
     deliberadamente adyacente, dejando el par consecutivo roto aunque el conteo total de días
     libres no cambie.
- Las variables `y` (canje de un REF CAL ya en plan por una plaza real del mismo día) se
  analizaron y se confirma que **no necesitan arreglo**: solo aplican a un día que el trabajador
  YA tenía ocupado (con REF CAL) antes de que el modelo corra, y cambian el turno, nunca si ese
  día está trabajado o libre — el mismo razonamiento que ya las eximía para `domingo_ok` se
  traslada intacto aquí, porque esta regla también es puramente sobre "trabajado vs. libre", no
  sobre qué turno concreto.

### La corrección

Ver Tasks 10-11 en `docs/superpowers/plans/2026-08-26-descanso-consecutivo-finde.md`:

- **Task 10** — guarda `canjear_refuerzos`, `_cadena` y `rellenar_refuerzos` con
  `legal.descanso_finde_ok`, en el mismo punto donde ya se comprueba `legal.permite`: si añadir el
  día nuevo rompe la regla para la semana del trabajador afectado, se deshace ese intento concreto
  y se sigue con el siguiente candidato — mismo patrón de "aplicar, comprobar, revertir" que ya usa
  `equidad._valido_dia`. Ceder un día (nunca ocurre en estas tres funciones más que como pago,
  nunca como resultado final) no necesita comprobación: liberar un día no puede romper la regla,
  solo puede ayudar o ser neutro (ver «El problema» del spec original de esta misma regla).
- **Task 11** — para las variables `z`, una restricción dura análoga a la de la Task 3 pero
  aplicada a la semana flexible del mixto: si esa semana ya tiene sábado Y domingo fijados en
  `plan` (decidido en el Paso A2, invariable en este punto), se construyen variables `libre` por
  cada día L-V de esa semana (reificadas contra `z` para los días candidatos, constantes para los
  días fijos fuera de `candidatos`) y se exige el mismo par consecutivo (o el fijo de
  `config.dias_descanso_finde`) que ya exige la Task 3 para el pool.

Ambas se verifican con la Task 12 (re-verificación end-to-end), repitiendo el método de la Task 8.

## Addendum 2 (2026-08-26): séptimo mecanismo — `libranzas.ceder`, fase 1, el cubridor

La Task 12 (re-verificación tras las Tasks 10-11) bajó las violaciones de 130 a **2**, ambas
`tipo=patron`. Diagnóstico completo en
`.superpowers/sdd/2026-08-26-descanso-consecutivo-finde/task-12-report.md`; resumen:

- Las 2 ya estaban en la lista original de 130 de la Task 8 — no las introdujo nada de las
  Tasks 10-11 (que solo tocan `residuo.py`; estas 2 nacen en `libranzas.ceder`, fase 1).
- Causa raíz: en `_aplicar_fase1` (`libranzas.py:346-365`), cuando un cubridor de plaza designada
  asume el ciclo de un titular ausente/que cede, se le desalojan sus propios días que chocan con
  la ventana asumida (`libro.borra(cubridor, plan.pop((cubridor, f)))`). El `Cesion` que queda
  registra `titular` (quien cede) pero **nunca `cubridor`** como sujeto a revisar:
  `_forzar_descanso_finde` (Task 5, `libranzas.py:570-571`) construye `tocadas` y su bucle
  exterior indexando solo por `c.titular`. Si el cubridor pierde un día entre semana que formaba
  su único par consecutivo mientras gana un fin de semana ajeno, su propia semana rota nunca se
  comprueba ni se repara — es él quien la tiene rota, no el titular.
- Es una omisión de alcance de la propia Task 5 (nunca probó el camino de fase 1 con traspaso
  real), no un defecto de las Tasks 10-11, que no tocan `libranzas.py`.

### La corrección

Ver Task 14 en el plan: `_forzar_descanso_finde` amplía `tocadas` y su bucle exterior para incluir
también `(c.cubridor, lunes_de(f))` por cada `f in c.dias`, además de `(c.titular, lunes_de(f))`
— mismo criterio, misma lista de fechas ya disponible en el `Cesion`, sin cambiar el modelo de
datos. Se verifica con la Task 15 (tercera y última re-verificación end-to-end de este plan).

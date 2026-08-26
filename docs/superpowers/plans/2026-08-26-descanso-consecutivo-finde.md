# Descanso consecutivo tras un fin de semana completo: implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** si un trabajador hace sábado y domingo de la misma semana ISO, esa semana debe tener un
par de días consecutivos libres entre semana (lunes-viernes) — configurable (`config.toml`), con
los grupos de patrón rígidos (ciclo entero) exentos por estructura, y solo forzado en los patrones
flexibles donde el pipeline ya decide algo (nunca auditando su matriz original heredada).

**Architecture:** un predicado nuevo de SEMANA completa en `legal.py` (`descanso_finde_ok`, a
diferencia de `domingo_ok` no se pliega en `permite()` porque depende de los cinco días laborables
a la vez, no de un solo día contra el anterior). Se aplica en los cinco mecanismos que deciden
sábado/domingo: `base.colocar_mixtos` (proactivo, elige el día adyacente), `residuo.resolver`
(restricción dura del CP-SAT, con variables auxiliares de "día libre"), `libranzas.ceder` (pase de
reparación tras ceder, solo en semanas que el pipeline tocó), `equidad.pulir_dias` (guarda,
`equidad.pulir` no necesita cambios) y la auditoría de `legal.integridad`/`auditar` (con línea base
tolerada para lo heredado del esqueleto, mismo patrón que ya se usó para `domingo_ok`). La rigidez
del grupo de cada trabajador (`ritmo.rigido`) se mide una sola vez en `pipeline.py` y se pasa como
parámetro a quien la necesite.

**Tech Stack:** Python 3, OR-Tools CP-SAT. Sin framework de tests — el proyecto verifica ejecutando
y auditando la salida (`CLAUDE.md`). Cada tarea lleva su propio script de verificación en Python,
ejecutado con `python3 -c` contra los datos reales de `data/input/`.

**Spec:** `docs/superpowers/specs/2026-08-26-descanso-consecutivo-finde-design.md`

## Global Constraints

- **Sin sesgo por tipo de trabajador.** El criterio de exención es estructural (¿el grupo de
  patrón es rígido, `ritmo.rigido`?), nunca "eres de tipo X". Mixtos y correturnos están siempre
  sujetos a la regla.
- **A diferencia de `domingo_ok`, esta regla NO necesita excepción de festivos.** El predicado solo
  mira si hay algún turno asignado ese día (`(trabajador_id, fecha) in plan`), sin distinguir
  `tipo_dia`; un festivo trabajado entre semana cuenta igual que un laborable trabajado: ocupa el
  día, no lo libera.
- **Patrones flexibles: solo se fuerza donde el pipeline decide algo.** Su matriz original de
  `patrones.csv` nunca se audita ni se corrige — igual que el resto de reglas legales. Solo si
  `libranzas.ceder` o `equidad` tocan esa semana concreta, el resultado debe cumplir la regla.
- **Patrones rígidos (`ritmo.rigido`, ratio ≥ `config.ratio_rigido`): exentos siempre**, ni se
  comprueban ni cuentan como "tolerados" — para ellos el concepto no aplica, punto.
- **`ritmos` se mide una sola vez**, en `pipeline.py`, justo después de `colocar_mixtos` (Paso A2)
  — no justo tras `base.construir()`, porque los mixtos no tienen ninguna asignación todavía en
  ese punto y su grupo ni existiría en el diccionario. Se pasa como parámetro a
  `libranzas.ceder`, `equidad.pulir` y `legal.auditar`, en vez de remedirse en cada uno.
- **El correturno no tiene grupo medible** (cero asignaciones hasta el paso D): cualquier consulta
  de rigidez debe usar `ritmo.es_rigido(datos, ritmos, trab)` (que trata "sin medición" como "no
  rígido" — nunca es rígido de todas formas), nunca indexar `ritmos[grupo]` directamente salvo que
  el trabajador sea garantizadamente de patrón (como en `libranzas.py`, que solo procesa titulares
  de patrón).
- **`config.dias_descanso_finde`**: lista vacía (libre elección de qué par, con tal de que sean
  consecutivos) o exactamente 2 nombres de `DIAS_LV = ("lunes", "martes", "miercoles", "jueves",
  "viernes")`, distintos y de índice consecutivo. En los datos de este proyecto queda vacía.
- Todos los comandos de verificación se ejecutan desde la raíz del repo
  (`/home/samu/Documents/Universidad/HT-GROUP`) con el entorno conda `ortools_env` activado y
  `PYTHONPATH=src`.

---

### Task 1: `cargar_datos.py` / `config.toml` / `validar_datos.py` — parámetro `dias_descanso_finde`

**Files:**
- Modify: `src/cargar_datos.py:59-70` (`Config`, y una constante nueva `DIAS_LV` justo antes)
- Modify: `data/input/config.toml`
- Modify: `src/validar_datos.py:85-106` (`revisar_config`)

**Interfaces:**
- Produces: `cargar_datos.DIAS_LV: tuple[str, ...]` — orden canónico de los cinco días laborables,
  usado por las Tasks 2, 3 y 8. `Config.dias_descanso_finde: tuple[str, ...]` — usado por las
  Tasks 2 y 3.

- [ ] **Step 1: Escribir el script de verificación (debe fallar: el campo no existe)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from cargar_datos import cargar, DIAS_LV
datos = cargar()
assert DIAS_LV == ('lunes', 'martes', 'miercoles', 'jueves', 'viernes'), DIAS_LV
assert datos.config.dias_descanso_finde == (), datos.config.dias_descanso_finde
print('Config.dias_descanso_finde por defecto vacío — OK')
"
```

Expected: `ImportError: cannot import name 'DIAS_LV' from 'cargar_datos'`

- [ ] **Step 2: Añadir `DIAS_LV` y el campo nuevo a `Config` (líneas 57-70 actuales)**

Antes:
```python
@dataclass(frozen=True)             #El uso de forzen impide que se modifique el propio objeto Config (logico la configuracion
                                    # no deberia modificarse)
class Config:
    """
    Parámetros de la INSTANCIA (`config.toml`): qué año se resuelve y bajo qué convenio.
    `anio` es obligatorio en config.toml: el horizonte lo declaran los datos, no se deduce.
    """
    anio: int                    # Anio sobre el que estamos haciendo el calendario
    horas_objetivo: int = 1776   # jornada anual objetivo (h): techo de todo lo que no sea cubrir
    descanso_minimo: int = 12               # descanso mínimo entre jornadas (h)              -> C4
    horas_max_semana: int = 48              # máx. horas en cualquier ventana de 7 días       -> C6
    dias_max_semana: int = 6                # máx. días trabajados por semana ISO             -> C5
    ratio_rigido: float = 0.6               # Parametro a priori que permite saber como gestionar algunos patrones 
                                            # como el caso de UVI y Noches (replantear si añadir a patron como param)
```

Después:
```python
DIAS_LV = ("lunes", "martes", "miercoles", "jueves", "viernes")   # orden canónico L-V


@dataclass(frozen=True)             #El uso de forzen impide que se modifique el propio objeto Config (logico la configuracion
                                    # no deberia modificarse)
class Config:
    """
    Parámetros de la INSTANCIA (`config.toml`): qué año se resuelve y bajo qué convenio.
    `anio` es obligatorio en config.toml: el horizonte lo declaran los datos, no se deduce.
    """
    anio: int                    # Anio sobre el que estamos haciendo el calendario
    horas_objetivo: int = 1776   # jornada anual objetivo (h): techo de todo lo que no sea cubrir
    descanso_minimo: int = 12               # descanso mínimo entre jornadas (h)              -> C4
    horas_max_semana: int = 48              # máx. horas en cualquier ventana de 7 días       -> C6
    dias_max_semana: int = 6                # máx. días trabajados por semana ISO             -> C5
    ratio_rigido: float = 0.6               # Parametro a priori que permite saber como gestionar algunos patrones 
                                            # como el caso de UVI y Noches (replantear si añadir a patron como param)
    dias_descanso_finde: tuple[str, ...] = ()   # regla de reparto (no convenio): si sáb+dom se
                                            # trabajan, exige este par consecutivo de DIAS_LV; vacío
                                            # = libre elección de qué par, con tal de que sean
                                            # consecutivos
```

- [ ] **Step 3: Añadir la sección al `config.toml`**

`data/input/config.toml` está en `.gitignore` (todo `/data/*` lo está, desde que `config.toml`
existe) — es un fichero de entrada más, igual que los CSV reales de la empresa: se edita en disco,
nunca se comittea. **No hacer `git add` de este archivo en el Step 8.**

Al final del archivo (`data/input/config.toml`), añadir:

```toml

[reparto]
# Regla de reparto (no del convenio, como domingo_ok): si se trabajan sábado y domingo de la
# misma semana ISO, hace falta un par de días consecutivos libres entre semana (lunes-viernes).
# Vacío = libre elección de qué par, con tal de que sean consecutivos. Con dos días en español,
# consecutivos y de lunes a viernes ("martes", "miercoles"), fija exactamente cuáles.
dias_descanso_finde = []
```

- [ ] **Step 4: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `Config.dias_descanso_finde por defecto vacío — OK`

- [ ] **Step 5: Escribir el script de verificación de la validación (debe fallar: no hay chequeo todavía)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from dataclasses import replace
from cargar_datos import cargar
import validar_datos as vd

datos = cargar()

class InformeFalso:
    def __init__(self):
        self.errores = []
    def error(self, msg):
        self.errores.append(msg)

# Monkeypatch: _cargar_config devuelve una Config con el campo que queremos probar.
def _con(valor):
    original = vd._cargar_config
    def fake():
        cfg = original()
        return replace(cfg, dias_descanso_finde=valor)
    vd._cargar_config = fake
    inf = InformeFalso()
    ok = vd.revisar_config(inf)
    vd._cargar_config = original
    return ok, inf.errores

for valor, debe_pasar in [
    ((), True),
    (('martes', 'miercoles'), True),
    (('miercoles', 'martes'), True),            # orden invertido, mismo par: sigue siendo válido
    (('lunes',), False),                       # solo 1 día
    (('lunes', 'miercoles'), False),            # no consecutivos
    (('lunes', 'lunes'), False),                # repetido, no son 2 días distintos
    (('sabado', 'domingo'), False),             # no son L-V
    (('lunes', 'martes', 'miercoles'), False),  # 3 días
]:
    ok, errores = _con(valor)
    assert ok == debe_pasar, f'{valor}: esperaba ok={debe_pasar}, salió ok={ok} errores={errores}'
print('revisar_config valida dias_descanso_finde — OK')
"
```

Expected: falla el caso `('lunes',)` (y los demás inválidos) porque hoy `revisar_config` los acepta
sin comprobar nada — el `assert` salta con `AssertionError`.

- [ ] **Step 6: Añadir la comprobación a `revisar_config` (líneas 85-106 actuales)**

Antes:
```python
    if not 0 < cfg.ratio_rigido <= 1:
        inf.error(f"config.toml: ratio_rigido={cfg.ratio_rigido} está fuera de (0, 1]: es una "
                  f"proporción de días de descanso por día de trabajo")
    return not inf.errores
```

Después:
```python
    if not 0 < cfg.ratio_rigido <= 1:
        inf.error(f"config.toml: ratio_rigido={cfg.ratio_rigido} está fuera de (0, 1]: es una "
                  f"proporción de días de descanso por día de trabajo")
    dias = cfg.dias_descanso_finde
    if dias:
        indices = sorted(DIAS_LV.index(d) for d in dias if d in DIAS_LV)
        valido = (len(dias) == 2 and len(set(dias)) == 2 and len(indices) == 2
                  and indices[1] - indices[0] == 1)
        if not valido:
            inf.error(f"config.toml: dias_descanso_finde={dias!r} debe ir vacío o tener "
                      f"exactamente 2 días de {DIAS_LV}, distintos y consecutivos")
    return not inf.errores
```

Necesita `DIAS_LV` importado — `validar_datos.py` ya importa `_cargar_config` desde
`cargar_datos`; añadir `DIAS_LV` a esa misma importación (verificar el `import` exacto en el
archivo real antes de editarlo).

- [ ] **Step 7: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 5.
Expected: `revisar_config valida dias_descanso_finde — OK`

- [ ] **Step 8: Commit**

```bash
git add src/cargar_datos.py src/validar_datos.py
git commit -m "$(cat <<'EOF'
Añade el parámetro dias_descanso_finde a config.toml

Regla de reparto nueva (no del convenio): vacío por defecto (libre
elección de qué par de días consecutivos, con tal de que lo sean); con
2 días de lunes a viernes, consecutivos, los fija. Validado en
validar_datos.py con el mismo estilo que ratio_rigido/horas_max_semana.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `legal.py` — `descanso_finde_ok`, `ritmo.es_rigido`, y las dos auditorías

**Files:**
- Modify: `src/ritmo.py` (nueva función `es_rigido`, tras `grupo_de`)
- Modify: `src/legal.py` (nueva función `descanso_finde_ok`, tras `domingo_ok`; `integridad()`
  gana un parámetro `ritmos` y un bucle nuevo; `auditar()` gana `descansos_esqueleto` y `ritmos`)

**Interfaces:**
- Consumes: `cargar_datos.DIAS_LV`, `Config.dias_descanso_finde` (Task 1); `ritmo.medir`,
  `ritmo.grupo_de`, `ritmo.Ritmo.rigido` (ya existen).
- Produces: `ritmo.es_rigido(datos, ritmos, trab) -> bool` — usado por las Tasks 5 y 6.
  `legal.descanso_finde_ok(datos, plan, trabajador_id, lunes) -> bool` — usado por las Tasks 5 y 6.
  `legal.integridad(datos, plan, ritmos=None) -> list[str]` y
  `legal.auditar(datos, plan, pactadas_esqueleto, domingos_esqueleto=None,
  descansos_esqueleto=None, ritmos=None) -> None` — firmas nuevas, usadas por la Task 7.

- [ ] **Step 1: Escribir el script de verificación de `ritmo.es_rigido` (debe fallar: no existe)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from cargar_datos import cargar
import ritmo

datos = cargar()
plan = {}                                    # plan vacío: ningún grupo tiene medición
ritmos = ritmo.medir(datos, plan)
assert ritmos == {}, ritmos
assert ritmo.es_rigido(datos, ritmos, '71225031B') is False, 'sin medición debe ser no-rígido'
print('ritmo.es_rigido: sin medición trata como no-rígido — OK')
"
```

Expected: `AttributeError: module 'ritmo' has no attribute 'es_rigido'`

- [ ] **Step 2: Añadir `es_rigido` tras `grupo_de` en `ritmo.py` (línea 63 actual)**

```python
def es_rigido(datos: Datos, ritmos: dict[str, Ritmo], trab: str) -> bool:
    """Como ritmos[grupo].rigido, pero seguro para un grupo sin medición (p.ej. correturno antes
    del paso D, sin ninguna asignación todavía): sin datos, se trata como flexible — nunca es
    rígido de todas formas."""
    r = ritmos.get(grupo_de(datos, trab))
    return bool(r and r.rigido)
```

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `ritmo.es_rigido: sin medición trata como no-rígido — OK`

- [ ] **Step 4: Escribir el script de verificación de `descanso_finde_ok` (debe fallar: no existe)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from dataclasses import replace
from cargar_datos import cargar
import legal

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM')
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

# 1. Solo domingo (sin sábado): no aplica, True (esto lo cubre domingo_ok, no esta regla).
assert legal.descanso_finde_ok(datos, {}, 'TEST', lunes) is True

# 2. Sábado+domingo trabajados, lunes y martes libres (par consecutivo): True.
plan = {('TEST', sabado): turno_finde, ('TEST', domingo): turno_finde,
        ('TEST', miercoles): turno_lv, ('TEST', jueves): turno_lv, ('TEST', viernes): turno_lv}
assert legal.descanso_finde_ok(datos, plan, 'TEST', lunes) is True

# 3. Sábado+domingo trabajados, libres lunes y miércoles (NO consecutivos): False.
plan = {('TEST', sabado): turno_finde, ('TEST', domingo): turno_finde,
        ('TEST', martes): turno_lv, ('TEST', jueves): turno_lv, ('TEST', viernes): turno_lv}
assert legal.descanso_finde_ok(datos, plan, 'TEST', lunes) is False

# 4. Con dias_descanso_finde fijado a (martes, miercoles): solo ese par vale, aunque haya otro
#    par consecutivo libre.
datos_fijo = replace(datos, config=replace(datos.config,
                     dias_descanso_finde=('martes', 'miercoles')))
plan = {('TEST', sabado): turno_finde, ('TEST', domingo): turno_finde,
        ('TEST', jueves): turno_lv, ('TEST', viernes): turno_lv}   # lunes,martes,miercoles libres
assert legal.descanso_finde_ok(datos_fijo, plan, 'TEST', lunes) is True   # martes+miercoles libres
plan2 = {('TEST', sabado): turno_finde, ('TEST', domingo): turno_finde,
         ('TEST', martes): turno_lv, ('TEST', viernes): turno_lv}  # lunes,miercoles,jueves libres
assert legal.descanso_finde_ok(datos_fijo, plan2, 'TEST', lunes) is False  # martes trabajado

print('descanso_finde_ok: libre/fijo, consecutivo/no consecutivo — OK')
"
```

Expected: `AttributeError: module 'legal' has no attribute 'descanso_finde_ok'`

- [ ] **Step 5: Añadir `descanso_finde_ok` tras `domingo_ok` en `legal.py` (línea 74 actual, tras el
  `return` de `domingo_ok`)**

```python
def descanso_finde_ok(datos: Datos, plan: Plan, trabajador_id: str, lunes: date) -> bool:
    """Si esa semana ISO (`lunes`..`lunes+6`) se trabajan sábado Y domingo, exige un par de días
    consecutivos libres entre semana: el que fije config.dias_descanso_finde si está fijado, o
    cualquier par adyacente si no. No es del convenio: es una regla de reparto, como domingo_ok.
    A diferencia de domingo_ok, no hace falta excepción de festivos: un festivo trabajado entre
    semana ocupa el día igual que un laborable."""
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

Necesita `DIAS_LV` importado desde `cargar_datos` (añadir a la importación existente
`from cargar_datos import Datos` → `from cargar_datos import Datos, DIAS_LV`).

- [ ] **Step 6: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 4.
Expected: `descanso_finde_ok: libre/fijo, consecutivo/no consecutivo — OK`

- [ ] **Step 7: Actualizar el docstring del módulo (líneas 1-17 actuales)**

Añadir, tras el párrafo que ya menciona `domingo_ok`:

```python
También vive aquí `descanso_finde_ok` — si sábado y domingo se trabajan la misma semana, exige un
par de días consecutivos libres entre semana. Tampoco es del convenio, y a diferencia de
domingo_ok no se pliega en permite(): depende de la semana completa, no de un día contra el
anterior, así que se evalúa una vez decidida la semana (ver equidad.py, libranzas.py).
```

- [ ] **Step 8: Escribir el script de verificación de `integridad()`/`auditar()` con el parámetro
  `ritmos` (debe fallar: `integridad` no acepta ese argumento todavía)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal, ritmo

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
w = '71225031B'                             # id real, correturno
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and datos.disponible(w, f) and datos.disponible(w, f - timedelta(days=1)))
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

# Semana sin par consecutivo: integridad() debe detectarlo (con ritmos vacío, correturno no es
# rígido por defecto — ver Task 2 Step 1-3). OJO: quitar un solo día de los 5 laborables NUNCA
# rompe todos los pares posibles (con 4 libres de 5, siempre queda alguna pareja adyacente) — hay
# que dejar TRES trabajados y dos libres NO consecutivos (lunes y jueves, a 3 días de distancia).
plan = {(w, sabado): turno_finde, (w, domingo): turno_finde, (w, martes): turno_lv,
        (w, miercoles): turno_lv, (w, viernes): turno_lv}
fallos = legal.integridad(datos, plan, ritmos={})
assert any('consecutivos libres' in f for f in fallos), fallos

# Con el par consecutivo (lunes+martes libres, resto trabajado): no debe aparecer.
plan_ok = {(w, sabado): turno_finde, (w, domingo): turno_finde,
           (w, miercoles): turno_lv, (w, jueves): turno_lv, (w, viernes): turno_lv}
fallos_ok = legal.integridad(datos, plan_ok, ritmos={})
assert not any('consecutivos libres' in f for f in fallos_ok), fallos_ok

# integridad() sin el argumento ritmos (default None) también debe funcionar (mide internamente).
fallos_default = legal.integridad(datos, plan)
assert any('consecutivos libres' in f for f in fallos_default), fallos_default

print('integridad() detecta la semana sin par consecutivo — OK')
"
```

Expected: `TypeError: integridad() got an unexpected keyword argument 'ritmos'`

- [ ] **Step 9: Añadir el parámetro y el bucle nuevo a `integridad()` (líneas 170-189 actuales)**

Antes:
```python
def integridad(datos: Datos, plan: Plan) -> list[str]:
    """Lo que haría el cuadrante inejecutable, al margen del convenio: alguien asignado estando de
    vacaciones, un turno en un día en que su línea no opera, alguien sin capacidad para la línea que
    hace, o más gente asignada que demanda tiene la plaza. Aquí nunca debería haber nada."""
    cuenta: dict[tuple[str, date], int] = {}
    fallos: list[str] = []
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
    for (s, f), n in cuenta.items():
        if datos.turnos[s].dem and n > datos.turnos[s].dem:
            fallos.append(f"{s} el {f:%d/%m}: {n} asignados para {datos.turnos[s].dem} de demanda")
    return fallos
```

Después:
```python
def integridad(datos: Datos, plan: Plan, ritmos: dict[str, "ritmo_mod.Ritmo"] | None = None) -> list[str]:
    """Lo que haría el cuadrante inejecutable, al margen del convenio: alguien asignado estando de
    vacaciones, un turno en un día en que su línea no opera, alguien sin capacidad para la línea que
    hace, o más gente asignada que demanda tiene la plaza. Aquí nunca debería haber nada."""
    cuenta: dict[tuple[str, date], int] = {}
    fallos: list[str] = []
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
    for (s, f), n in cuenta.items():
        if datos.turnos[s].dem and n > datos.turnos[s].dem:
            fallos.append(f"{s} el {f:%d/%m}: {n} asignados para {datos.turnos[s].dem} de demanda")

    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, plan)
    vistas: set[tuple[str, date]] = set()
    for (w, f) in plan:
        lunes = f - timedelta(days=f.weekday())
        if (w, lunes) in vistas or ritmo_mod.es_rigido(datos, ritmos, w):
            continue
        vistas.add((w, lunes))
        if not descanso_finde_ok(datos, plan, w, lunes):
            fallos.append(f"{w} semana del {lunes:%d/%m}: sábado y domingo sin un par de días "
                          f"consecutivos libres entre semana")
    return fallos
```

Necesita `import ritmo as ritmo_mod` al principio de `legal.py` (verificar que no exista ya y
añadirlo junto a las demás importaciones).

- [ ] **Step 10: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 8.
Expected: `integridad() detecta la semana sin par consecutivo — OK`

- [ ] **Step 11: Escribir el script de verificación de `auditar()` con la línea base tolerada
  (debe fallar: `auditar` no acepta los argumentos nuevos)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
w = '71225031B'
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and datos.disponible(w, f) and datos.disponible(w, f - timedelta(days=1)))
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

# Semana sin par consecutivo, YA en la línea base tolerada -> auditoría limpia (con extra). Mismo
# cuidado que en el Step 8: hacen falta 3 días trabajados entre semana (no 1) para dejar los dos
# libres (lunes, jueves) sin ser adyacentes.
plan = {(w, sabado): turno_finde, (w, domingo): turno_finde, (w, martes): turno_lv,
        (w, miercoles): turno_lv, (w, viernes): turno_lv}
legal.auditar(datos, plan, set(), descansos_esqueleto={(w, lunes)}, ritmos={})
print()

# Misma semana, SIN estar en la línea base -> debe contar como FALLO nuevo.
legal.auditar(datos, plan, set(), descansos_esqueleto=set(), ritmos={})
"
```

Expected: `TypeError: auditar() got an unexpected keyword argument 'descansos_esqueleto'`

- [ ] **Step 12: Añadir `descansos_esqueleto` y `ritmos` a `auditar()` (líneas 192-215 actuales)**

Antes:
```python
def auditar(datos: Datos, plan: Plan, pactadas_esqueleto: set,
            domingos_esqueleto: set[tuple[str, date]] | None = None) -> None:
    """El repaso legal que se imprime al cerrar cada ejecución.

    La legalidad NO se mide contando: se mide por FORMAS. Los patrones incumplen el convenio por
    acuerdo con los trabajadores, así que el cuadrante nace con más de mil incumplimientos que hay
    que respetar. Lo que importa no es el total, sino cuántos tienen una forma —un par de turnos
    seguidos, una semana ISO— que el esqueleto NO produce por su cuenta: esos se los ha inventado
    el pipeline, y son los únicos que hay que mirar.

    domingos_esqueleto son los domingos sueltos que ya trae el esqueleto puro (Paso A) — se
    toleran igual que las formas pactadas de los patrones y no cuentan como FALLOS nuevos.
    """
    domingos_esqueleto = domingos_esqueleto or set()
    rotos = integridad(datos, plan)
    domingo_actual = {(w, f) for (w, f), s in plan.items() if not domingo_ok(datos, plan, w, f, s)}
    heredados = domingo_actual & domingos_esqueleto
    nuevos_domingo = domingo_actual - domingos_esqueleto
    otros = [r for r in rotos if "sin el sábado" not in r]
    graves = len(otros) + len(nuevos_domingo)
    if graves == 0:
        extra = (f" ({len(heredados)} domingo(s) heredado(s) del esqueleto, tolerados)"
                 if heredados else "")
        print(f"\nAUDITORÍA — integridad: correcta{extra}")
    else:
        if otros:
            ejemplo = otros[0]
        else:
            w, f = next(iter(nuevos_domingo))
            ejemplo = f"{w} el {f:%d/%m}: domingo NUEVO sin el sábado de ese fin de semana"
        print(f"\nAUDITORÍA — integridad: *** {graves} FALLOS: {ejemplo} ***")
```

Después (solo cambia la firma y el cuerpo hasta el `print` del resumen; el resto de la función,
la parte de `formas`/`inventadas`, sigue igual):
```python
def auditar(datos: Datos, plan: Plan, pactadas_esqueleto: set,
            domingos_esqueleto: set[tuple[str, date]] | None = None,
            descansos_esqueleto: set[tuple[str, date]] | None = None,
            ritmos: dict[str, "ritmo_mod.Ritmo"] | None = None) -> None:
    """El repaso legal que se imprime al cerrar cada ejecución.

    La legalidad NO se mide contando: se mide por FORMAS. Los patrones incumplen el convenio por
    acuerdo con los trabajadores, así que el cuadrante nace con más de mil incumplimientos que hay
    que respetar. Lo que importa no es el total, sino cuántos tienen una forma —un par de turnos
    seguidos, una semana ISO— que el esqueleto NO produce por su cuenta: esos se los ha inventado
    el pipeline, y son los únicos que hay que mirar.

    domingos_esqueleto y descansos_esqueleto son lo que ya trae el esqueleto puro (Paso A) para
    cada regla — se toleran igual que las formas pactadas de los patrones y no cuentan como
    FALLOS nuevos.
    """
    domingos_esqueleto = domingos_esqueleto or set()
    descansos_esqueleto = descansos_esqueleto or set()
    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, plan)
    rotos = integridad(datos, plan, ritmos)

    domingo_actual = {(w, f) for (w, f), s in plan.items() if not domingo_ok(datos, plan, w, f, s)}
    heredados_dom = domingo_actual & domingos_esqueleto
    nuevos_domingo = domingo_actual - domingos_esqueleto

    descanso_actual: set[tuple[str, date]] = set()
    vistas: set[tuple[str, date]] = set()
    for (w, f) in plan:
        lunes = f - timedelta(days=f.weekday())
        if (w, lunes) in vistas or ritmo_mod.es_rigido(datos, ritmos, w):
            continue
        vistas.add((w, lunes))
        if not descanso_finde_ok(datos, plan, w, lunes):
            descanso_actual.add((w, lunes))
    heredados_desc = descanso_actual & descansos_esqueleto
    nuevos_descanso = descanso_actual - descansos_esqueleto

    otros = [r for r in rotos if "sin el sábado" not in r and "consecutivos libres" not in r]
    graves = len(otros) + len(nuevos_domingo) + len(nuevos_descanso)
    if graves == 0:
        extra_bits = []
        if heredados_dom:
            extra_bits.append(f"{len(heredados_dom)} domingo(s) heredado(s) del esqueleto")
        if heredados_desc:
            extra_bits.append(f"{len(heredados_desc)} semana(s) sin descanso consecutivo "
                              f"heredada(s) del esqueleto")
        extra = f" ({', '.join(extra_bits)}, tolerados)" if extra_bits else ""
        print(f"\nAUDITORÍA — integridad: correcta{extra}")
    else:
        if otros:
            ejemplo = otros[0]
        elif nuevos_domingo:
            w, f = next(iter(nuevos_domingo))
            ejemplo = f"{w} el {f:%d/%m}: domingo NUEVO sin el sábado de ese fin de semana"
        else:
            w, lunes = next(iter(nuevos_descanso))
            ejemplo = f"{w} semana del {lunes:%d/%m}: NUEVA sin par de días consecutivos libres"
        print(f"\nAUDITORÍA — integridad: *** {graves} FALLOS: {ejemplo} ***")
```

- [ ] **Step 13: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 11.
Expected: primera llamada imprime `AUDITORÍA — integridad: correcta (1 semana(s) sin descanso
consecutivo heredada(s) del esqueleto, tolerados)`; segunda llamada imprime
`AUDITORÍA — integridad: *** 1 FALLOS: ... NUEVA sin par de días consecutivos libres ***`.

- [ ] **Step 14: Commit**

```bash
git add src/ritmo.py src/legal.py
git commit -m "$(cat <<'EOF'
legal.py: descanso_finde_ok, y auditoría con línea base tolerada

descanso_finde_ok es de semana completa (no se pliega en permite(), a
diferencia de domingo_ok): si sábado y domingo se trabajan, exige un
par consecutivo libre entre semana. ritmo.es_rigido() da la frontera
estructural rígido/flexible de forma segura para grupos sin medición
(correturno). integridad()/auditar() ganan el mismo tratamiento de
línea base tolerada que ya se usó para domingo_ok, para no dejar la
auditoría permanentemente roja por lo heredado del esqueleto.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `residuo.py` — restricción dura en el CP-SAT del pool

**Files:**
- Modify: `src/residuo.py` (nuevo bloque, justo después del bloque de `domingo_ok` — verificar la
  línea exacta contra el archivo real, hoy alrededor de la línea 272)

**Interfaces:**
- Consumes: `datos.config.dias_descanso_finde`, `cargar_datos.DIAS_LV` (Task 1); `pool`,
  `por_trab`, `x`, `vars_por_fecha` (ya construidos en `resolver()`, el último por el bloque de
  `domingo_ok`); `forma.lunes_de` (ya existe).

Esta tarea, como la restricción de `domingo_ok`, no admite un test aislado del resto de
`resolver()` — es un fragmento dentro de la función de ~500 líneas que construye un único modelo
CP-SAT. La verificación real es la ejecución completa del pipeline (Task 8) y el spike de
rendimiento (Task 16). Aquí solo se comprueba que el fragmento no rompe la construcción del modelo,
y se hace una comprobación aislada del modelo con datos sintéticos pequeños para confirmar que la
restricción se comporta como se espera antes de gastar los ~5 minutos de un pipeline completo.

- [ ] **Step 1: Localizar el bloque de `domingo_ok` en el archivo real y confirmar que
  `vars_por_fecha` sigue teniendo exactamente esta forma**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && grep -n "domingo sin sábado: restricción dura\|dias_domingo = {f for f, s in por_trab\[w\]" src/residuo.py
```

Expected: dos líneas, confirmando el bloque que ya existe (de la Task 3 de `domingo_ok`). Si el
texto no coincide exactamente, releer el bloque completo antes de continuar — el resto de esta
tarea asume la forma exacta documentada en el spec.

- [ ] **Step 2: Añadir el bloque nuevo, inmediatamente después del `for f in dias_domingo:` de
  `domingo_ok` (dentro del mismo `for w in pool:`)**

```python
        for f in dias_domingo:
            dia_anterior = vars_por_fecha.get(f - timedelta(days=1), [])
            modelo.Add(sum(vars_por_fecha[f]) <= sum(dia_anterior))

        # descanso consecutivo tras un finde completo: restricción dura, mismo nivel que arriba
        for lunes in {forma.lunes_de(f) for f, s in por_trab[w]}:
            dias_semana = [lunes + timedelta(days=i) for i in range(7)]
            sab_vars = vars_por_fecha.get(dias_semana[5], [])
            dom_vars = vars_por_fecha.get(dias_semana[6], [])
            if not sab_vars or not dom_vars:
                continue                                    # esta semana no tiene ambos en juego
            ambos_finde = modelo.NewBoolVar(f"ambosfinde_{w}_{lunes:%m%d}")
            modelo.Add(ambos_finde >= sum(sab_vars) + sum(dom_vars) - 1)

            libres = []
            for i in range(5):
                dia_vars = vars_por_fecha.get(dias_semana[i], [])
                libre = modelo.NewBoolVar(f"librefinde_{w}_{dias_semana[i]:%m%d}")
                modelo.Add(sum(dia_vars) + libre == 1)       # C2 ya garantiza como mucho 1 turno/día
                libres.append(libre)

            fijos = datos.config.dias_descanso_finde
            if fijos:
                idx = {nombre: i for i, nombre in enumerate(DIAS_LV)}
                i1, i2 = sorted(idx[n] for n in fijos)
                modelo.Add(libres[i1] + libres[i2] >= 2 * ambos_finde)
            else:
                pares = []
                for i in range(4):
                    par = modelo.NewBoolVar(f"parfinde_{w}_{dias_semana[i]:%m%d}")
                    modelo.Add(par <= libres[i])
                    modelo.Add(par <= libres[i + 1])
                    pares.append(par)
                modelo.Add(sum(pares) >= ambos_finde)
```

Necesita `DIAS_LV` importado desde `cargar_datos` (añadir a la importación existente de
`residuo.py`, verificar el `import` exacto antes de editar).

- [ ] **Step 3: Verificar que el módulo sigue importando sin errores de sintaxis**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
import residuo
print('residuo.py importa sin errores de sintaxis: OK')
"
```

Expected: `residuo.py importa sin errores de sintaxis: OK`

- [ ] **Step 4: Comprobación aislada del modelo con un escenario sintético pequeño**

Construye un modelo CP-SAT mínimo (sin pasar por `resolver()` completo) que reproduzca solo el
fragmento nuevo, para confirmar la lógica de reificación antes de gastar un pipeline completo:

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from ortools.sat.python import cp_model

def resuelve(fijar_sab_dom, dias_libres_forzados=()):
    modelo = cp_model.CpModel()
    # 7 variables, una por día de la semana (L,M,X,J,V,S,D); 1 = trabaja.
    dia = [modelo.NewBoolVar(f'd{i}') for i in range(7)]
    if fijar_sab_dom:
        modelo.Add(dia[5] == 1)
        modelo.Add(dia[6] == 1)
    for i in dias_libres_forzados:
        modelo.Add(dia[i] == 0)

    ambos = modelo.NewBoolVar('ambos')
    modelo.Add(ambos >= dia[5] + dia[6] - 1)
    libres = []
    for i in range(5):
        libre = modelo.NewBoolVar(f'libre{i}')
        modelo.Add(dia[i] + libre == 1)
        libres.append(libre)
    pares = []
    for i in range(4):
        par = modelo.NewBoolVar(f'par{i}')
        modelo.Add(par <= libres[i])
        modelo.Add(par <= libres[i + 1])
        pares.append(par)
    modelo.Add(sum(pares) >= ambos)

    modelo.Maximize(sum(dia))          # intenta trabajar el máximo de días posible
    solver = cp_model.CpSolver()
    estado = solver.Solve(modelo)
    if estado not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return [solver.Value(d) for d in dia]

# 1. Sin sábado+domingo fijados (el solver es libre de elegir sáb/dom él mismo, maximizando):
#    el máximo real es 6, NO 7. El solver puede trabajar sábado+domingo+los 5 laborables (7) solo
#    si rompe la restricción, así que maximizar lo evita trabajando SOLO uno de los dos días de
#    fin de semana (sábado o domingo, nunca ambos) más los 5 laborables = 6. Trabajar los 7 días
#    exigiría ambos_finde=1 sin ningún par consecutivo libre, lo que la restricción prohíbe.
sol = resuelve(fijar_sab_dom=False)
assert sum(sol) == 6, sol

# 2. Con sábado+domingo fijados, maximizando días trabajados: como mucho 5 (7 - 2 consecutivos
#    libres), nunca 6 o 7.
sol = resuelve(fijar_sab_dom=True)
assert sum(sol) <= 5, sol
libres_idx = [i for i in range(5) if sol[i] == 0]
assert len(libres_idx) >= 2, sol
consecutivos = any(libres_idx[k] + 1 == libres_idx[k + 1] for k in range(len(libres_idx) - 1))
assert consecutivos, f'los libres no son consecutivos: {libres_idx}'

# 3. Si se fuerza a que L, M y X estén TRABAJADOS (día 0,1,2 = 1), el único par consecutivo que
#    puede quedar libre es (J, V) — días 3 y 4. El helper 'resuelve' no parametriza "trabajado
#    forzado", así que este caso repite el modelo con esos tres días fijados a mano.
from ortools.sat.python import cp_model as cpm
modelo = cpm.CpModel()
dia = [modelo.NewBoolVar(f'd{i}') for i in range(7)]
modelo.Add(dia[5] == 1); modelo.Add(dia[6] == 1)
modelo.Add(dia[0] == 1); modelo.Add(dia[1] == 1); modelo.Add(dia[2] == 1)
ambos = modelo.NewBoolVar('ambos')
modelo.Add(ambos >= dia[5] + dia[6] - 1)
libres = []
for i in range(5):
    libre = modelo.NewBoolVar(f'libre{i}')
    modelo.Add(dia[i] + libre == 1)
    libres.append(libre)
pares = []
for i in range(4):
    par = modelo.NewBoolVar(f'par{i}')
    modelo.Add(par <= libres[i]); modelo.Add(par <= libres[i + 1])
    pares.append(par)
modelo.Add(sum(pares) >= ambos)
solver = cpm.CpSolver()
estado = solver.Solve(modelo)
assert estado in (cpm.OPTIMAL, cpm.FEASIBLE), estado
assert solver.Value(dia[3]) == 0 and solver.Value(dia[4]) == 0, 'debía forzar J y V libres'

print('reificación ambos_finde / libre / par: se comporta como se espera — OK')
"
```

Expected: `reificación ambos_finde / libre / par: se comporta como se espera — OK`

- [ ] **Step 5: Commit**

```bash
git add src/residuo.py
git commit -m "$(cat <<'EOF'
CP-SAT: descanso consecutivo tras finde completo, restricción dura

Mismo nivel que C4/C5/C6/domingo_ok, en el mismo bloque `for w in
pool`. Variables auxiliares por trabajador-semana: ambos_finde (sábado
y domingo trabajados), libre_d por cada día L-V, y o bien el par fijo
de config.dias_descanso_finde o una disyunción de los 4 pares
adyacentes posibles. Verificado con un modelo CP-SAT sintético aislado
antes de tocar el pipeline completo.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `base.py` — `_soltar_dia_lv` busca el día adyacente

**Files:**
- Modify: `src/base.py:173-195` (`_soltar_dia_lv`)

**Interfaces:**
- Consumes: nada nuevo (usa solo `plan`, ya disponible en el ámbito de la función).

- [ ] **Step 1: Escribir el script de verificación (debe fallar: hoy no prioriza el adyacente)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar, Turno, Capacidad
import base, horas
from collections import Counter

datos = cargar()
turno = 'VADN022'
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'DOM')
lunes = domingo - timedelta(days=6)
dias = [lunes + timedelta(days=i) for i in range(5)]
lunes_, martes, miercoles, jueves, viernes = dias
trab = '12402832J'                          # id real, mixto

# Un turno L-V SINTÉTICO POR DÍA, con 'popularidad' (nº de capacitados) muy distinta — usar el
# MISMO turno los 5 días (como hacía una versión anterior de este script) empata el criterio
# 'peor' por completo, y max() de Python resuelve el empate por orden de lista (lunes, martes...),
# que salen adyacentes por pura coincidencia sin que la lógica de adyacencia haga nada. Con
# popularidades distintas, el criterio SÍ discrimina: lunes es el más popular (gana la primera
# llamada); viernes es el segundo más popular (ganaría la segunda llamada SIN la lógica de
# adyacencia); martes es el menos popular pero adyacente a lunes (debe ganar la segunda llamada
# CON la lógica de adyacencia).
popularidad = {lunes_: 10, martes: 1, miercoles: 5, jueves: 2, viernes: 8}
turno_de = {}
for f in dias:
    tid = f'TESTLV_{f:%m%d}'
    datos.turnos[tid] = Turno(id=tid, municipio=datos.turnos[turno].municipio, lv=1, sab=0, dom=0,
                              fes=0, hora_entrada=datos.turnos[turno].hora_entrada,
                              hora_salida=datos.turnos[turno].hora_salida, dem=1, horas=8.0)
    for j in range(popularidad[f]):
        datos.capacidades[(f'FAKE_{f:%m%d}_{j}', tid)] = Capacidad(lv=1, sab=0, dom=0, fest=0, v=0)
    turno_de[f] = tid

plan = {(trab, f): turno_de[f] for f in dias}
libro = horas.LibroHoras.desde_plan(datos, plan)
cubiertas = Counter()
for (_, f), s in plan.items():
    cubiertas[(s, f)] += 1

# Primera llamada (nada libre todavía): gana el más popular -> lunes (10).
primero = base._soltar_dia_lv(datos, plan, libro, cubiertas, trab, domingo)
assert primero is not None
dia_libre_1, _ = primero
assert dia_libre_1 == lunes_, f'se esperaba lunes (el más popular, 10), salió {dia_libre_1}'

# Segunda llamada: con lunes ya libre, debe preferir el ADYACENTE (martes, popularidad 1) en vez
# del más popular de los restantes (viernes, popularidad 8) — eso es justo lo que probaría que la
# lógica de adyacencia funciona: sin ella, ganaría viernes.
segundo = base._soltar_dia_lv(datos, plan, libro, cubiertas, trab, domingo)
assert segundo is not None
dia_libre_2, _ = segundo
assert dia_libre_2 == martes, (
    f'se esperaba martes (adyacente a lunes, aunque menos popular), salió {dia_libre_2}')
print('_soltar_dia_lv: la segunda llamada prioriza el adyacente sobre el más popular — OK')
"
```

Expected: `AssertionError: se esperaba martes (adyacente a lunes, aunque menos popular), salió
2026-XX-XX` (con la fecha de viernes) — hoy la segunda llamada elige "peor" sobre TODOS los
restantes sin mirar adyacencia, así que gana viernes (popularidad 8) en vez de martes.

- [ ] **Step 2: Modificar `_soltar_dia_lv` (líneas 173-195 actuales)**

Antes:
```python
def _soltar_dia_lv(datos: Datos, plan: dict[tuple[str, date], str], libro,
                   cubiertas: Counter, trab: str, f: date) -> tuple[date, str] | None:
    """Libra un día entre semana de la MISMA semana ISO para hacer sitio al de finde.

    Es literalmente lo que hace el planificador a mano: se libra un día entre semana para hacer un
    sábado. Deja las horas neutras y evita pasarse del tope de días por semana (cinco de L-V más el
    sábado ya son seis, y con el domingo siete).

    CUÁL se suelta es aquí solo una elección provisional —la línea que más gente puede tapar—,
    porque en este paso los correturnos todavía no están colocados y no hay forma de saber quién
    estará libre. La decisión de verdad la toma el paso D, que ve la semana entera: la devolvemos
    marcada como flexible y allí se elige el día mirando quién puede cubrir el hueco que deja.
    Devuelve (día soltado, línea) o None si no había ninguno.
    """
    lunes = f - timedelta(days=f.weekday())
    suyos = [lunes + timedelta(days=i) for i in range(5) if (trab, lunes + timedelta(days=i)) in plan]
    if not suyos:
        return None
    peor = max(suyos, key=lambda g: sum(1 for (_, ss) in datos.capacidades if ss == plan[(trab, g)]))
    s = plan.pop((trab, peor))
    libro.borra(trab, s)
    cubiertas[(s, peor)] -= 1
    return peor, s
```

Después:
```python
def _soltar_dia_lv(datos: Datos, plan: dict[tuple[str, date], str], libro,
                   cubiertas: Counter, trab: str, f: date) -> tuple[date, str] | None:
    """Libra un día entre semana de la MISMA semana ISO para hacer sitio al de finde.

    Es literalmente lo que hace el planificador a mano: se libra un día entre semana para hacer un
    sábado. Deja las horas neutras y evita pasarse del tope de días por semana (cinco de L-V más el
    sábado ya son seis, y con el domingo siete).

    Si esta semana ya se soltó otro día (p.ej. al conceder el sábado, antes de conceder el
    domingo), se prioriza el día ADYACENTE a él: sábado+domingo trabajados exigen un par de días
    consecutivos libres entre semana (`descanso_finde_ok`), y como SAB se procesa antes que DOM
    en `colocar_mixtos`, esta es la segunda de las dos llamadas que arma ese par.

    CUÁL se suelta si no hay una semana ya empezada es solo una elección provisional —la línea que
    más gente puede tapar—, porque en este paso los correturnos todavía no están colocados y no
    hay forma de saber quién estará libre. La decisión de verdad la toma el paso D, que ve la
    semana entera: la devolvemos marcada como flexible y allí se elige el día mirando quién puede
    cubrir el hueco que deja. Devuelve (día soltado, línea) o None si no había ninguno.
    """
    lunes = f - timedelta(days=f.weekday())
    suyos = [lunes + timedelta(days=i) for i in range(5) if (trab, lunes + timedelta(days=i)) in plan]
    if not suyos:
        return None
    ya_libre = [lunes + timedelta(days=i) for i in range(5)
                if (trab, lunes + timedelta(days=i)) not in plan]
    adyacentes = [d for d in suyos if any(abs((d - libre).days) == 1 for libre in ya_libre)]
    candidatos = adyacentes or suyos
    peor = max(candidatos,
              key=lambda g: sum(1 for (_, ss) in datos.capacidades if ss == plan[(trab, g)]))
    s = plan.pop((trab, peor))
    libro.borra(trab, s)
    cubiertas[(s, peor)] -= 1
    return peor, s
```

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `_soltar_dia_lv: la segunda llamada libera el día adyacente — OK`

- [ ] **Step 4: Verificar que la primera llamada de una semana sigue igual que antes (no
  sobre-restringe cuando no hay nada que emparejar)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import base, horas
from collections import Counter

datos = cargar()
turno = 'VADN022'
sabado = next(f for f in datos.lista_dias_calendario
              if datos.tipo_dia(f, datos.turnos[turno].municipio) == 'SAB')
lunes = sabado - timedelta(days=5)
trab = '12402832J'
turno_lv = next(s for s, t in datos.turnos.items()
                if t.lv == 1 and datos.elegible(trab, s, lunes)[0])
plan = {(trab, lunes + timedelta(days=i)): turno_lv for i in range(5)}
libro = horas.LibroHoras.desde_plan(datos, plan)
cubiertas = Counter()
for (_, f), s in plan.items():
    cubiertas[(s, f)] += 1

# Única llamada de la semana: debe devolver alguno de los 5 días (comportamiento normal).
resultado = base._soltar_dia_lv(datos, plan, libro, cubiertas, trab, sabado)
assert resultado is not None
dia, _ = resultado
assert lunes <= dia <= lunes + timedelta(days=4)
print('_soltar_dia_lv: primera llamada de la semana sigue funcionando — OK')
"
```

Expected: `_soltar_dia_lv: primera llamada de la semana sigue funcionando — OK`

- [ ] **Step 5: Commit**

```bash
git add src/base.py
git commit -m "$(cat <<'EOF'
colocar_mixtos: _soltar_dia_lv prioriza el día adyacente

Cuando un mixto recibe sábado y domingo la misma semana, SAB se
procesa antes que DOM (FINDE lo garantiza), así que la segunda llamada
a _soltar_dia_lv ya puede ver qué día se liberó con la primera. Si hay
un adyacente entre los que sigue trabajando, lo prioriza para formar
el par consecutivo que exige descanso_finde_ok; si no, cae al criterio
de siempre.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `libranzas.py` — `ceder()` acepta `ritmos`, y pase de reparación

**Files:**
- Modify: `src/libranzas.py:526-538` (`ceder`, acepta `ritmos` como parámetro opcional)
- Modify: `src/libranzas.py` (nueva función `_forzar_descanso_finde`, llamada al final de `ceder`)

**Interfaces:**
- Consumes: `legal.descanso_finde_ok`, `ritmo.es_rigido` (Task 2); `Registro.cesiones` (ya existe,
  cada `Cesion` tiene `titular: str`, `dias: list[date]`).
- Produces: `ceder(datos, plan, libro, protegidos=None, ritmos=None) -> Registro` — firma nueva,
  usada por la Task 7.

- [ ] **Step 1: Escribir el script de verificación de la firma nueva de `ceder` (debe fallar:
  hoy no acepta `ritmos`)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from cargar_datos import cargar
import base, horas, libranzas, ritmo

datos = cargar()
plan = base.construir(datos)
libro = horas.LibroHoras.desde_plan(datos, plan)
protegidos, flexibles = base.colocar_mixtos(datos, plan, libro)
ritmos = ritmo.medir(datos, plan)

# ceder() debe aceptar un ritmos ya calculado, sin volver a medirlo.
reg = libranzas.ceder(datos, plan, libro, protegidos, ritmos=ritmos)
print(f'ceder() acepta ritmos externo — OK ({len(reg.cesiones)} cesiones)')
"
```

Expected: `TypeError: ceder() got an unexpected keyword argument 'ritmos'`

- [ ] **Step 2: Modificar `ceder()` (líneas 526-538 actuales)**

Antes:
```python
def ceder(datos: Datos, plan: Plan, libro: LibroHoras,
          protegidos: dict[str, set[date]] | None = None) -> Registro:
    """`protegidos` son días que no se pueden ceder aunque sobren horas — hoy, los fines de semana
    de cuota que el paso A2 le dio a los mixtos: no son exceso, son la equidad que justifica que el
    mixto salga de su línea."""
    ritmos = ritmo_mod.medir(datos, plan)
    ritmo_mod.resumen(ritmos)
    reg = Registro()
    for w, dias in (protegidos or {}).items():
        reg.protegidos[w] |= dias
    fase1(datos, plan, libro, ritmos, reg)
    fase2(datos, plan, libro, ritmos, reg)
    return reg
```

Después:
```python
def ceder(datos: Datos, plan: Plan, libro: LibroHoras,
          protegidos: dict[str, set[date]] | None = None,
          ritmos: dict[str, Ritmo] | None = None) -> Registro:
    """`protegidos` son días que no se pueden ceder aunque sobren horas — hoy, los fines de semana
    de cuota que el paso A2 le dio a los mixtos: no son exceso, son la equidad que justifica que el
    mixto salga de su línea.

    `ritmos`, si no se pasa, se mide aquí mismo (comportamiento de siempre) — pipeline.py ya lo
    calcula en este mismo punto (tras colocar_mixtos) y lo pasa, para no remedirlo tres veces."""
    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, plan)
    ritmo_mod.resumen(ritmos)
    reg = Registro()
    for w, dias in (protegidos or {}).items():
        reg.protegidos[w] |= dias
    fase1(datos, plan, libro, ritmos, reg)
    fase2(datos, plan, libro, ritmos, reg)
    _forzar_descanso_finde(datos, plan, libro, reg, ritmos)
    return reg
```

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `NameError: name '_forzar_descanso_finde' is not defined` (todavía no existe — es el
Step 5).
Este es un fallo intermedio esperado: confirma que `ceder()` ya intenta llamarla.

- [ ] **Step 4: Escribir el script de verificación de `_forzar_descanso_finde` (debe fallar: no
  existe)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from libranzas import _forzar_descanso_finde
print('importa OK')
"
```

Expected: `ImportError: cannot import name '_forzar_descanso_finde' from 'libranzas'`

- [ ] **Step 5: Añadir `_forzar_descanso_finde`, tras `escribir_csv` y antes de `comprobar`**

```python
def _forzar_descanso_finde(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro,
                           ritmos: dict[str, Ritmo]) -> None:
    """Tras fase1+fase2: si una semana de sábado+domingo trabajado que el pipeline SÍ tocó (le
    cedió al menos un día) sigue sin un par consecutivo libre, cede uno más para completarlo —
    aunque cueste una cesión de más de la que pedían solo las horas. Las semanas que nadie tocó se
    dejan como están, igual que el resto de reglas legales sobre un patrón heredado."""
    def lunes_de(f: date) -> date:
        return f - timedelta(days=f.weekday())

    tocadas = {(c.titular, lunes_de(f)) for c in reg.cesiones for f in c.dias}
    titulares = sorted({c.titular for c in reg.cesiones})
    for titular in titulares:
        if ritmo_mod.es_rigido(datos, ritmos, titular):
            continue
        lunes_de_titular = sorted({lunes_de(f) for (w, f) in plan if w == titular})
        for lunes in lunes_de_titular:
            if legal.descanso_finde_ok(datos, plan, titular, lunes):
                continue
            if (titular, lunes) not in tocadas:
                continue                                  # esqueleto puro: se tolera
            dias_semana = [lunes + timedelta(days=i) for i in range(5)]
            libres = [d for d in dias_semana if (titular, d) not in plan]
            trabajados = [d for d in dias_semana if (titular, d) in plan]
            adyacentes = [d for d in trabajados if any(abs((d - lb).days) == 1 for lb in libres)]
            mejor = None
            for candidato in (adyacentes or trabajados):
                libres_para_cubrir = _libres(datos, plan, libro, plan[(titular, candidato)],
                                             candidato, {titular})
                if libres_para_cubrir == 0:
                    continue
                if mejor is None or libres_para_cubrir > mejor[1]:
                    mejor = (candidato, libres_para_cubrir)
            if mejor is None:
                print(f"  aviso  {titular} semana del {lunes:%d/%m}: sábado+domingo sin par "
                      f"consecutivo y nadie puede cubrir el día que lo completaría")
                continue
            candidato, _ = mejor
            s = plan.pop((titular, candidato))
            libro.borra(titular, s)
            reg.cesiones.append(Cesion(fase=2, titular=titular, dias=[candidato],
                                       horas=datos.turnos[s].horas, cubridor=None,
                                       desalojadas=0, motivo="descanso de finde"))
```

- [ ] **Step 6: Ejecutar el script de verificación del Step 1 de nuevo**

Mismo comando del Step 1.
Expected: `ceder() acepta ritmos externo — OK (N cesiones)` (con N el número real de cesiones de
esta ejecución — no hace falta que coincida con ninguna cifra concreta, solo que no lance
excepción).

- [ ] **Step 7: Verificar con un escenario sintético que la reparación fuerza el par cuando la
  semana SÍ fue tocada, y la deja intacta cuando NO**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal, libranzas, horas
from libranzas import Registro, Cesion

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
titular = '71158833F'                       # id real, PAT_GRANDE_VALL (flexible)
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and datos.disponible(titular, f) and datos.disponible(titular, f - timedelta(days=1)))
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

def plan_semana():
    return {(titular, sabado): turno_finde, (titular, domingo): turno_finde,
            (titular, martes): turno_lv, (titular, jueves): turno_lv, (titular, viernes): turno_lv}
    # libres: lunes y miércoles -> NO consecutivos

ritmos = {}   # titular sin grupo medible aquí -> ritmo.es_rigido lo trata como flexible: se evalúa

# Caso 1: la semana SÍ fue tocada (hay una cesión registrada ese lunes) -> debe repararla.
plan = plan_semana()
libro = horas.LibroHoras.desde_plan(datos, plan)
reg = Registro()
reg.cesiones.append(Cesion(fase=2, titular=titular, dias=[miercoles], horas=8.0,
                           cubridor=None, desalojadas=0, motivo='ciclo entero'))
libranzas._forzar_descanso_finde(datos, plan, libro, reg, ritmos)
assert legal.descanso_finde_ok(datos, plan, titular, lunes), 'debía quedar reparada'

# Caso 2: la semana NO fue tocada (ninguna cesión ese lunes) -> se tolera, no se toca.
plan2 = plan_semana()
libro2 = horas.LibroHoras.desde_plan(datos, plan2)
reg2 = Registro()                            # sin cesiones esa semana
libranzas._forzar_descanso_finde(datos, plan2, libro2, reg2, ritmos)
assert plan2 == plan_semana(), 'no debía tocar una semana que el pipeline no había tocado'
print('_forzar_descanso_finde: repara lo tocado, tolera lo heredado — OK')
"
```

Expected: `_forzar_descanso_finde: repara lo tocado, tolera lo heredado — OK`

- [ ] **Step 8: Commit**

```bash
git add src/libranzas.py
git commit -m "$(cat <<'EOF'
libranzas: pase de reparación del descanso consecutivo tras ceder

ceder() acepta ritmos como parámetro (pipeline.py lo calculará una
sola vez). _forzar_descanso_finde corre al final: si una semana de
sábado+domingo trabajado que el propio pipeline tocó sigue sin un par
consecutivo libre, cede un día más (el adyacente, con el mismo
criterio de "quién puede cubrirlo" que ya usa fase2) para completarlo;
lo que el pipeline nunca tocó se tolera, igual que el resto de reglas
legales sobre un patrón heredado.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `equidad.py` — `ritmos` en `pulir`/`pulir_dias`, guarda en `_valido_dia`

**Files:**
- Modify: `src/equidad.py:190-191` (`pulir`, acepta `ritmos`)
- Modify: `src/equidad.py:264-266` (`_valido_dia`, acepta `ritmos` y añade la guarda)
- Modify: `src/equidad.py:319-322` (`pulir_dias`, acepta `ritmos` y lo reenvía)
- Modify: `src/equidad.py:364` (la llamada a `_valido_dia` dentro de `pulir_dias`, pasa `ritmos`)
- Modify: `src/equidad.py:248` (la llamada a `pulir_dias` dentro de `pulir`, pasa `ritmos`)

**Interfaces:**
- Consumes: `legal.descanso_finde_ok`, `ritmo.es_rigido` (Task 2).
- Produces: `pulir(datos, plan, libro, vueltas=400, pactadas=None, ritmos=None) -> dict` y
  `pulir_dias(datos, plan, libro, vueltas=600, pactadas=None, ritmos=None) -> int` — firmas
  nuevas, usadas por la Task 7.

- [ ] **Step 1: Escribir el script de verificación de la firma nueva (debe fallar: no acepta
  `ritmos`)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from cargar_datos import cargar
import base, horas, equidad, ritmo

datos = cargar()
plan = base.construir(datos)
libro = horas.LibroHoras.desde_plan(datos, plan)
ritmos = ritmo.medir(datos, plan)
n = equidad.pulir_dias(datos, plan, libro, vueltas=1, ritmos=ritmos)
print(f'pulir_dias acepta ritmos externo — OK ({n} intercambios)')
"
```

Expected: `TypeError: pulir_dias() got an unexpected keyword argument 'ritmos'`

- [ ] **Step 2: Añadir `ritmos` a `pulir_dias` y reenviarlo a `_valido_dia` (líneas 319-322 y 364
  actuales)**

Antes (línea 319-322):
```python
def pulir_dias(datos: Datos, plan: Plan, libro: LibroHoras, vueltas: int = 600,
               pactadas: set | None = None) -> int:
    if pactadas is None:
        pactadas = legal.pactadas(datos, base.construir(datos))
```

Después:
```python
def pulir_dias(datos: Datos, plan: Plan, libro: LibroHoras, vueltas: int = 600,
               pactadas: set | None = None, ritmos: dict[str, "ritmo_mod.Ritmo"] | None = None) -> int:
    if pactadas is None:
        pactadas = legal.pactadas(datos, base.construir(datos))
    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, base.construir(datos))
```

Antes (línea 364, dentro del bucle):
```python
                        if _valido_dia(datos, plan, libro, a, da, b, db, pactadas):
```

Después:
```python
                        if _valido_dia(datos, plan, libro, a, da, b, db, pactadas, ritmos):
```

Necesita `import ritmo as ritmo_mod` en `equidad.py` (verificar si ya está importado; si no,
añadirlo junto a las demás importaciones del archivo).

- [ ] **Step 3: Añadir `ritmos` a `_valido_dia` y la guarda nueva (líneas 264-302 actuales)**

Antes:
```python
def _valido_dia(datos: Datos, plan: Plan, libro: LibroHoras,
                a: str, da: date, b: str, db: date, pactadas: set) -> bool:
    """`a` le pasa su día `da` a `b` y se queda con el `db` de `b`. Ambos de la misma semana."""
    sa, sb = plan[(a, da)], plan[(b, db)]
    if (b, da) in plan or (a, db) in plan:
        return False
    if not (datos.disponible(b, da) and datos.elegible(b, sa, da)[0]):
        return False
    if not (datos.disponible(a, db) and datos.elegible(a, sb, db)[0]):
        return False
    ha, hb = datos.turnos[sa].horas, datos.turnos[sb].horas
    if libro.horas(a) - ha + hb > libro.objetivo(a) + EPS:
        return False
    if libro.horas(b) - hb + ha > libro.objetivo(b) + EPS:
        return False

    # Los dos días pueden venir en cualquier orden: sin ordenarlos aquí el tramo sale invertido y
    # la comprobación no mira nada.
    desde, hasta = min(da, db), max(da, db)
    antes = legal.formas(datos, plan, a, desde, hasta) + legal.formas(datos, plan, b, desde, hasta)
    del plan[(a, da)], plan[(b, db)]
    plan[(b, da)], plan[(a, db)] = sa, sb
    lunes = _lunes(da)
    assert lunes == _lunes(db), "_valido_dia espera da y db en la misma semana ISO"
    # Asimetría deliberada frente a _empeora: _empeora es RELATIVA (solo rechaza una forma peor
    # que lo que el esqueleto ya tolera), _semana_respeta_domingo es ABSOLUTA (rechaza CUALQUIER
    # domingo huérfano, incluso uno que el esqueleto ya traía antes de este intercambio). Una
    # semana con un domingo huérfano heredado del esqueleto queda así congelada para pulir_dias
    # —ningún intercambio de día que involucre a alguno de los dos pasará nunca esa semana—, y es
    # intencional: esta regla no es de convenio y es más dura que las formas pactadas.
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

Después (solo cambian la firma y el bloque desde `rompe_domingo` hasta el `if`):
```python
def _valido_dia(datos: Datos, plan: Plan, libro: LibroHoras,
                a: str, da: date, b: str, db: date, pactadas: set,
                ritmos: dict[str, "ritmo_mod.Ritmo"]) -> bool:
    """`a` le pasa su día `da` a `b` y se queda con el `db` de `b`. Ambos de la misma semana."""
    sa, sb = plan[(a, da)], plan[(b, db)]
    if (b, da) in plan or (a, db) in plan:
        return False
    if not (datos.disponible(b, da) and datos.elegible(b, sa, da)[0]):
        return False
    if not (datos.disponible(a, db) and datos.elegible(a, sb, db)[0]):
        return False
    ha, hb = datos.turnos[sa].horas, datos.turnos[sb].horas
    if libro.horas(a) - ha + hb > libro.objetivo(a) + EPS:
        return False
    if libro.horas(b) - hb + ha > libro.objetivo(b) + EPS:
        return False

    # Los dos días pueden venir en cualquier orden: sin ordenarlos aquí el tramo sale invertido y
    # la comprobación no mira nada.
    desde, hasta = min(da, db), max(da, db)
    antes = legal.formas(datos, plan, a, desde, hasta) + legal.formas(datos, plan, b, desde, hasta)
    del plan[(a, da)], plan[(b, db)]
    plan[(b, da)], plan[(a, db)] = sa, sb
    lunes = _lunes(da)
    assert lunes == _lunes(db), "_valido_dia espera da y db en la misma semana ISO"
    # Asimetría deliberada frente a _empeora: _empeora es RELATIVA (solo rechaza una forma peor
    # que lo que el esqueleto ya tolera), _semana_respeta_domingo/_semana_respeta_descanso_finde
    # son ABSOLUTAS (rechazan CUALQUIER violación, incluso una que el esqueleto ya traía antes de
    # este intercambio). Una semana con un incumplimiento heredado queda así congelada para
    # pulir_dias, y es intencional: estas reglas no son de convenio y son más duras que las formas
    # pactadas.
    rompe_domingo = (not _semana_respeta_domingo(datos, plan, a, lunes)
                      or not _semana_respeta_domingo(datos, plan, b, lunes))
    rompe_descanso = (
        (not ritmo_mod.es_rigido(datos, ritmos, a)
         and not legal.descanso_finde_ok(datos, plan, a, lunes))
        or (not ritmo_mod.es_rigido(datos, ritmos, b)
            and not legal.descanso_finde_ok(datos, plan, b, lunes))
    )
    if rompe_domingo or rompe_descanso or _empeora(datos, plan, a, b, desde, hasta, antes, pactadas):
        del plan[(b, da)], plan[(a, db)]
        plan[(a, da)], plan[(b, db)] = sa, sb
        return False
    libro.borra(a, sa); libro.apunta(a, sb)
    libro.borra(b, sb); libro.apunta(b, sa)
    return True
```

- [ ] **Step 4: Ejecutar el script de verificación del Step 1 de nuevo**

Mismo comando del Step 1.
Expected: `pulir_dias acepta ritmos externo — OK (N intercambios)`.

- [ ] **Step 5: Añadir `ritmos` a `pulir` y reenviarlo a `pulir_dias` (líneas 190-193 y 248
  actuales)**

Antes (190-193):
```python
def pulir(datos: Datos, plan: Plan, libro: LibroHoras, vueltas: int = 400,
          pactadas: set | None = None) -> dict:
    if pactadas is None:
        pactadas = legal.pactadas(datos, base.construir(datos))
```

Después:
```python
def pulir(datos: Datos, plan: Plan, libro: LibroHoras, vueltas: int = 400,
          pactadas: set | None = None, ritmos: dict[str, "ritmo_mod.Ritmo"] | None = None) -> dict:
    if pactadas is None:
        pactadas = legal.pactadas(datos, base.construir(datos))
    if ritmos is None:
        ritmos = ritmo_mod.medir(datos, base.construir(datos))
```

Antes (línea 248):
```python
    dias = pulir_dias(datos, plan, libro, pactadas=pactadas)
```

Después:
```python
    dias = pulir_dias(datos, plan, libro, pactadas=pactadas, ritmos=ritmos)
```

- [ ] **Step 6: Verificar con un escenario sintético que la guarda rechaza un intercambio que
  dejaría a alguien con la semana rota, y que un grupo rígido queda exento**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import equidad, legal, ritmo

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
a, b = '71225031B', '71136820M'             # ids reales, correturno (siempre flexibles)

domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and all(datos.disponible(a, f - timedelta(days=i)) for i in range(0, 7))
               and datos.disponible(b, f - timedelta(days=4)))
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

class LibroFalso:
    def horas(self, w): return 0.0
    def objetivo(self, w): return 999999.0
    def borra(self, w, s): pass
    def apunta(self, w, s): pass

# a tiene sábado+domingo, trabaja lunes/jueves/viernes y tiene libres martes+miércoles (su único
# par consecutivo). b trabaja el martes. Intercambiar el LUNES de a (no forma parte del par) por
# el MARTES de b deja a a trabajando martes y libre en lunes+miércoles — ya NO consecutivos (están
# a 2 días) — rompe el único par que tenía, y debe rechazarse.
plan = {(a, sabado): turno_finde, (a, domingo): turno_finde, (a, lunes): turno_lv,
        (a, jueves): turno_lv, (a, viernes): turno_lv, (b, martes): turno_lv}
assert legal.descanso_finde_ok(datos, plan, a, lunes) is True, 'martes+miércoles ya son el par'
ritmos_vacio = {}                            # ambos correturno: es_rigido siempre False aquí

ok = equidad._valido_dia(datos, plan, LibroFalso(), a, lunes, b, martes, set(), ritmos_vacio)
assert ok is False, 'debía rechazar: a perdería su único par consecutivo libre'
assert (a, lunes) in plan and (b, martes) in plan, 'el plan no debe quedar mutado'

# Con a marcado como rígido (grupo ficticio con Ritmo.rigido=True), la guarda no debe intervenir:
# lo comprobamos directamente contra descanso_finde_ok, no contra _valido_dia (que también puede
# rechazar por _empeora/domingo_ok, ruido que no viene al caso aquí).
ritmo_rigido = {ritmo.grupo_de(datos, a): ritmo.Ritmo(grupo='x', trabajo=7, descanso=7,
                                                       regularidad=1.0, ratio=1.0, rigido=True)}
assert ritmo.es_rigido(datos, ritmo_rigido, a) is True
# El propio criterio que usa _valido_dia: 'not es_rigido(a) and not descanso_finde_ok(a)' debe
# ser False para a cuando es rígido, aunque descanso_finde_ok(a) sea False por su cuenta. Se
# construye a mano el plan YA intercambiado (mismo resultado que produciría el swap de arriba),
# para comprobar el criterio sin pasar otra vez por _valido_dia.
plan_roto = dict(plan)
del plan_roto[(a, lunes)], plan_roto[(b, martes)]
plan_roto[(b, lunes)], plan_roto[(a, martes)] = turno_lv, turno_lv
sigue_roto = not legal.descanso_finde_ok(datos, plan_roto, a, lunes)
assert sigue_roto is True, 'el plan a mano debía reproducir la semana rota'
criterio_rigido = not ritmo.es_rigido(datos, ritmo_rigido, a) and sigue_roto
assert criterio_rigido is False, 'un grupo rígido no debe activar rompe_descanso aunque la ' \
                                  'semana no tenga par consecutivo'
print('_valido_dia: rompe_descanso rechaza sin exención, y la exención rígida sí frena el criterio — OK')
"
```

Expected: `_valido_dia: rompe_descanso rechaza el intercambio que deja sin par consecutivo — OK`

- [ ] **Step 7: Commit**

```bash
git add src/equidad.py
git commit -m "$(cat <<'EOF'
pulir_dias: guarda el descanso consecutivo tras finde completo

_valido_dia gana rompe_descanso, análoga a rompe_domingo: si el
intercambio deja a a o a b sin un par consecutivo libre en una semana
de sábado+domingo trabajado, se deshace igual que con _empeora. Los
grupos de patrón rígidos quedan exentos vía ritmo.es_rigido, no por
tipo de trabajador. pulir() no se toca: mueve la semana entera como
unidad atómica, así que no puede romper esta regla si ya se cumplía.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `pipeline.py` — calcula `ritmos` una sola vez y lo reparte

**Files:**
- Modify: `src/pipeline.py:46-58` (tras `base.construir()` y `colocar_mixtos`)
- Modify: `src/pipeline.py:83-88` (`equidad.pulir` y `legal.auditar`)

**Interfaces:**
- Consumes: las firmas nuevas de `libranzas.ceder`, `equidad.pulir` y `legal.auditar` (Tasks 5, 6,
  2); `ritmo.medir` (ya existe).

- [ ] **Step 1: Leer el estado actual exacto de `pipeline.py` líneas 40-89**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && sed -n '40,89p' src/pipeline.py
```

Confirmar que coincide con lo que sigue antes de editar — si algo cambió respecto a lo mostrado
aquí, adaptar los números de línea, no el contenido de los cambios.

- [ ] **Step 2: Calcular `ritmos_esqueleto` (para la línea base tolerada) y `domingos_esqueleto` /
  `descansos_esqueleto` justo tras `base.construir()`, antes de `colocar_mixtos`**

Antes:
```python
    # -- Paso Base ------------------------------------------------------------- #
    plan = base.construir(datos)
    libro = horas.LibroHoras.desde_plan(datos, plan)
    horas.resumen(datos,libro,"PASO A ")
    pactadas = legal.pactadas(datos,plan)

    # -- Paso A2 ------------------------------------------------------------ #
    protegidos, flexibles = base.colocar_mixtos(datos, plan, libro)
    base.resumen_mixtos(datos, plan, libro)

    # -- Paso B ------------------------------------------------------------- #
    reg = libranzas.ceder(datos, plan, libro, protegidos)
```

Después:
```python
    # -- Paso Base ------------------------------------------------------------- #
    plan = base.construir(datos)
    libro = horas.LibroHoras.desde_plan(datos, plan)
    horas.resumen(datos,libro,"PASO A ")
    pactadas = legal.pactadas(datos,plan)
    domingos_esqueleto = {(w, f) for (w, f), s in plan.items()
                          if not legal.domingo_ok(datos, plan, w, f, s)}
    # ritmos_esqueleto es SOLO para la línea base tolerada de descansos_esqueleto: medido sobre
    # el Paso A puro (antes de que mixtos/correturnos tengan ninguna asignación), es correcto para
    # clasificar a los grupos de PATRÓN que ya existen en ese punto — que es lo único que puede
    # aparecer en descansos_esqueleto, ver legal.py.
    ritmos_esqueleto = ritmo.medir(datos, plan)
    descansos_esqueleto: set[tuple[str, date]] = set()
    vistas_esqueleto: set[tuple[str, date]] = set()
    for (w, f) in plan:
        lunes = f - timedelta(days=f.weekday())
        if (w, lunes) in vistas_esqueleto or ritmo.es_rigido(datos, ritmos_esqueleto, w):
            continue
        vistas_esqueleto.add((w, lunes))
        if not legal.descanso_finde_ok(datos, plan, w, lunes):
            descansos_esqueleto.add((w, lunes))

    # -- Paso A2 ------------------------------------------------------------ #
    protegidos, flexibles = base.colocar_mixtos(datos, plan, libro)
    base.resumen_mixtos(datos, plan, libro)

    # ritmos (a secas) es la medición que se reparte al resto del pipeline: en este punto los
    # mixtos ya tienen su línea L-V y su cuota de finde, así que su grupo ("mixto") sí es medible
    # — medirlo antes (como ritmos_esqueleto) los dejaría fuera. libranzas.ceder ya media esto
    # mismo internamente si no se le pasa; aquí se calcula una vez y se reparte.
    ritmos = ritmo.medir(datos, plan)

    # -- Paso B ------------------------------------------------------------- #
    reg = libranzas.ceder(datos, plan, libro, protegidos, ritmos)
```

Necesita `import ritmo` (o el alias que ya use el resto del archivo) y `from datetime import date,
timedelta` en `pipeline.py` — verificar las importaciones actuales del archivo antes de editar
(hoy importa `base, equidad, forma, horas, legal, libranzas, residuo`; añadir `ritmo` a esa
misma línea, y comprobar si `date`/`timedelta` ya están disponibles o hace falta importarlos).

- [ ] **Step 3: Pasar `ritmos` a `equidad.pulir` y los conjuntos nuevos a `legal.auditar`
  (líneas 83-88 actuales)**

Antes:
```python
    # -- Paso E ------------------------------------------------------------- #
    info = equidad.pulir(datos, plan, libro)
    equidad.resumen(datos, plan, info)
    horas.resumen(datos, libro, "PASO E — horas finales")
    legal.auditar(datos, plan, pactadas, domingos_esqueleto)
```

Después:
```python
    # -- Paso E ------------------------------------------------------------- #
    info = equidad.pulir(datos, plan, libro, ritmos=ritmos)
    equidad.resumen(datos, plan, info)
    horas.resumen(datos, libro, "PASO E — horas finales")
    legal.auditar(datos, plan, pactadas, domingos_esqueleto, descansos_esqueleto, ritmos)
```

- [ ] **Step 4: Verificar que el módulo importa sin errores**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
import pipeline
print('pipeline.py importa sin errores: OK')
"
```

Expected: `pipeline.py importa sin errores: OK`

- [ ] **Step 5: Commit**

```bash
git add src/pipeline.py
git commit -m "$(cat <<'EOF'
pipeline.py: ritmos calculado una sola vez y repartido

ritmos_esqueleto (sobre el Paso A puro) alimenta descansos_esqueleto,
la línea base tolerada de la nueva regla, igual que domingos_esqueleto
ya hace para domingo_ok. ritmos (a secas, medido tras colocar_mixtos,
para que el grupo "mixto" sea medible) se pasa a libranzas.ceder,
equidad.pulir y legal.auditar en vez de remedirse en cada uno.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Verificación end-to-end

**Files:** ninguno (solo ejecución)

**Interfaces:** ninguna — integra las Tasks 1-7.

- [ ] **Step 1: Ejecutar el pipeline completo**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP
source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null
conda activate ortools_env
LOG=/tmp/claude-descanso-e2e/pipeline_descanso.log     # o el scratchpad real de la sesión activa
mkdir -p "$(dirname "$LOG")"
nohup python3 src/pipeline.py > "$LOG" 2>&1 &
PID=$!
while kill -0 "$PID" 2>/dev/null; do sleep 15; done
tail -100 "$LOG"
```

Este es un proceso real de varios minutos: no asumir que termina solo porque el comando "vuelve" —
el bucle `while kill -0` es lo que confirma que el proceso ya no existe antes de leer el log.

Expected: termina sin traceback. La línea `AUDITORÍA — integridad:` puede salir `correcta` (con o
sin el paréntesis de tolerados) o con FALLOS — si hay FALLOS, comprobar primero si mencionan
"consecutivos libres" (sería la señal de un mecanismo no identificado, ver Step 3) antes de asumir
que es solo el caso benigno ya conocido del domingo suelto de `12427762B`.

- [ ] **Step 2: Confirmar en el log que no hay ninguna semana nueva sin par consecutivo**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import base, equidad, forma, horas, legal, libranzas, residuo, ritmo

datos = cargar()
plan = base.construir(datos)
libro = horas.LibroHoras.desde_plan(datos, plan)
pactadas = legal.pactadas(datos, plan)
domingos_esqueleto = {(w, f) for (w, f), s in plan.items()
                      if not legal.domingo_ok(datos, plan, w, f, s)}
ritmos_esqueleto = ritmo.medir(datos, plan)
descansos_esqueleto = set()
vistas = set()
for (w, f) in plan:
    lunes = f - timedelta(days=f.weekday())
    if (w, lunes) in vistas or ritmo.es_rigido(datos, ritmos_esqueleto, w):
        continue
    vistas.add((w, lunes))
    if not legal.descanso_finde_ok(datos, plan, w, lunes):
        descansos_esqueleto.add((w, lunes))

protegidos, flexibles = base.colocar_mixtos(datos, plan, libro)
ritmos = ritmo.medir(datos, plan)
reg = libranzas.ceder(datos, plan, libro, protegidos, ritmos)
rep = forma.repartir(datos, plan)
residuo.resolver(datos, plan, libro, rep, flexibles, segundos=300, hilos=8)
residuo.rellenar_refuerzos(datos, plan, libro)
residuo.canjear_en_cadena(datos, plan, libro)
residuo.canjear_refuerzos(datos, plan, libro)
equidad.pulir(datos, plan, libro, ritmos=ritmos)

descanso_actual = set()
vistas2 = set()
for (w, f) in plan:
    lunes = f - timedelta(days=f.weekday())
    if (w, lunes) in vistas2 or ritmo.es_rigido(datos, ritmos, w):
        continue
    vistas2.add((w, lunes))
    if not legal.descanso_finde_ok(datos, plan, w, lunes):
        descanso_actual.add((w, lunes))
nuevas = descanso_actual - descansos_esqueleto
assert not nuevas, f'{len(nuevas)} semanas NUEVAS sin par consecutivo: {sorted(nuevas)[:5]}'
print(f'Pipeline completo: 0 semanas nuevas sin descanso consecutivo '
      f'({len(descanso_actual)} toleradas del esqueleto). OK')
"
```

Expected: `Pipeline completo: 0 semanas nuevas sin descanso consecutivo (N toleradas del
esqueleto). OK`. Esta ejecución tarda lo mismo que el pipeline completo porque repite el mismo
trabajo — es intencional, es la única forma de comprobar el invariante sobre el plan real completo
sin depender de que `pipeline.py` exponga variables inspeccionables. Si `nuevas` no está vacío,
repetir el diagnóstico que ya usó la verificación end-to-end de `domingo_ok` (clasificar por tipo
de trabajador, comparar contra el plan justo tras `base.construir()`) para identificar si es un
mecanismo no contemplado por este plan.

- [ ] **Step 3: Comparar cobertura y niveles del CP-SAT contra la referencia post-`domingo_ok`**

Revisar en el log del Step 1 contra `pipeline_task7.log` (la referencia de después de cerrar el
plan de `domingo_ok`):
- `PASO D — cobertura final: N/18295` — una bajada es posible y aceptable (spec, «Consecuencias
  esperadas»); anotar la cifra nueva.
- Los 4 niveles del CP-SAT (`OPTIMAL`/`FEASIBLE`, valor, tiempo) — no se espera que empeoren más
  allá de lo que ya se documentó en el spike de rendimiento de `domingo_ok`; si empeoran mucho más,
  anotarlo para la Task 16 (spike dedicado a esta restricción).
- Tabla de PASO A2 (mixtos) y PASO E (equidad): confirmar que no hay una caída llamativa en la
  cuota de fin de semana de ningún mixto o grupo de patrón flexible.

Esto es un paso de lectura, no de comando — deja constancia en la conversación de las cifras
antes/después.

- [ ] **Step 4: Reportar el resultado**

No hay commit en este paso. Resume en la conversación: si el invariante se sostiene end-to-end
(0 semanas nuevas sin par consecutivo), cobertura final, y si algún nivel del CP-SAT empeoró de
forma notable respecto a la referencia de `domingo_ok`.

---

### Task 10 (amendment, descubierta en Task 8): `residuo.py` — guarda los tres canjes/relleno posteriores al modelo

**Descubierto en Task 8** (verificación end-to-end): 130 semanas nuevas sin par consecutivo, la
mayoría (correturno + buena parte de mixto) trazables a `canjear_refuerzos`, `canjear_en_cadena`
(`_cadena`) y `rellenar_refuerzos` — los tres asignan un día nuevo a un trabajador sin comprobar
`legal.descanso_finde_ok`. Ver el Addendum en
`docs/superpowers/specs/2026-08-26-descanso-consecutivo-finde-design.md` para el diagnóstico
completo.

**Files:**
- Modify: `src/residuo.py:444-508` (`canjear_refuerzos`)
- Modify: `src/residuo.py:545-587` (`_cadena`)
- Modify: `src/residuo.py:593-638` (`rellenar_refuerzos`)

**Interfaces:**
- Consumes: `legal.descanso_finde_ok` (Task 2), `forma.lunes_de` (ya existe).

Las tres funciones comparten el mismo patrón de arreglo: comprobar `legal.descanso_finde_ok`
justo después de añadir el día nuevo al `plan` (nunca al ceder uno: ceder solo puede ayudar o ser
neutro para esta regla, nunca romperla — ver «El problema» del spec), y deshacer ese candidato
concreto si lo rompe, igual que ya se hace con `legal.permite`.

- [ ] **Step 1: Escribir el script de verificación de `canjear_refuerzos` (debe fallar: hoy puede
  romper el par consecutivo)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal, residuo, horas

datos = cargar()
turno_finde, turno_lv, turno_refcal = 'VADN022', 'VADN001', next(
    s for s, t in datos.turnos.items() if t.dem == 0)
w = '71225031B'                              # id real, correturno

domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and all(datos.disponible(w, f - timedelta(days=i)) for i in range(7))
               and datos.elegible(w, turno_refcal, f - timedelta(days=1))[0])
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

# w tiene sábado+domingo trabajados y libres martes+miércoles (su único par consecutivo); el
# REF CAL que 'usados' soltaría para pagar un hueco cualquiera es justo el sábado libre... no,
# es un día YA TRABAJADO por w con turno_refcal: lo situamos en jueves (fuera del par). Al
# soltarlo para pagar un hueco en, p.ej., viernes (que se AÑADE), w pasaría a trabajar viernes y
# perder... en realidad lo que rompe el par es AÑADIR viernes mismo si viernes fuera parte del
# par — aquí construimos el caso más directo: el hueco a cubrir (f) es exactamente uno de los dos
# días libres del par (martes), y cubrirlo lo ocupa, dejando solo miércoles libre (sin pareja).
plan = {(w, sabado): turno_finde, (w, domingo): turno_finde, (w, jueves): turno_refcal,
        (w, viernes): turno_lv}
libro = horas.LibroHoras.desde_plan(datos, plan)
assert legal.descanso_finde_ok(datos, plan, w, lunes) is True, 'martes+miércoles ya son el par'

antes = dict(plan)
residuo.canjear_refuerzos(datos, plan, libro)
# Si canjear_refuerzos ha cubierto el hueco de martes con w (rompiendo el par), debe haberse
# deshecho — el plan de w para esa semana debe seguir respetando la regla.
assert legal.descanso_finde_ok(datos, plan, w, lunes), (
    f'canjear_refuerzos rompió el par consecutivo: {plan}')
print('canjear_refuerzos: no rompe el par consecutivo — OK')
"
```

Expected: este guion depende de que exista un hueco real en `martes` que `canjear_refuerzos`
pueda cubrir con `w` gastando el REF CAL de `jueves` — si en la ejecución real no hay ningún
hueco de ese tipo ese día concreto, el `assert` no llega a activarse (falso negativo). **Antes de
dar este paso por bueno**, confirmar con un `print` intermedio si `canjear_refuerzos` realmente
intentó y deshizo el candidato (comparar `plan == antes` tras la llamada) — si no tocó nada
porque no había hueco que cubrir con `w` ese día, ajustar la fecha/turno del guion hasta encontrar
un caso real donde SÍ compita, en vez de aceptar un test que no ejercita nada.

**Dos problemas reales ya encontrados al ejecutar este guion tal cual** (sesión de implementación
de esta Task): (1) un REF CAL nunca opera en sábado (`turnos.csv`: todo turno `dem==0` es
`lv=1, sab=0`), así que exigir `datos.elegible(w, turno_refcal, sabado)` en la búsqueda del
`next(...)` revienta con `StopIteration` — el REF CAL debe buscarse/colocarse siempre en un día
L-V, nunca en el propio sábado. (2) `canjear_refuerzos` recorre `datos.trabajadores` completo
(`candidatos = sorted(datos.trabajadores, ...)`) y todos los huecos del año
(`forma.huecos(datos, plan)`): con los datos reales de por medio, otro trabajador distinto de `w`
puede cubrir el hueco de `martes` antes de que el bucle llegue a `w`, dejando el escenario sin
ejercitar el candidato de interés — falso negativo silencioso. Para aislar de verdad el camino de
código de `w`, hace falta acotar la búsqueda con monkeypatch temporal de `datos.trabajadores`
(a `{w: ...}`) y de `forma.huecos` (a solo `[(turno_finde... o el turno del hueco, martes)]`)
durante la llamada a `canjear_refuerzos`, restaurando ambos después — no es hacer trampa: son
sustituciones legítimas de la ENTRADA de búsqueda, no del código bajo prueba.

- [ ] **Step 2: Modificar `canjear_refuerzos` (líneas 481-507 actuales)**

Antes:
```python
                sueltos = sorted(refuerzos_de.get(w, []), key=lambda g: -abs((g - f).days))
                usados: list[date] = []
                for g in sueltos:
                    if falta <= 0:
                        break
                    falta -= datos.turnos[plan[(w, g)]].horas
                    usados.append(g)
                if falta > 0:
                    continue                              # no le llega ni soltándolos todos
            else:
                usados = []
            if not legal.permite(datos, plan, w, f, s):
                continue
            # Se suelta el REF CAL de otro día (g) para pagar la cobertura de f. Seguro frente al
            # domingo-sin-sábado solo porque todo turno con dem==0 en turnos.csv es lv=1 con
            # sab/dom/fest=0: un REF CAL nunca cae en fin de semana, así que soltarlo no puede
            # dejar huérfano un domingo. Si algún día se añade un REF CAL de fin de semana, esto
            # habría que revisarlo.
            for g in usados:
                libro.borra(w, plan.pop((w, g)))
                refuerzos_de[w].remove(g)
            plan[(w, f)] = s
            libro.apunta(w, s)
            cerrados += 1
            break
```

Después:
```python
                sueltos = sorted(refuerzos_de.get(w, []), key=lambda g: -abs((g - f).days))
                usados: list[tuple[date, str]] = []
                for g in sueltos:
                    if falta <= 0:
                        break
                    turno_g = plan[(w, g)]
                    falta -= datos.turnos[turno_g].horas
                    usados.append((g, turno_g))
                if falta > 0:
                    continue                              # no le llega ni soltándolos todos
            else:
                usados = []
            if not legal.permite(datos, plan, w, f, s):
                continue
            # Se suelta el REF CAL de otro día (g) para pagar la cobertura de f. Seguro frente al
            # domingo-sin-sábado: todo turno con dem==0 es lv=1 sin fin de semana, así que soltarlo
            # nunca deja huérfano un domingo. NO es seguro frente al descanso consecutivo: soltar o
            # añadir un día entre semana es justo lo que forma o rompe el par, así que se comprueba
            # tras aplicar el cambio y se deshace este candidato si lo rompe.
            for g, _ in usados:
                del plan[(w, g)]
            plan[(w, f)] = s
            if not legal.descanso_finde_ok(datos, plan, w, forma.lunes_de(f)):
                del plan[(w, f)]
                for g, turno_g in usados:
                    plan[(w, g)] = turno_g
                continue
            for g, turno_g in usados:
                libro.borra(w, turno_g)
                refuerzos_de[w].remove(g)
            libro.apunta(w, s)
            cerrados += 1
            break
```

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `canjear_refuerzos: no rompe el par consecutivo — OK`

- [ ] **Step 4: Modificar `_cadena` (líneas 572-581 actuales)**

Antes:
```python
                ref_c, turno_w = plan.pop((c, g)), plan.pop((w, g))
                if not legal.permite(datos, plan, c, g, propio):
                    plan[(c, g)], plan[(w, g)] = ref_c, turno_w
                    continue
                libro.borra(c, ref_c)
                plan[(c, g)] = propio
                libro.apunta(c, propio)
                libro.borra(w, turno_w)
                plan[(w, f)] = s
                libro.apunta(w, s)
                refcal_dia[g].remove(c)
                dias_de[w].remove(g)
                dias_de[w].append(f)
                dias_de[c].append(g) if g not in dias_de[c] else None
                return True
```

Después:
```python
                ref_c, turno_w = plan.pop((c, g)), plan.pop((w, g))
                if not legal.permite(datos, plan, c, g, propio):
                    plan[(c, g)], plan[(w, g)] = ref_c, turno_w
                    continue
                plan[(c, g)] = propio
                plan[(w, f)] = s
                if not legal.descanso_finde_ok(datos, plan, w, forma.lunes_de(f)):
                    del plan[(c, g)], plan[(w, f)]
                    plan[(c, g)], plan[(w, g)] = ref_c, turno_w
                    continue
                libro.borra(c, ref_c)
                libro.apunta(c, propio)
                libro.borra(w, turno_w)
                libro.apunta(w, s)
                refcal_dia[g].remove(c)
                dias_de[w].remove(g)
                dias_de[w].append(f)
                dias_de[c].append(g) if g not in dias_de[c] else None
                return True
```

Nota: `c` gana el turno `propio` en el mismo día `g` que ya tenía ocupado con su REF CAL — no
cambia si ese día está trabajado o libre para `c` (mismo turno, distinto día no aplica aquí), así
que no necesita su propia comprobación de `descanso_finde_ok`. Solo `w` gana un día NUEVO (`f`).

- [ ] **Step 5: Modificar `rellenar_refuerzos` (líneas 629-635 actuales)**

Antes:
```python
                    if (datos.elegible(w, s, f)[0] and libro.cabe(w, s)
                            and legal.permite(datos, plan, w, f, s)):
                        plan[(w, f)] = s
                        libro.apunta(w, s)
                        puestas += 1
                        colocado = True
                        break
```

Después:
```python
                    if (datos.elegible(w, s, f)[0] and libro.cabe(w, s)
                            and legal.permite(datos, plan, w, f, s)):
                        plan[(w, f)] = s
                        if not legal.descanso_finde_ok(datos, plan, w, forma.lunes_de(f)):
                            del plan[(w, f)]
                            continue
                        libro.apunta(w, s)
                        puestas += 1
                        colocado = True
                        break
```

- [ ] **Step 6: Verificar que el módulo importa sin errores**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
import residuo
print('residuo.py importa sin errores: OK')
"
```

Expected: `residuo.py importa sin errores: OK`

`_cadena` y `rellenar_refuerzos` no admiten un guion de verificación aislado tan directo como
`canjear_refuerzos` sin gastar mucho más tiempo montando las condiciones exactas que cada uno
necesita (huecos, presupuesto de horas, REF CAL de un tercero disponible) — mismo compromiso que
ya se aceptó para el bloque CP-SAT de la Task 3. Su verificación real es la Task 12
(re-ejecución completa del pipeline): si tras aplicar estas tres correcciones siguen apareciendo
semanas nuevas rotas trazables a `_cadena` o `rellenar_refuerzos`, se vuelve aquí.

- [ ] **Step 7: Commit**

```bash
git add src/residuo.py
git commit -m "$(cat <<'EOF'
residuo: canjear_refuerzos/_cadena/rellenar_refuerzos respetan el descanso

Los tres asignan un día nuevo a un trabajador sin comprobar
descanso_finde_ok — encontrado en la verificación end-to-end de la
Task 8 (130 semanas nuevas rotas). Se comprueba tras aplicar el
cambio, igual que ya se hace con legal.permite, y se deshace ese
candidato concreto si rompe el par consecutivo. Ceder un día (siempre
como pago, nunca como resultado final en estas tres funciones) no
necesita comprobación: solo puede ayudar o ser neutro para esta regla.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11 (amendment, descubierta en Task 8): `residuo.py` — restricción dura para la semana flexible del mixto (`z`)

**Descubierto en Task 8**, mismo diagnóstico que la Task 10: el CP-SAT está obligado a reactivar
exactamente `cuantos` de los días candidatos de la semana flexible de un mixto
(`modelo.Add(sum(suyas) == min(cuantos, len(suyas)))`), pero es libre de elegir CUÁLES —puede
dejar libres días distintos a los que la Task 4 (`base.py`) eligió deliberadamente adyacentes,
rompiendo el par aunque el conteo total de días libres no cambie.

**Files:**
- Modify: `src/residuo.py:174` (justo después del bloque existente de variables `z`, dentro del
  mismo `for (trab, lunes), (candidatos, cuantos) in flex.items():`)

**Interfaces:**
- Consumes: `datos.config.dias_descanso_finde`, `cargar_datos.DIAS_LV` (Task 1); `z`, `flex`,
  `plan` (ya construidos en `resolver()`).

Igual que la Task 3, este bloque no admite un test aislado del resto de `resolver()` — es un
fragmento dentro de la función que construye el único modelo CP-SAT. Se verifica con un modelo
sintético aislado (Step 2) antes de gastar un pipeline completo, y con la Task 12 como
verificación real.

- [ ] **Step 1: Localizar el bloque de variables `z` en el archivo real**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && grep -n "El mixto vuelve a coger todos sus días\|AddAtMostOne(\[z\[" src/residuo.py
```

Expected: dos líneas, confirmando el bloque existente. Si el texto no coincide, releerlo entero
antes de continuar.

- [ ] **Step 2: Comprobación aislada del modelo con un escenario sintético**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from ortools.sat.python import cp_model

# Reproduce el patrón: 5 booleanas z (una por día L-V candidato), obligadas a activarse EXACTAMENTE
# 3 de las 5 (cuantos=3, len(candidatos)=5 -> se quedan libres 2). Sin la restricción nueva, el
# modelo es libre de dejar libres dos días no consecutivos (p.ej. lunes y miércoles). Con ella,
# debe verse forzado a dejar libres un par consecutivo.
modelo = cp_model.CpModel()
z = [modelo.NewBoolVar(f'z{i}') for i in range(5)]         # 1 = reactivado (trabajado)
modelo.Add(sum(z) == 3)                                     # se quedan libres exactamente 2

libres = []
for i in range(5):
    libre = modelo.NewBoolVar(f'libre{i}')
    modelo.Add(z[i] + libre == 1)
    libres.append(libre)
pares = []
for i in range(4):
    par = modelo.NewBoolVar(f'par{i}')
    modelo.Add(par <= libres[i])
    modelo.Add(par <= libres[i + 1])
    pares.append(par)
modelo.Add(sum(pares) >= 1)                                 # ambos_finde=1 siempre en este escenario

# Fuerza a que lunes (0) y miércoles (2) NO sean los que queden libres (para comprobar que el
# modelo encuentra una alternativa válida en vez de quedar infactible).
modelo.Add(z[1] == 1)                                        # martes trabajado

solver = cp_model.CpSolver()
estado = solver.Solve(modelo)
assert estado in (cp_model.OPTIMAL, cp_model.FEASIBLE), estado
valores = [solver.Value(v) for v in z]
libres_idx = [i for i in range(5) if valores[i] == 0]
assert len(libres_idx) == 2, libres_idx
assert libres_idx[1] - libres_idx[0] == 1, f'los libres no son consecutivos: {libres_idx}'
print('restricción de par consecutivo sobre z: fuerza un par adyacente — OK')
"
```

Expected: `restricción de par consecutivo sobre z: fuerza un par adyacente — OK`

- [ ] **Step 3: Añadir la restricción real, dentro del bucle de `flex.items()` (tras la línea 174
  actual, `modelo.AddAtMostOne([z[(trab, g, a)], z[(trab, k, b)]])`, todavía dentro del mismo
  `for (trab, lunes), (candidatos, cuantos) in flex.items():`)**

```python
        dias_semana_trab = [lunes + timedelta(days=i) for i in range(7)]
        sab_t, dom_t = dias_semana_trab[5], dias_semana_trab[6]
        if (trab, sab_t) in plan and (trab, dom_t) in plan:
            # Sábado y domingo ya están fijados en plan (decididos en el paso A2, invariables
            # aquí) — igual que en el bloque de la Task 3, exige un par consecutivo libre entre
            # semana. z solo cubre los días candidatos; los demás días L-V de esta semana están
            # fijados fuera del modelo, así que su libre/trabajado es una constante, no una
            # variable.
            candidatos_por_dia = {g: s for g, s in candidatos}
            libres_l_v = []
            for i in range(5):
                dia = dias_semana_trab[i]
                s_dia = candidatos_por_dia.get(dia)
                if s_dia is not None and (trab, dia, s_dia) in z:
                    libre = modelo.NewBoolVar(f"librefinde_z_{trab}_{dia:%m%d}")
                    modelo.Add(z[(trab, dia, s_dia)] + libre == 1)
                else:
                    libre = 0 if (trab, dia) in plan else 1
                libres_l_v.append(libre)

            fijos = datos.config.dias_descanso_finde
            if fijos:
                idx = {nombre: i for i, nombre in enumerate(DIAS_LV)}
                i1, i2 = sorted(idx[n] for n in fijos)
                modelo.Add(libres_l_v[i1] + libres_l_v[i2] >= 2)
            else:
                pares_z = []
                for i in range(4):
                    a, b = libres_l_v[i], libres_l_v[i + 1]
                    if isinstance(a, int) and isinstance(b, int):
                        pares_z.append(1 if a and b else 0)
                        continue
                    par = modelo.NewBoolVar(f"parfinde_z_{trab}_{dias_semana_trab[i]:%m%d}")
                    modelo.Add(par <= a)
                    modelo.Add(par <= b)
                    pares_z.append(par)
                modelo.Add(sum(pares_z) >= 1)
```

Nota sobre `candidatos_por_dia = {g: s for g, s in candidatos}`: asume un único turno candidato
por fecha para este mixto esa semana. Comprobar contra los datos reales en el Step 4 — si algún
mixto tiene más de un turno candidato el mismo día (`candidatos` con fechas repetidas), este
`dict` se queda solo con el último y habría que agrupar por fecha en vez de sobrescribir.

- [ ] **Step 4: Verificar que el módulo importa sin errores y que no hay fechas repetidas en
  `candidatos` para el mismo mixto/semana en los datos reales**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
import residuo
print('residuo.py importa sin errores: OK')
"
```

Si al ejecutar el pipeline completo (Task 12) aparece algún mixto con fechas repetidas en
`candidatos` (la nota del Step 3), ajustar `candidatos_por_dia` para agrupar en vez de
sobrescribir antes de confiar en el resultado.

- [ ] **Step 5: Commit**

```bash
git add src/residuo.py
git commit -m "$(cat <<'EOF'
CP-SAT: la semana flexible del mixto respeta el descanso consecutivo

Las variables z pueden reactivar un día distinto al que base.py eligió
deliberadamente adyacente al conceder sábado+domingo — el conteo total
de días libres se mantenía pero no su consecutividad. Misma
restricción que la Task 3 (par fijo o disyunción de 4 pares
adyacentes), aplicada a la semana flexible cuando sábado+domingo ya
están fijados en plan.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Re-verificación end-to-end tras las Tasks 10-11

**Files:** ninguno (solo ejecución)

- [ ] **Step 1: Ejecutar el pipeline completo y confirmar 0 semanas nuevas sin par consecutivo**

Repetir exactamente el Step 1 y Step 2 de la Task 8 (pipeline completo + réplica inline
comparando contra `descansos_esqueleto`), sobre el código ya con las Tasks 10-11 aplicadas.

Esperado: `Pipeline completo: 0 semanas nuevas sin descanso consecutivo (N toleradas del
esqueleto). OK` — mismo formato que el Step 2 de la Task 8.

Si sigue habiendo semanas nuevas, repetir el diagnóstico de la Task 8 (clasificar por tipo de
trabajador, comparar contra `descansos_esqueleto`, identificar el mecanismo concreto) — no asumir
que las Tasks 10-11 cierran todo sin comprobarlo. Prestar atención especial a si el patrón de
fallos apunta a la nota de la Task 11 Step 3 (fechas repetidas en `candidatos`).

- [ ] **Step 2: Reportar el resultado**

No hay commit en este paso. Resume en la conversación: si el invariante ya se sostiene end-to-end,
cobertura final comparada con la Task 8 (referencia: 18285/18295), y cualquier cambio relevante en
los niveles del CP-SAT o en las cuotas de mixtos/patrones flexibles.

---

### Task 14 (amendment, descubierta en Task 12): `libranzas.py` — `_forzar_descanso_finde` también repara al cubridor

**Descubierto en Task 12** (segunda re-verificación end-to-end): de las 130 violaciones
originales, 128 las cerraron las Tasks 10-11; las 2 restantes son un séptimo mecanismo
preexistente y distinto — `_forzar_descanso_finde` (Task 5) nunca comprueba la semana propia de
un **cubridor** de plaza designada tras un traspaso de fase 1, solo la del titular que cede. Ver
el Addendum 2 en `docs/superpowers/specs/2026-08-26-descanso-consecutivo-finde-design.md`.

**Files:**
- Modify: `src/libranzas.py:561-602` (`_forzar_descanso_finde`)

**Interfaces:**
- Consumes: `Cesion.cubridor`, `Cesion.dias` (ya existen).

- [ ] **Step 1: Escribir el script de verificación (debe fallar: hoy no repara al cubridor)**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal, libranzas, horas
from libranzas import Registro, Cesion

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
cubridor = '71158833F'                      # id real, PAT_GRANDE_VALL (flexible)
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and datos.disponible(cubridor, f) and datos.disponible(cubridor, f - timedelta(days=1)))
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

# El cubridor asume sábado+domingo de una plaza designada (fase 1) y pierde su propio martes (el
# día con el que chocaba la ventana asumida) — quedan libres lunes y miércoles: NO consecutivos.
plan = {(cubridor, sabado): turno_finde, (cubridor, domingo): turno_finde,
        (cubridor, martes): turno_lv, (cubridor, jueves): turno_lv, (cubridor, viernes): turno_lv}
libro = horas.LibroHoras.desde_plan(datos, plan)
assert legal.descanso_finde_ok(datos, plan, cubridor, lunes) is False, 'lunes y miércoles ya deberían ser NO consecutivos'
reg = Registro()
# Cesion de fase 1: titular es OTRA persona real (quien cedió la plaza; el bucle exterior de
# _forzar_descanso_finde itera por id real vía ritmo.grupo_de, así que no vale un id inventado),
# cubridor es el afectado real cuya semana propia debe repararse.
otro_titular = '71117540E'                  # id real, fijo, sin relación con este escenario
reg.cesiones.append(Cesion(fase=1, titular=otro_titular, dias=[sabado, domingo], horas=16.0,
                           cubridor=cubridor, desalojadas=1, motivo=f'vacaciones de {otro_titular}'))

libranzas._forzar_descanso_finde(datos, plan, libro, reg, ritmos={})
assert legal.descanso_finde_ok(datos, plan, cubridor, lunes), (
    f'la semana del cubridor debía repararse: {plan}')
print('_forzar_descanso_finde: también repara la semana propia del cubridor — OK')
"
```

Expected: `AssertionError: la semana del cubridor debía repararse: ...` — hoy `tocadas`/el bucle
exterior solo indexan por `c.titular` ('OTRO_TITULAR', que ni siquiera tiene días en `plan`), así
que la semana rota del cubridor nunca se examina.

- [ ] **Step 2: Modificar `_forzar_descanso_finde` (líneas 561-602 actuales)**

Antes:
```python
def _forzar_descanso_finde(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro,
                           ritmos: dict[str, Ritmo]) -> None:
    """Tras fase1+fase2: si una semana de sábado+domingo trabajado que el pipeline SÍ tocó (le
    cedió al menos un día) sigue sin un par consecutivo libre, cede uno más para completarlo —
    aunque cueste una cesión de más de la que pedían solo las horas. Las semanas que nadie tocó se
    dejan como están, igual que el resto de reglas legales sobre un patrón heredado."""
    def lunes_de(f: date) -> date:
        return f - timedelta(days=f.weekday())

    tocadas = {(c.titular, lunes_de(f)) for c in reg.cesiones for f in c.dias}
    titulares = sorted({c.titular for c in reg.cesiones})
    for titular in titulares:
        if ritmo_mod.es_rigido(datos, ritmos, titular):
            continue
        lunes_de_titular = sorted({lunes_de(f) for (w, f) in plan if w == titular})
        for lunes in lunes_de_titular:
            if legal.descanso_finde_ok(datos, plan, titular, lunes):
                continue
            if (titular, lunes) not in tocadas:
                continue                                  # esqueleto puro: se tolera
            dias_semana = [lunes + timedelta(days=i) for i in range(5)]
            libres = [d for d in dias_semana if (titular, d) not in plan]
            trabajados = [d for d in dias_semana if (titular, d) in plan]
            adyacentes = [d for d in trabajados if any(abs((d - lb).days) == 1 for lb in libres)]
            mejor = None
            for candidato in (adyacentes or trabajados):
                libres_para_cubrir = _libres(datos, plan, libro, plan[(titular, candidato)],
                                             candidato, {titular})
                if libres_para_cubrir == 0:
                    continue
                if mejor is None or libres_para_cubrir > mejor[1]:
                    mejor = (candidato, libres_para_cubrir)
            if mejor is None:
                print(f"  aviso  {titular} semana del {lunes:%d/%m}: sábado+domingo sin par "
                      f"consecutivo y nadie puede cubrir el día que lo completaría")
                continue
            candidato, _ = mejor
            s = plan.pop((titular, candidato))
            libro.borra(titular, s)
            reg.cesiones.append(Cesion(fase=2, titular=titular, dias=[candidato],
                                       horas=datos.turnos[s].horas, cubridor=None,
                                       desalojadas=0, motivo="descanso de finde"))
```

Después (renombra `titular`/`titulares` a `afectado`/`afectados` a lo largo de la función, y
añade al cubridor a `tocadas` y al conjunto de gente a revisar):
```python
def _forzar_descanso_finde(datos: Datos, plan: Plan, libro: LibroHoras, reg: Registro,
                           ritmos: dict[str, Ritmo]) -> None:
    """Tras fase1+fase2: si una semana de sábado+domingo trabajado que el pipeline SÍ tocó (le
    cedió al menos un día) sigue sin un par consecutivo libre, cede uno más para completarlo —
    aunque cueste una cesión de más de la que pedían solo las horas. Las semanas que nadie tocó se
    dejan como están, igual que el resto de reglas legales sobre un patrón heredado.

    'Tocado' incluye tanto al titular que cede/está de vacaciones como al CUBRIDOR de una plaza
    designada (fase 1): el cubridor pierde sus propios días que chocan con la ventana asumida
    (`_aplicar_fase1`), y esa pérdida puede romperle su propio par consecutivo igual que una
    cesión de fase 2 se lo rompe a un titular."""
    def lunes_de(f: date) -> date:
        return f - timedelta(days=f.weekday())

    tocadas = {(c.titular, lunes_de(f)) for c in reg.cesiones for f in c.dias}
    tocadas |= {(c.cubridor, lunes_de(f)) for c in reg.cesiones if c.cubridor for f in c.dias}
    afectados = sorted({c.titular for c in reg.cesiones}
                       | {c.cubridor for c in reg.cesiones if c.cubridor})
    for afectado in afectados:
        if ritmo_mod.es_rigido(datos, ritmos, afectado):
            continue
        lunes_de_afectado = sorted({lunes_de(f) for (w, f) in plan if w == afectado})
        for lunes in lunes_de_afectado:
            if legal.descanso_finde_ok(datos, plan, afectado, lunes):
                continue
            if (afectado, lunes) not in tocadas:
                continue                                  # esqueleto puro: se tolera
            dias_semana = [lunes + timedelta(days=i) for i in range(5)]
            libres = [d for d in dias_semana if (afectado, d) not in plan]
            trabajados = [d for d in dias_semana if (afectado, d) in plan]
            adyacentes = [d for d in trabajados if any(abs((d - lb).days) == 1 for lb in libres)]
            mejor = None
            for candidato in (adyacentes or trabajados):
                libres_para_cubrir = _libres(datos, plan, libro, plan[(afectado, candidato)],
                                             candidato, {afectado})
                if libres_para_cubrir == 0:
                    continue
                if mejor is None or libres_para_cubrir > mejor[1]:
                    mejor = (candidato, libres_para_cubrir)
            if mejor is None:
                print(f"  aviso  {afectado} semana del {lunes:%d/%m}: sábado+domingo sin par "
                      f"consecutivo y nadie puede cubrir el día que lo completaría")
                continue
            candidato, _ = mejor
            s = plan.pop((afectado, candidato))
            libro.borra(afectado, s)
            reg.cesiones.append(Cesion(fase=2, titular=afectado, dias=[candidato],
                                       horas=datos.turnos[s].horas, cubridor=None,
                                       desalojadas=0, motivo="descanso de finde"))
```

Nota: la nueva `Cesion` de reparación sigue registrándose con `titular=afectado` (nunca
`cubridor=algo`) independientemente de si `afectado` era originalmente un titular o un cubridor —
es correcto: esta cesión nueva es siempre de fase 2, sin traspaso, sobre la persona que la sufre
directamente.

- [ ] **Step 3: Ejecutar el script de verificación de nuevo**

Mismo comando del Step 1.
Expected: `_forzar_descanso_finde: también repara la semana propia del cubridor — OK`

- [ ] **Step 4: Verificar que el caso ya cubierto (titular de fase 2, sin cubridor) sigue
  funcionando — repetir el Step 7 de la Task 5**

```bash
cd /home/samu/Documents/Universidad/HT-GROUP && PYTHONPATH=src python3 -c "
from datetime import timedelta
from cargar_datos import cargar
import legal, libranzas, horas
from libranzas import Registro, Cesion

datos = cargar()
turno_finde, turno_lv = 'VADN022', 'VADN001'
titular = '71158833F'
domingo = next(f for f in datos.lista_dias_calendario
               if datos.tipo_dia(f, datos.turnos[turno_finde].municipio) == 'DOM'
               and datos.disponible(titular, f) and datos.disponible(titular, f - timedelta(days=1)))
sabado = domingo - timedelta(days=1)
lunes = domingo - timedelta(days=6)
martes, miercoles, jueves, viernes = (lunes + timedelta(days=i) for i in (1, 2, 3, 4))

def plan_semana():
    return {(titular, sabado): turno_finde, (titular, domingo): turno_finde,
            (titular, martes): turno_lv, (titular, jueves): turno_lv, (titular, viernes): turno_lv}

plan = plan_semana()
libro = horas.LibroHoras.desde_plan(datos, plan)
reg = Registro()
reg.cesiones.append(Cesion(fase=2, titular=titular, dias=[miercoles], horas=8.0,
                           cubridor=None, desalojadas=0, motivo='ciclo entero'))
libranzas._forzar_descanso_finde(datos, plan, libro, reg, ritmos={})
assert legal.descanso_finde_ok(datos, plan, titular, lunes), 'debía quedar reparada (regresión)'
print('_forzar_descanso_finde: caso de titular sin cubridor sigue funcionando — OK')
"
```

Expected: `_forzar_descanso_finde: caso de titular sin cubridor sigue funcionando — OK`

- [ ] **Step 5: Commit**

```bash
git add src/libranzas.py
git commit -m "$(cat <<'EOF'
libranzas: _forzar_descanso_finde también repara al cubridor

Un cubridor de plaza designada (fase 1) puede perder su propio día
entre semana al asumir el ciclo de un titular ausente, rompiendo su
propio par consecutivo — pero el guard solo indexaba por c.titular,
nunca por c.cubridor. Encontrado en la Task 12 (2 de las 130
violaciones originales eran este séptimo mecanismo, distinto de los
que cerraron las Tasks 10-11). tocadas y el bucle exterior ahora
incluyen también al cubridor.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Tercera y última re-verificación end-to-end

**Files:** ninguno (solo ejecución)

- [ ] **Step 1: Ejecutar el pipeline completo y confirmar 0 semanas nuevas sin par consecutivo**

Repetir exactamente el método de las Tasks 8 y 12 (pipeline completo + réplica inline comparando
contra `descansos_esqueleto`), sobre el código con la Task 14 aplicada.

Esperado: `Pipeline completo: 0 semanas nuevas sin descanso consecutivo (N toleradas del
esqueleto). OK`.

Si sigue habiendo semanas nuevas, repetir el diagnóstico (clasificar por tipo, comparar contra
`descansos_esqueleto`, trazar contra qué paso del pipeline las rompe) — no asumir que la Task 14
cierra todo sin comprobarlo. Con tres rondas de diagnóstico ya hechas (Tasks 8, 12, y esta), si
aparece un OCTAVO mecanismo distinto, es una señal de que puede haber más — considerar en ese caso
parar y consultar con el usuario antes de seguir añadiendo tasks de una en una.

- [ ] **Step 2: Reportar el resultado**

No hay commit en este paso. Resume en la conversación: si el invariante ya se sostiene end-to-end
al 100%, cobertura final comparada con la Task 12 (referencia: 18277/18295), y cualquier cambio
relevante en los niveles del CP-SAT o en las cuotas de mixtos/patrones flexibles.

---

### Task 16: Spike de rendimiento del CP-SAT (con y sin la restricción nueva)

**Files:** ninguno (solo ejecución, scripts en el scratchpad de la sesión)

Mismo método que el spike de `domingo_ok`: aislar el efecto de ESTA restricción del ruido de
máquina, comparando el nivel 1 del CP-SAT con y sin ella, dos reconstrucciones limpias seguidas.

- [ ] **Step 1: Copiar `residuo.py` al scratchpad de la sesión y envolverlo con dos interruptores**

```bash
mkdir -p /tmp/claude-descanso-spike
cp /home/samu/Documents/Universidad/HT-GROUP/src/residuo.py /tmp/claude-descanso-spike/residuo_ab.py
```

Editar `/tmp/claude-descanso-spike/residuo_ab.py`:

1. Añadir `import os` junto a las demás importaciones del principio del archivo.
2. Envolver el bloque nuevo de la Task 3 (el que empieza en
   `# descanso consecutivo tras un finde completo: restricción dura, mismo nivel que arriba`,
   dentro del `for w in pool:`) en `if os.environ.get("AB_DESCANSO", "1") == "1":`, indentando
   todo su contenido un nivel más — igual que ya se hizo con el bloque de `domingo_ok` en su
   propio spike (verificar contra el archivo real: debe quedar como un `if` que engloba desde el
   `for lunes in {forma.lunes_de(f) ...}:` hasta el final del bloque, antes del siguiente
   `for w in pool:` de C9).
3. Justo después de `modelo.Add(cubiertas >= mejor)` (el final del bloque del nivel 1, antes del
   comentario `# -- Nivel 2`), añadir:
   ```python
       if os.environ.get("AB_SOLO_NIVEL1"):
           return {}
   ```

- [ ] **Step 2: Escribir el script conductor, reconstruyendo el estado fresco para cada variante**

```python
# /tmp/claude-descanso-spike/ab_descanso.py
import os
import sys
import time

sys.path.insert(0, "/home/samu/Documents/Universidad/HT-GROUP/src")
sys.path.insert(0, "/tmp/claude-descanso-spike")

from cargar_datos import cargar
import base, horas, libranzas, forma, ritmo
import residuo_ab


def construir_hasta_D():
    datos = cargar()
    plan = base.construir(datos)
    libro = horas.LibroHoras.desde_plan(datos, plan)
    protegidos, flexibles = base.colocar_mixtos(datos, plan, libro)
    ritmos = ritmo.medir(datos, plan)
    libranzas.ceder(datos, plan, libro, protegidos, ritmos)
    rep = forma.repartir(datos, plan)
    return datos, plan, libro, rep, flexibles


os.environ["AB_SOLO_NIVEL1"] = "1"

for etiqueta, valor_env in [("CON restricción de descanso (Task 3, código actual)", "1"),
                            ("SIN restricción de descanso", "0")]:
    os.environ["AB_DESCANSO"] = valor_env
    print(f"\n{'=' * 70}\n{etiqueta}\n{'=' * 70}")
    t0 = time.time()
    datos, plan, libro, rep, flexibles = construir_hasta_D()
    t1 = time.time()
    print(f"  setup (A+A2+B+C): {t1 - t0:.0f}s")
    residuo_ab.resolver(datos, plan, libro, rep, flexibles, segundos=300, hilos=8)
    print(f"  tiempo total nivel 1 + setup: {time.time() - t0:.0f}s")
```

- [ ] **Step 3: Ejecutar la comparación en background, con espera activa hasta que termine**

```bash
source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null
conda activate ortools_env
nohup python3 /tmp/claude-descanso-spike/ab_descanso.py > /tmp/claude-descanso-spike/ab_descanso.log 2>&1 &
PID=$!
while kill -0 "$PID" 2>/dev/null; do sleep 15; done
cat /tmp/claude-descanso-spike/ab_descanso.log
```

Expected: dos bloques de salida, uno por variante, cada uno con la línea
`nivel 1 cobertura: N (OPTIMAL|FEASIBLE, Ts)`. Comparar tiempo, estado y valor entre ambos.

- [ ] **Step 4: Reportar el resultado**

No hay commit en este paso. Documentar en el spec (`docs/superpowers/specs/2026-08-26-descanso-
consecutivo-finde-design.md`, sección «Consecuencias esperadas») el tiempo/estado/valor medido
con y sin la restricción, igual que se hizo para `domingo_ok` — sustituyendo la expectativa a
priori ("es razonable esperar...") por la cifra real medida. Si el impacto es severo (agota el
tope de 300s con una caída de valor grande), plantear al usuario si hace falta una Task 10 (subir
el tope de segundos solo al nivel 1, o buscar una formulación más barata) antes de dar el plan por
cerrado — mismo patrón de decisión que se siguió con `domingo_ok`.

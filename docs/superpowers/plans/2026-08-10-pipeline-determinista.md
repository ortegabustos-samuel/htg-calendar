# Pipeline determinista de cinco pasos — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sustituir `modelo.py` (CP-SAT) y `pulido.py` por un procedimiento determinista de cinco pasos que produzca el cuadrante anual y pueda explicarse entero, celda a celda.

**Architecture:** Ocho módulos pequeños encadenados. Los pasos 1–4 solo añaden asignaciones a un objeto `Plan`; el paso 5 es el único que deshace. Un `Legal` compartido arbitra el convenio antes de cada asignación y se verifica el plan entero después de cada paso. Cada asignación queda registrada en un libro de decisiones que sale a CSV.

**Tech Stack:** Python 3.12 (conda env `ortools_env`), stdlib únicamente en los módulos nuevos. `openpyxl` solo en `salida.py`, que no se toca. **OR-Tools deja de usarse en el motor**; permanece instalado solo para el benchmark contra la rama base.

## Global Constraints

- **Intérprete:** `/home/samu/anaconda3/envs/ortools_env/bin/python`. No usar `python` a secas — el `python` del sistema revienta al importar `ortools` (conflicto de protobuf).
- **Idioma:** código, comentarios, docstrings y mensajes en **castellano**, como el resto del proyecto.
- **Tests:** scripts planos en `tests/`, sin pytest (no está instalado). Cada test es un `def main() -> int` con `assert`, imprime `OK …` y termina con `raise SystemExit(main())`. Se ejecuta `python tests/test_X.py`. Es la convención ya establecida por `tests/test_ausencias.py`.
- **Moneda del objetivo anual:** `Turno.horas` (horas **legales** computadas). `Turno.horas_consumo` **no se usa** en el motor nuevo.
- **Objetivo anual:** `datos.config.horas_objetivo` (1776), escalado por `Trabajador.factor_jornada`. Nunca la constante a pelo.
- **Convenio:** `datos.config.rmin` (12), `hmax7` (48), `cmax` (6), `cmax_pool` (5). Nunca constantes a pelo.
- **Determinismo:** todo orden de iteración debe ser total. Al recorrer conjuntos o diccionarios, ordenar siempre — desempate final por `id_trab` ascendente. Un `set` sin ordenar en un bucle que asigna es un bug.
- **Sin `random`, sin `datetime.now()`, sin dependencia del orden de inserción de un `dict` construido a partir de un `set`.**
- **No tocar:** `cargar_datos.py`, ni ningún CSV de `data/input/`, ni `config.toml`. De `salida.py`, `validar_datos.py` y `diagnostico.py` se cambia **exclusivamente su línea de `import`** en la Task 0, y nada más en ningún otro momento.
- **Espec:** `docs/superpowers/specs/2026-08-10-pipeline-determinista-design.md`.

## Estructura de ficheros

| fichero | responsabilidad |
|---|---|
| `src/plan.py` | `Plan` (asignaciones + libranzas + huecos) y `Decision` (libro). Sin lógica de negocio. |
| `src/legal.py` | `Legal`: único juez del convenio. `puede()` y `verificar()`. |
| `src/deuda.py` | `Deuda`: colas ordenadas por deuda de horas/sábados/domingos/festivos. |
| `src/libranzas.py` | Paso 1. Reparte las cesiones por exceso. |
| `src/criticos.py` | Paso 2. Cobertura de líneas `prioridad ≥ 2`. |
| `src/rotacion.py` | Paso 3. Estampa patrones y fijos; adopción de filas huérfanas. |
| `src/reparto.py` | Paso 4. Pool semana a semana, escasez dentro, deuda decide. |
| `src/reparacion.py` | Paso 5. Cinco movimientos, itera hasta punto fijo. |
| `src/generar_anual.py` | Orquesta, escribe `decisiones.csv`, llama a `salida.generar_anual`. |
| `src/calendario.py` | Fechas: `semana`, `rango_fechas`, `fila_patron`, `turno_prescrito`. |
| `src/metricas.py` | Métricas e informe: constantes, `peso_cobertura`, `jornada_minutos`, helpers de patrón. |

Se **borran** al final: `src/modelo.py`, `src/pulido.py`.

**Por qué existe la Task 0.** `salida.py`, `validar_datos.py` y `diagnostico.py` importan diez símbolos de `modelo.py`, y ninguno tiene que ver con CP-SAT: son utilidades de fecha, métricas y pesos de informe que acabaron dentro del fichero del solver por acumulación histórica. Si no se mudan primero, borrar `modelo.py` en la Task 11 deja el proyecto sin arrancar.

## Interfaces que ya existen y NO se tocan

```python
# cargar_datos.py
DIAS = ["lun","mar","mie","jue","vie","sab","dom"]   # índice = date.weekday()
LIBRE = "LIBRE"
cargar() -> Datos

Datos.turnos: dict[str, Turno]              # Turno: .horas .horas_consumo .prioridad .dem
                                            #        .municipio .lv .sab .dom .fes .tipo
                                            #        .hora_entrada .hora_salida
Datos.trabajadores: dict[str, Trabajador]   # Trabajador: .id .tipo .patron .vacaciones
                                            #        .linea .factor_jornada .grupo .fila_inicial
Datos.patrones: dict[str, list[dict[str,str]]]   # patron -> [ {dia: turno|LIBRE} ], indexado por fila
Datos.offsets: dict[str, int]               # trabajador de patrón -> fila de arranque
Datos.capacidades: dict[tuple[str,str], Capacidad]   # Capacidad: .lv .sab .dom .fest .v
Datos.config: Config                        # .anio .horas_objetivo .rmin .hmax7 .cmax .cmax_pool
Datos.inicio / .fin / .fechas
Datos.es_festivo(f, municipio) -> bool
Datos.tipo_dia(f, municipio) -> str         # "LV" | "SAB" | "DOM" | "FEST"
Datos.opera(turno_id, f) -> bool
Datos.disponible(trab_id, f) -> bool        # False si está de vacaciones
Datos.elegible(trab_id, turno_id, f) -> tuple[bool, bool]   # (elegible, es_refuerzo)

# salida.py
generar_anual(datos: Datos, plan: dict[tuple[str, date], str]) -> None
```

**`Datos.patrones[patron]` está indexado por posición de fila.** La fila que le toca a `w` el día `f` es:

```python
filas = datos.patrones[trabajador.patron]
fila = (datos.offsets[w] + (f - datos.inicio).days // 7) % len(filas)
turno = filas[fila][DIAS[f.weekday()]]
```

---

### Task 0: mudar las utilidades compartidas fuera de `modelo.py`

**Files:**
- Create: `src/calendario.py`
- Create: `src/metricas.py`
- Modify: `src/salida.py:22` (solo la línea de import)
- Modify: `src/validar_datos.py:402` (solo la línea de import)
- Modify: `src/diagnostico.py:28` (solo la línea de import)
- Test: `tests/test_mudanza.py`

**Interfaces:**
- Consumes: nada nuevo
- Produces:
  - `calendario.semana(f: date) -> tuple[int, int]` (de `modelo.py:186`)
  - `calendario.rango_fechas(inicio: date, fin: date) -> list[date]` (de `modelo.py:181`)
  - `metricas.JORNADA_LOCALIZADO_SEMANA = 40` (de `modelo.py:40`)
  - `metricas.METRICAS` (de `modelo.py:111`), `metricas.LAMBDA` (de `modelo.py:112`)
  - `metricas.peso_cobertura(t: Turno) -> int` (de `modelo.py:86`)
  - `metricas.lineas_localizadas(datos) -> set[str]` (de `modelo.py:192`)
  - `metricas.jornada_minutos(datos, plan, fechas=None) -> dict[str, int]` (de `modelo.py:201`)
  - `metricas._patrones_uvi(datos) -> set[str]` (de `modelo.py:1291`)
  - `metricas._patrones_noche(datos) -> set[str]` (de `modelo.py:1303`)

**Qué es esta tarea:** una **mudanza literal**, no una reescritura. Copia cada símbolo con su cuerpo y su docstring **intactos** desde `modelo.py` a su nuevo módulo, y arregla los imports. El comportamiento de `salida.py`, `validar_datos.py` y `diagnostico.py` debe quedar **idéntico** — si cambia una sola salida, la mudanza está mal hecha.

`metricas.jornada_minutos` sigue usando la moneda de CONSUMO (`horas_consumo`, `JORNADA_LOCALIZADO_SEMANA`). Eso es correcto y no contradice al resto del proyecto: el motor nuevo retira esa moneda del **libro anual**, pero `salida` y `diagnostico` la usan para **informar**, que es otra cosa. No la toques.

`metricas.py` importa de `calendario` lo que necesite (`semana`); `calendario.py` no importa de `metricas` (sin ciclos).

- [ ] **Step 1: Capturar la salida ACTUAL como referencia**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python src/diagnostico.py > /tmp/diagnostico_antes.txt 2>&1
/home/samu/anaconda3/envs/ortools_env/bin/python src/validar_datos.py > /tmp/validar_antes.txt 2>&1
wc -l /tmp/diagnostico_antes.txt /tmp/validar_antes.txt
```

Ambos deben tener contenido. Son la referencia de que la mudanza no cambia nada.

- [ ] **Step 2: Escribir el test que falla**

```python
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
```

- [ ] **Step 3: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_mudanza.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'calendario'`

- [ ] **Step 4: Crear `src/calendario.py` con los dos símbolos de fecha**

Copia `rango_fechas` (`modelo.py:181-184`) y `semana` (`modelo.py:186-189`) **verbatim**, con sus docstrings. Encabeza el módulo con:

```python
"""Utilidades de fecha compartidas por todo el proyecto.

Vivían en `modelo.py` por acumulación histórica, no por diseño: no tienen nada que ver con el
solver y las consultan `salida`, `validar_datos` y los cinco pasos del pipeline."""
```

- [ ] **Step 5: Crear `src/metricas.py` con los ocho símbolos restantes**

Copia **verbatim** desde `modelo.py`: `JORNADA_LOCALIZADO_SEMANA` (l.40), `peso_cobertura` (l.86), `METRICAS` (l.111), `LAMBDA` (l.112), `lineas_localizadas` (l.192), `jornada_minutos` (l.201), `_patrones_uvi` (l.1291), `_patrones_noche` (l.1303). Mantén los comentarios que llevan encima. Encabeza con:

```python
"""Métricas y pesos de INFORME: lo que se cuenta y cómo se pondera al reportar.

Ojo a la moneda: `jornada_minutos` cuenta en horas de CONSUMO (una semana de localizado ocupa
`JORNADA_LOCALIZADO_SEMANA` entera). El motor nuevo NO usa esa moneda para el libro anual —el
objetivo de 1776 es una cifra de convenio y se lleva en horas legales—, pero los informes sí la
enseñan, que es otra cosa. Las dos contabilidades conviven a propósito."""
```

- [ ] **Step 6: Cambiar las tres líneas de import**

`src/salida.py:22-24`, sustituye el bloque `from modelo import (...)` por:

```python
from calendario import semana
from metricas import (JORNADA_LOCALIZADO_SEMANA, LAMBDA, METRICAS, _patrones_uvi,
                      jornada_minutos, lineas_localizadas, peso_cobertura)
```

`src/validar_datos.py:402`, sustituye por:

```python
    from calendario import rango_fechas                                   # noqa: PLC0415
    from metricas import _patrones_noche, _patrones_uvi, jornada_minutos  # noqa: PLC0415
```

`src/diagnostico.py:28`, sustituye por:

```python
from metricas import jornada_minutos
```

**No cambies nada más en esos tres ficheros.**

- [ ] **Step 7: Verificar que la salida es IDÉNTICA**

```bash
/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_mudanza.py
/home/samu/anaconda3/envs/ortools_env/bin/python src/diagnostico.py > /tmp/diagnostico_despues.txt 2>&1
/home/samu/anaconda3/envs/ortools_env/bin/python src/validar_datos.py > /tmp/validar_despues.txt 2>&1
diff /tmp/diagnostico_antes.txt /tmp/diagnostico_despues.txt && echo "DIAGNOSTICO IDENTICO"
diff /tmp/validar_antes.txt /tmp/validar_despues.txt && echo "VALIDAR IDENTICO"
```
Expected: el test pasa y **los dos `diff` salen vacíos**. Si alguno difiere, la mudanza no fue literal — arréglalo antes de commitear.

- [ ] **Step 8: Commit**

```bash
git add src/calendario.py src/metricas.py src/salida.py src/validar_datos.py \
        src/diagnostico.py tests/test_mudanza.py
git commit -m "muda a calendario.py y metricas.py lo que salida y validar usan de modelo

Diez simbolos que salida.py, validar_datos.py y diagnostico.py importaban de
modelo.py y que no tienen nada que ver con CP-SAT: fechas, metricas y pesos de
informe que acabaron dentro del fichero del solver por acumulacion historica.
Sin esta mudanza, borrar modelo.py deja el proyecto sin arrancar.

Mudanza literal: diagnostico y validar producen una salida identica byte a byte.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 1: `calendario.py` (ampliación) y `plan.py` — el sustrato

**Files:**
- Modify: `src/calendario.py` (**ya existe** desde la Task 0 con `semana` y `rango_fechas`; se le **añaden** dos funciones, no se reescribe)
- Create: `src/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `cargar_datos.LIBRE`, `calendario.semana` (de la Task 0)
- Produces:
  - `calendario.fila_patron(datos, w: str, f: date) -> int | None`
  - `calendario.turno_prescrito(datos, w: str, f: date) -> str | None` — turno que el patrón/línea fija prescribe ese día, `None` si LIBRE o si no es de patrón/fijo
  - `plan.Decision` (frozen dataclass), `plan.Hueco` (frozen dataclass), `plan.Plan`
  - `Plan.asignar(w, f, turno, paso, regla, descartados=()) -> None`
  - `Plan.ceder(w, f, paso, regla) -> None`
  - `Plan.liberar(w, f, paso, regla) -> str | None`
  - `Plan.turno_de(w, f) -> str | None`
  - `Plan.cedido(w, f) -> bool`
  - `Plan.ocupado(w, f) -> bool`
  - `Plan.cubierto(f, turno) -> int`
  - `Plan.dias_de(w) -> list[date]` — fechas en que TRABAJA, ordenadas; las cesiones no cuentan
  - `Plan.hueco(f, turno, motivo) -> None`
  - `Plan.asignaciones() -> dict[tuple[str, date], str]`
  - `Plan.libro: list[Decision]`, `Plan.huecos: list[Hueco]`

- [ ] **Step 1: Escribir el test que falla**

```python
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
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_plan.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'calendario'`

- [ ] **Step 3: AÑADIR a `src/calendario.py` las dos funciones de patrón**

`semana` y `rango_fechas` ya están ahí desde la Task 0 — **no las reescribas ni las dupliques**. Añade al final del módulo, y amplía el import de `cargar_datos` a `DIAS, LIBRE, Datos`:

```python
def fila_patron(datos: Datos, w: str, f: date) -> int | None:
    """Fila de `patrones.csv` que le toca a `w` el día `f`, o None si no es de patrón.

    El trabajador avanza una fila por semana desde el LUNES del arranque; `datos.offsets` dice en
    cuál empieza (es lo que da continuidad entre años, ver `cargar_datos.offsets_patron`).

    OJO al ancla: es el lunes de la semana del 1 de enero, NO el 1 de enero. El año no empieza en
    lunes —2026 empieza en jueves—, así que anclar en `datos.inicio` haría que la rotación cambiase
    de fila los jueves y desplazaría el patrón entero a partir del 5 de enero. Es el mismo ancla
    que usa `modelo.py:702` y el que documenta CLAUDE.md."""
    trab = datos.trabajadores[w]
    if trab.tipo != "patron" or not trab.patron:
        return None
    filas = datos.patrones.get(trab.patron)
    if not filas:
        return None
    ancla = datos.inicio - timedelta(days=datos.inicio.weekday())   # lunes de la semana ancla
    return (datos.offsets[w] + (f - ancla).days // 7) % len(filas)


def turno_prescrito(datos: Datos, w: str, f: date) -> str | None:
    """Turno que la ROTACIÓN (patrón) o la LÍNEA (fijo) prescribe a `w` el día `f`.

    None si ese día su patrón dice LIBRE, si el turno no opera esa fecha, o si el trabajador no es
    ni de patrón ni fijo (los mixtos y correturnos no tienen nada prescrito: son el pool)."""
    trab = datos.trabajadores[w]
    if trab.tipo == "fijo":
        if not trab.linea or not datos.opera(trab.linea, f):
            return None
        return trab.linea
    fila = fila_patron(datos, w, f)
    if fila is None:
        return None
    s = datos.patrones[trab.patron][fila].get(DIAS[f.weekday()])
    if not s or s == LIBRE or s not in datos.turnos or not datos.opera(s, f):
        return None
    return s
```

- [ ] **Step 4: Escribir `src/plan.py`**

```python
"""El calendario en construcción y su LIBRO DE DECISIONES.

No tiene lógica de negocio: no sabe qué es legal ni qué es justo. Solo guarda quién hace qué, quién
cede, qué se quedó sin cubrir, y —esto es lo que lo diferencia del `dict` que usaba el modelo
anterior— POR QUÉ. El libro es lo que hace el cuadrante defendible: ante cualquier celda hay una
frase que la justifica y la lista de quién más podía haberlo hecho."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Decision:
    """Una línea del libro. `turno=None` es una cesión; `liberado` marca lo que el paso 5 deshizo."""
    trabajador: str
    fecha: date
    turno: str | None
    paso: str
    regla: str
    descartados: tuple[str, ...] = ()
    liberado: bool = False


@dataclass(frozen=True)
class Hueco:
    fecha: date
    turno: str
    motivo: str


@dataclass
class Plan:
    _asign: dict[tuple[str, date], str] = field(default_factory=dict)
    _cesiones: set[tuple[str, date]] = field(default_factory=set)
    libro: list[Decision] = field(default_factory=list)
    huecos: list[Hueco] = field(default_factory=list)

    # -- Escritura ---------------------------------------------------------- #
    def asignar(self, w: str, f: date, turno: str, paso: str, regla: str,
                descartados: tuple[str, ...] = ()) -> None:
        if (w, f) in self._asign or (w, f) in self._cesiones:
            raise ValueError(f"{w} ya tiene algo el {f}: "
                             f"{self._asign.get((w, f), 'CESION')}")
        self._asign[(w, f)] = turno
        self.libro.append(Decision(w, f, turno, paso, regla, descartados))

    def ceder(self, w: str, f: date, paso: str, regla: str) -> None:
        """Marca el día como LIBRADO: el trabajador no hará lo que su rotación prescribe."""
        if (w, f) in self._asign or (w, f) in self._cesiones:
            raise ValueError(f"{w} ya tiene algo el {f}")
        self._cesiones.add((w, f))
        self.libro.append(Decision(w, f, None, paso, regla))

    def liberar(self, w: str, f: date, paso: str, regla: str) -> str | None:
        """Deshace lo del día y devuelve el turno que había. SOLO el paso 5 puede llamarlo."""
        turno = self._asign.pop((w, f), None)
        self._cesiones.discard((w, f))
        self.libro.append(Decision(w, f, turno, paso, regla, liberado=True))
        return turno

    def hueco(self, f: date, turno: str, motivo: str) -> None:
        self.huecos.append(Hueco(f, turno, motivo))

    # -- Consulta ----------------------------------------------------------- #
    def turno_de(self, w: str, f: date) -> str | None:
        return self._asign.get((w, f))

    def cedido(self, w: str, f: date) -> bool:
        return (w, f) in self._cesiones

    def ocupado(self, w: str, f: date) -> bool:
        """El día está decidido: o trabaja, o cede. En ambos casos no admite nada más."""
        return (w, f) in self._asign or (w, f) in self._cesiones

    def cubierto(self, f: date, turno: str) -> int:
        return sum(1 for (_, d), s in self._asign.items() if d == f and s == turno)

    def dias_de(self, w: str) -> list[date]:
        """Fechas en que `w` TRABAJA, ordenadas. Las cesiones no cuentan."""
        return sorted(d for (t, d) in self._asign if t == w)

    def asignaciones(self) -> dict[tuple[str, date], str]:
        """Copia del calendario en el formato que come `salida.generar_anual`."""
        return dict(self._asign)
```

- [ ] **Step 5: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_plan.py`
Expected: PASS, imprime `OK  plan · 4 decisiones · 1 huecos`

- [ ] **Step 6: Commit**

```bash
git add src/calendario.py src/plan.py tests/test_plan.py
git commit -m "plan y calendario: el sustrato del pipeline determinista

El Plan guarda quien hace que, quien cede y que se quedo sin cubrir, y ademas
POR QUE: cada escritura deja una linea en el libro de decisiones con la regla
que la justifica y quien mas podia haberlo hecho. Es lo que hace el cuadrante
defendible celda a celda.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `legal.py` — el juez único del convenio

**Files:**
- Create: `src/legal.py`
- Test: `tests/test_legal.py`

**Interfaces:**
- Consumes: `plan.Plan`, `calendario.semana`
- Produces:
  - `legal.Legal(datos: Datos)`
  - `Legal.pares_c4: set[tuple[str, str]]` — pares de turnos exentos de las 12 h
  - `Legal.puede(plan, w, f, turno) -> tuple[bool, str]` — `(True, "")` o `(False, motivo)`
  - `Legal.verificar(plan) -> list[str]` — lista de infracciones; vacía = plan legal
  - `Legal.tope_dias(w) -> int` — `cmax_pool` si es mixto/correturno, `cmax` si no

`_pares_pactados` se porta desde `modelo.py:487`. Es la única exención legal que sobrevive: los localizados 24 h (22:00→22:00) incumplen C4 por diseño, y la exención es **del par de turnos y de la rotación**, no del trabajador — la lista es única para toda la plantilla, porque quien cubre una línea de localizado debe poder hacer la secuencia entera igual que su titular.

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
"""Legal: C4 (12h), C5 (dias/semana) y C6 (48h/semana), y la exencion de los pares pactados."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from legal import Legal
from plan import Plan


def main() -> int:
    datos = cargar()
    ley = Legal(datos)
    p = Plan()

    # Los pares pactados existen y contienen el localizado consigo mismo: VADU47127 es
    # 22:00->22:00, encadena con 0 h de descanso y su rotacion lo exige.
    assert ("VADU47127", "VADU47127") in ley.pares_c4, "el localizado debe estar exento de C4"

    # C4: una noche seguida de una manana del dia siguiente no llega a 12 h de descanso.
    # VADN171 sale a las 08:00, VADN173 entra a las 07:00 -> 23 h, eso SI cabe.
    # Buscamos un par real que NO quepa: noche 23:00-08:00 y tarde que entre antes de las 20:00.
    lun, mar = date(2026, 3, 16), date(2026, 3, 17)
    w = next(x.id for x in datos.trabajadores.values() if x.tipo == "correturno")
    p.asignar(w, lun, "VADN171", "test", "montaje")       # 23:00 -> 08:00 del martes
    ok, motivo = ley.puede(p, w, mar, "VADN171")          # otra noche: 23:00 del martes
    assert ok, f"noche+noche son 15 h de descanso, deberia caber: {motivo}"

    # C5: el tope del pool es cmax_pool (5), no cmax (6).
    assert ley.tope_dias(w) == datos.config.cmax_pool
    fijo = next(x.id for x in datos.trabajadores.values() if x.tipo == "fijo")
    assert ley.tope_dias(fijo) == datos.config.cmax

    # Llenamos la semana del pool hasta el tope y comprobamos que el siguiente dia se bloquea.
    p2 = Plan()
    dias = [date(2026, 3, 16) + timedelta(days=i) for i in range(7)]
    for d in dias[:datos.config.cmax_pool]:
        p2.asignar(w, d, "VADN001", "test", "montaje")
    ok, motivo = ley.puede(p2, w, dias[datos.config.cmax_pool], "VADN001")
    assert not ok and "C5" in motivo, f"deberia bloquear por C5, dijo: {ok} {motivo}"

    # El plan que acabamos de construir a mano es legal salvo por ese tope; verificar() lo dice.
    infracciones = ley.verificar(p2)
    assert infracciones == [], infracciones

    # Y un plan que SI infringe se detecta.
    p3 = Plan()
    for d in dias[:datos.config.cmax_pool + 1]:
        p3.asignar(w, d, "VADN001", "test", "montaje")
    assert any("C5" in x for x in ley.verificar(p3)), ley.verificar(p3)

    print(f"OK  legal · {len(ley.pares_c4)} pares exentos de C4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_legal.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'legal'`

- [ ] **Step 3: Escribir `src/legal.py`**

```python
"""JUEZ ÚNICO del convenio. Todos los pasos preguntan aquí antes de asignar.

Existe porque la legalidad ACOPLA los pasos: `rmin`, `hmax7` y `cmax` son restricciones sobre la
semana de UNA persona, y un turno asignado en el paso 2 se come el presupuesto de esa persona para
el paso 4. Con un juez compartido y una verificación tras cada paso, un incumplimiento se detecta
en el paso que lo causó. En la rama base esto se manifestaba como ventanas INFEASIBLE meses
después."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from calendario import semana
from cargar_datos import DIAS, LIBRE, Datos
from plan import Plan


class Legal:
    def __init__(self, datos: Datos) -> None:
        self.datos = datos
        self.cfg = datos.config
        self.pares_c4 = self._pares_pactados()

    # -- Exención ----------------------------------------------------------- #
    def _pares_pactados(self) -> set[tuple[str, str]]:
        """Encadenamientos que la rotación PACTADA contiene y que quedan exentos de C4.

        Es la ÚNICA exención legal del sistema. Los localizados 24 h (22:00→22:00) incumplen C4 por
        diseño: VADU47127 encadena consigo mismo con 0 h de descanso y PAT_MEDINA llega a −11 h.
        La exención es del PAR DE TURNOS y de la rotación, no del trabajador: quien cubre una línea
        de localizado hace la secuencia de ESE patrón y debe poder hacerla entera igual que su
        titular. Cualquier otro encadenamiento sí exige las 12 h."""
        pares: set[tuple[str, str]] = set()

        def ocupado(s: str | None) -> bool:
            return bool(s) and s != LIBRE and s in self.datos.turnos

        for filas in self.datos.patrones.values():
            total = len(filas)
            for k, fila in enumerate(filas):
                for j, dia in enumerate(DIAS):
                    s1 = fila.get(dia)
                    # el domingo encadena con el lunes de la fila SIGUIENTE de la rotación
                    siguiente = fila if j < 6 else filas[(k + 1) % total]
                    s2 = siguiente.get(DIAS[(j + 1) % 7])
                    if ocupado(s1) and ocupado(s2):
                        pares.add((s1, s2))
        return pares

    # -- Consultas ---------------------------------------------------------- #
    def tope_dias(self, w: str) -> int:
        """C5: días máximos por semana ISO. La plantilla FLEXIBLE tiene su propio tope."""
        tipo = self.datos.trabajadores[w].tipo
        return self.cfg.cmax_pool if tipo in ("mixto", "correturno") else self.cfg.cmax

    def _descanso_h(self, s_prev: str, f_prev: date, s_next: str, f_next: date) -> float:
        """Horas entre la SALIDA de `s_prev` y la ENTRADA de `s_next`."""
        t_prev, t_next = self.datos.turnos[s_prev], self.datos.turnos[s_next]
        sale = datetime.combine(f_prev, t_prev.hora_salida)
        if t_prev.hora_salida <= t_prev.hora_entrada:      # cruza medianoche
            sale += timedelta(days=1)
        entra = datetime.combine(f_next, t_next.hora_entrada)
        return (entra - sale).total_seconds() / 3600.0

    def puede(self, plan: Plan, w: str, f: date, turno: str) -> tuple[bool, str]:
        """¿Puede `w` hacer `turno` el día `f` sin romper el convenio? (ok, motivo)."""
        if plan.ocupado(w, f):
            return (False, "el dia ya esta decidido")
        if not self.datos.disponible(w, f):
            return (False, "de vacaciones")

        dias = plan.dias_de(w)
        sem = semana(f)

        # C5 — días por semana ISO
        en_sem = [d for d in dias if semana(d) == sem]
        if len(en_sem) + 1 > self.tope_dias(w):
            return (False, f"C5: superaria {self.tope_dias(w)} dias en la semana {sem[1]}")

        # C6 — horas legales por semana ISO
        horas = sum(self.datos.turnos[plan.turno_de(w, d)].horas for d in en_sem)
        if horas + self.datos.turnos[turno].horas > self.cfg.hmax7:
            return (False, f"C6: superaria {self.cfg.hmax7} h en la semana {sem[1]}")

        # C4 — descanso mínimo con el día anterior y el siguiente
        for otro in (f - timedelta(days=1), f + timedelta(days=1)):
            s_otro = plan.turno_de(w, otro)
            if s_otro is None:
                continue
            antes, despues = (s_otro, turno) if otro < f else (turno, s_otro)
            f_antes, f_despues = (otro, f) if otro < f else (f, otro)
            if (antes, despues) in self.pares_c4:
                continue                       # encadenamiento pactado: exento
            if self._descanso_h(antes, f_antes, despues, f_despues) < self.cfg.rmin:
                return (False, f"C4: menos de {self.cfg.rmin} h de descanso con {s_otro} del {otro}")

        return (True, "")

    def verificar(self, plan: Plan) -> list[str]:
        """Repasa el plan ENTERO. Lista vacía = legal. Se llama tras cada paso."""
        fallos: list[str] = []
        por_trab: dict[str, list[date]] = {}
        for (w, f), _ in sorted(plan.asignaciones().items()):
            por_trab.setdefault(w, []).append(f)

        for w in sorted(por_trab):
            dias = sorted(por_trab[w])
            por_sem: dict[tuple[int, int], list[date]] = {}
            for d in dias:
                por_sem.setdefault(semana(d), []).append(d)

            for sem in sorted(por_sem):
                ds = por_sem[sem]
                if len(ds) > self.tope_dias(w):
                    fallos.append(f"C5 {w} semana {sem}: {len(ds)} dias > {self.tope_dias(w)}")
                horas = sum(self.datos.turnos[plan.turno_de(w, d)].horas for d in ds)
                if horas > self.cfg.hmax7:
                    fallos.append(f"C6 {w} semana {sem}: {horas} h > {self.cfg.hmax7}")

            for a, b in zip(dias, dias[1:]):
                if (b - a).days != 1:
                    continue
                s_a, s_b = plan.turno_de(w, a), plan.turno_de(w, b)
                if (s_a, s_b) in self.pares_c4:
                    continue
                d = self._descanso_h(s_a, a, s_b, b)
                if d < self.cfg.rmin:
                    fallos.append(f"C4 {w} {a}->{b}: {d:.1f} h < {self.cfg.rmin}")
        return fallos
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_legal.py`
Expected: PASS, imprime el número de pares exentos (debe ser > 0)

- [ ] **Step 5: Commit**

```bash
git add src/legal.py tests/test_legal.py
git commit -m "legal: juez unico del convenio, consultado antes de cada asignacion

La legalidad ACOPLA los pasos: rmin/hmax7/cmax son restricciones sobre la semana
de una persona, y lo que asigna el paso 2 se come el presupuesto del paso 4. Con
un juez compartido y verificacion tras cada paso, la infraccion se detecta donde
se causa. En la rama base esto salia como ventana INFEASIBLE meses despues.

Porta _pares_pactados desde modelo.py: la unica exencion legal que sobrevive.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `deuda.py` — la equidad como cola

**Files:**
- Create: `src/deuda.py`
- Test: `tests/test_deuda.py`

**Interfaces:**
- Consumes: `plan.Plan`, `calendario.turno_prescrito`
- Produces:
  - `deuda.Deuda(datos: Datos)`
  - `Deuda.objetivo(w) -> float` — horas anuales que le tocan (`horas_objetivo × factor_jornada`)
  - `Deuda.horas(plan, w) -> float` — horas **legales** acumuladas
  - `Deuda.cuenta(plan, w, metrica) -> int` — `metrica` ∈ `{"sabado","domingo","festivo"}`
  - `Deuda.orden(plan, candidatos, f, turno) -> list[str]` — candidatos ordenados de MÁS a MENOS deuda, con desempate total

**Por qué esto sustituye al término P2 del optimizador:** medido sobre el dataset, la rotación ya reparte sola — los 38 de `PAT_GRANDE_VALL` acaban dentro de 8 h y 2 sábados unos de otros con solo 1,37 ciclos al año. La equidad solo hay que construirla para el pool y para las perturbaciones. Coger siempre al que menos lleva es autoequilibrante y se verifica a ojo.

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
"""Deuda: quien menos lleva va primero, y el desempate es total (determinismo)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from deuda import Deuda
from plan import Plan


def main() -> int:
    datos = cargar()
    dd = Deuda(datos)
    pool = sorted(x.id for x in datos.trabajadores.values() if x.tipo == "correturno")
    a, b, c = pool[0], pool[1], pool[2]

    # Objetivo: 1776 salvo reduccion de jornada
    assert dd.objetivo(a) == datos.config.horas_objetivo * datos.trabajadores[a].factor_jornada

    p = Plan()
    lunes = date(2026, 3, 16)
    # `a` acumula 3 turnos de 8 h; `b` uno; `c` ninguno.
    for i in range(3):
        p.asignar(a, lunes + timedelta(days=i), "VADN001", "test", "montaje")
    p.asignar(b, lunes, "VADN002", "test", "montaje")

    assert dd.horas(p, a) == 24.0, dd.horas(p, a)
    assert dd.horas(p, b) == 8.0
    assert dd.horas(p, c) == 0.0

    # Mas deuda = menos lleva -> c primero, luego b, luego a
    orden = dd.orden(p, [a, b, c], lunes + timedelta(days=10), "VADN001")
    assert orden == [c, b, a], orden

    # Empate total -> desempata id ascendente, siempre igual
    p2 = Plan()
    orden1 = dd.orden(p2, [c, b, a], lunes, "VADN001")
    orden2 = dd.orden(p2, [a, c, b], lunes, "VADN001")
    assert orden1 == orden2 == sorted([a, b, c]), (orden1, orden2)

    # Un sabado cuenta en la metrica sabado
    sab = date(2026, 3, 21)
    assert sab.weekday() == 5
    p3 = Plan()
    p3.asignar(a, sab, "VADN001", "test", "montaje")
    assert dd.cuenta(p3, a, "sabado") == 1
    assert dd.cuenta(p3, b, "sabado") == 0

    print("OK  deuda · orden por deuda con desempate total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_deuda.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'deuda'`

- [ ] **Step 3: Escribir `src/deuda.py`**

```python
"""La equidad, llevada como una COLA: en cada asignación va el que menos lleva.

Sustituye al término P2 del optimizador anterior. Medido sobre el dataset real, la rotación de un
patrón ya reparte sola (8 h y 2 sábados de rango entre los 38 de PAT_GRANDE_VALL, con solo 1,37
ciclos al año), así que el 74 % de la plantilla llega equitativo gratis. Lo que hay que repartir
son las PERTURBACIONES —coberturas, adopciones, el pool entero—, y para eso basta con preguntar
siempre quién va más corto. Es lo que hace un jefe de tráfico con una tabla en la pared, converge
solo y se verifica a ojo.

La moneda es `Turno.horas` (legales). `horas_consumo` no interviene: el objetivo de 1776 es una
cifra de convenio y un convenio cuenta horas legales."""
from __future__ import annotations

from datetime import date

from cargar_datos import Datos
from metricas import METRICAS      # ("sabado", "domingo", "festivo") — una sola definicion
from plan import Plan


class Deuda:
    def __init__(self, datos: Datos) -> None:
        self.datos = datos

    def objetivo(self, w: str) -> float:
        """Horas anuales que le tocan, escaladas por su reducción de jornada."""
        return self.datos.config.horas_objetivo * self.datos.trabajadores[w].factor_jornada

    def horas(self, plan: Plan, w: str) -> float:
        return sum(self.datos.turnos[plan.turno_de(w, d)].horas for d in plan.dias_de(w))

    def _metrica_de(self, f: date, turno: str) -> str | None:
        muni = self.datos.turnos[turno].municipio
        td = self.datos.tipo_dia(f, muni)
        return {"SAB": "sabado", "DOM": "domingo", "FEST": "festivo"}.get(td)

    def cuenta(self, plan: Plan, w: str, metrica: str) -> int:
        return sum(1 for d in plan.dias_de(w)
                   if self._metrica_de(d, plan.turno_de(w, d)) == metrica)

    def orden(self, plan: Plan, candidatos: list[str], f: date, turno: str) -> list[str]:
        """Candidatos de MÁS deuda a menos. Desempate TOTAL para que el reparto sea determinista.

        Clave, en este orden: la métrica del día (si el turno cae en sábado/domingo/festivo, primero
        quien menos lleve de ESO), luego las horas que le faltan para su objetivo, y por último el
        id ascendente — que es lo que garantiza que dos ejecuciones den lo mismo."""
        metrica = self._metrica_de(f, turno)

        def clave(w: str) -> tuple:
            falta = self.objetivo(w) - self.horas(plan, w)
            de_metrica = self.cuenta(plan, w, metrica) if metrica else 0
            return (de_metrica, -falta, w)

        return sorted(candidatos, key=clave)
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_deuda.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/deuda.py tests/test_deuda.py
git commit -m "deuda: la equidad como cola, el que menos lleva va primero

Sustituye al termino P2 del optimizador. La rotacion ya reparte sola (8 h y 2
sabados de rango entre los 38 de PAT_GRANDE_VALL), asi que solo hay que repartir
las perturbaciones. Desempate total por id para que el reparto sea determinista.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `libranzas.py` — paso 1

**Files:**
- Create: `src/libranzas.py`
- Test: `tests/test_libranzas.py`

**Interfaces:**
- Consumes: `plan.Plan`, `calendario.semana`, `calendario.turno_prescrito`, `calendario.fila_patron`
- Produces:
  - `libranzas.es_bloque(datos, patron) -> bool`
  - `libranzas.exceso_h(datos, w) -> float`
  - `libranzas.peso_semanas(datos) -> dict[tuple[int,int], float]`
  - `libranzas.repartir(datos, plan) -> None` — muta `plan` con las cesiones del paso 1

**Reglas (de la especificación §Paso 1):**

```
cede_h = max(0, horas_legales_del_patrón_al_año − horas_objetivo × factor_jornada)
```

Con esta moneda, `UVI_PRIV` (1.352 h) y `UVI_VAL` (1.344 h) salen a **cero solos**: no hay lista de exenciones. `PAT_MEDINA` cede 20 h, no 60 — es el único patrón no-UVI que toca un localizado 24 h (`VADN177`, 12 veces al año, 3,43 h de recargo cada una).

Granularidad **derivada** de `patrones.csv`: es de bloque si el mínimo de filas que trabajan un día de la semana es 1 (los 4 binomios dan `1 1 1 1 1 1 1`; `PAT_GRANDE_VALL` da `35 33 34 34 35 14 5`). Una cesión de bloque arrastra la fila entera **con sus descansos**; una suelta, solo el turno de ese día.

Peso de la semana = **capacidad residual por turno demandado**. Las cesiones caen en las semanas de mayor peso, huyendo de agosto.

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
"""Paso 1: quien cede, cuanto, con que granularidad y donde cae."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from libranzas import es_bloque, exceso_h, peso_semanas, repartir
from plan import Plan


def main() -> int:
    datos = cargar()

    # Granularidad DERIVADA: los cuatro binomios son de bloque, el resto no.
    bloque = {p for p in datos.patrones if es_bloque(datos, p)}
    assert bloque == {"UVI_VAL", "UVI_PRIV", "VAL_NOCHES", "VAL_NOCHES2"}, bloque

    # UVI no cede: sus horas LEGALES estan por debajo del objetivo.
    for p in ("UVI_PRIV", "UVI_VAL"):
        w = next(x.id for x in datos.trabajadores.values() if x.patron == p)
        assert exceso_h(datos, w) == 0.0, f"{p} no deberia ceder: {exceso_h(datos, w)}"

    # PAT_MEDINA cede ~20 h (no 60): es el unico no-UVI que toca un localizado 24h.
    w = next(x.id for x in datos.trabajadores.values() if x.patron == "PAT_MEDINA")
    assert 10 <= exceso_h(datos, w) <= 30, exceso_h(datos, w)

    # PAT_GRANDE_VALL cede ~92 h
    w = next(x.id for x in datos.trabajadores.values() if x.patron == "PAT_GRANDE_VALL")
    assert 80 <= exceso_h(datos, w) <= 105, exceso_h(datos, w)

    # El peso de agosto debe ser MENOR que el de una semana normal: hay menos gente.
    pesos = peso_semanas(datos)
    agosto = [v for k, v in pesos.items() if k[1] in (32, 33, 34)]
    marzo = [v for k, v in pesos.items() if k[1] in (11, 12, 13)]
    assert sum(agosto) / len(agosto) < sum(marzo) / len(marzo), "agosto deberia pesar menos"

    # Reparto completo: nadie cede un dia en el que ya estaba de vacaciones,
    # y las cesiones de bloque cubren la fila ENTERA de su semana.
    p = Plan()
    repartir(datos, p)
    cesiones = [(d.trabajador, d.fecha) for d in p.libro if d.turno is None]
    assert cesiones, "no ha cedido nadie"
    for w, f in cesiones:
        assert datos.disponible(w, f), f"{w} cede el {f} pero estaba de vacaciones"

    # Determinismo: dos repartos identicos
    p2 = Plan()
    repartir(datos, p2)
    assert [(d.trabajador, d.fecha) for d in p2.libro if d.turno is None] == cesiones

    print(f"OK  libranzas · {len(cesiones)} dias cedidos · {len(bloque)} patrones de bloque")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_libranzas.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'libranzas'`

- [ ] **Step 3: Escribir `src/libranzas.py`**

```python
"""PASO 1 — reparte las libranzas por exceso de jornada.

Casi todo patrón prescribe más horas de las que marca el convenio, así que ceder tiempo no es un
efecto secundario: es la mecánica central del problema. Este paso decide CUÁNTO cede cada uno y
DÓNDE cae, y lo hace antes que nada porque es la decisión que más condiciona al resto.

La moneda son las HORAS LEGALES. Con ella los dos patrones UVI salen a cero solos —sus 1.352 y
1.344 h están muy por debajo del objetivo, y su exceso aparente venía de cobrar la semana de
localizado entera—, así que no hace falta ninguna lista de exenciones. PAT_MEDINA cede 20 h y no
60 por el mismo motivo: es el único patrón no-UVI que toca un localizado de 24 h (VADN177, doce
veces al año)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from calendario import fila_patron, semana, turno_prescrito
from cargar_datos import DIAS, LIBRE, Datos
from plan import Plan

PASO = "libranzas"

# Escotilla de escape para un patrón cuya estructura no revele que es de bloque. Vacía: en este
# dataset la derivación acierta con los cuatro binomios. La especificación contemplaba declararla
# en `config.toml`, pero eso exigiría tocar `cargar_datos.Config` (frozen) para un caso que hoy no
# existe; cuando alguien lo necesite, se mueve allí y este set desaparece.
PATRONES_BLOQUE_FORZADOS: frozenset[str] = frozenset()


def es_bloque(datos: Datos, patron: str) -> bool:
    """¿La cesión arrastra la FILA ENTERA? Sí cuando solo una fila del grupo trabaja cada día.

    Se deriva de `patrones.csv`, no se declara: para cada día de la semana se cuenta cuántas filas
    trabajan; si el mínimo es 1, quitar a esa persona ese día deja la línea a cero, así que la
    cesión tiene que llevarse la fila completa. Los cuatro binomios dan `1 1 1 1 1 1 1`;
    PAT_GRANDE_VALL da `35 33 34 34 35 14 5` y admite huecos sueltos intrasemanales."""
    if patron in PATRONES_BLOQUE_FORZADOS:
        return True
    filas = datos.patrones.get(patron, [])
    if not filas:
        return False
    por_dia = [sum(1 for fila in filas
                   if fila.get(d) and fila[d] != LIBRE and fila[d] in datos.turnos)
               for d in DIAS]
    return min(por_dia) == 1


def horas_patron(datos: Datos, w: str) -> float:
    """Horas LEGALES que la rotación prescribe a `w` en todo el año, descontando vacaciones."""
    total = 0.0
    for f in datos.fechas:
        if not datos.disponible(w, f):
            continue
        s = turno_prescrito(datos, w, f)
        if s:
            total += datos.turnos[s].horas
    return total


def exceso_h(datos: Datos, w: str) -> float:
    """Horas que `w` debe CEDER: lo que su patrón prescribe por encima de su objetivo anual."""
    trab = datos.trabajadores[w]
    if trab.tipo != "patron":
        return 0.0
    objetivo = datos.config.horas_objetivo * trab.factor_jornada
    return max(0.0, horas_patron(datos, w) - objetivo)


def peso_semanas(datos: Datos) -> dict[tuple[int, int], float]:
    """Capacidad residual de cada semana ISO: disponibles ÷ turnos demandados.

    Los días NO son intercambiables. En agosto la demanda es la misma pero hay mucha menos gente,
    así que soltar ahí una libranza abre un hueco justo donde menos capacidad hay para taparlo. Un
    reparto uniforme lo haría; este reparto va a las semanas de mayor peso."""
    disp: dict[tuple[int, int], int] = defaultdict(int)
    dem: dict[tuple[int, int], int] = defaultdict(int)
    for f in datos.fechas:
        sem = semana(f)
        disp[sem] += sum(1 for w in datos.trabajadores if datos.disponible(w, f))
        dem[sem] += sum(t.dem for t in datos.turnos.values()
                        if t.prioridad >= 1 and datos.opera(t.id, f))
    return {sem: disp[sem] / dem[sem] if dem[sem] else 0.0 for sem in sorted(disp)}


def _dias_prescritos(datos: Datos, w: str, sem: tuple[int, int]) -> list[date]:
    """Días de esa semana ISO en que la rotación le prescribe turno y está disponible."""
    return [f for f in datos.fechas
            if semana(f) == sem and datos.disponible(w, f) and turno_prescrito(datos, w, f)]


def _dias_de_semana(datos: Datos, sem: tuple[int, int]) -> list[date]:
    return [f for f in datos.fechas if semana(f) == sem]


def repartir(datos: Datos, plan: Plan) -> None:
    """Marca en `plan` los días que cada trabajador de patrón cede por exceso de jornada."""
    pesos = peso_semanas(datos)
    orden_sem = sorted(pesos, key=lambda s: (-pesos[s], s))

    for w in sorted(datos.trabajadores):
        trab = datos.trabajadores[w]
        if trab.tipo != "patron":
            continue
        pendiente = exceso_h(datos, w)
        if pendiente <= 0:
            continue
        bloque = es_bloque(datos, trab.patron)

        for sem in orden_sem:
            if pendiente <= 0:
                break
            dias = _dias_prescritos(datos, w, sem)
            if not dias:
                continue

            if bloque:
                # La fila entera, con sus descansos: adoptar una plaza es llevársela completa,
                # y dejar medio binomio partido rompe la complementariedad de las dos filas.
                horas = sum(datos.turnos[turno_prescrito(datos, w, f)].horas for f in dias)
                if horas > pendiente + datos.turnos[turno_prescrito(datos, w, dias[0])].horas:
                    continue           # la fila se pasa demasiado: prueba otra semana
                for f in _dias_de_semana(datos, sem):
                    if datos.disponible(w, f) and not plan.ocupado(w, f):
                        plan.ceder(w, f, PASO, f"cesion de fila entera (semana {sem[1]}, "
                                                f"binomio {trab.patron})")
                pendiente -= horas
            else:
                # Días sueltos: uno por semana como mucho, para no vaciar una semana entera.
                f = dias[0]
                if plan.ocupado(w, f):
                    continue
                h = datos.turnos[turno_prescrito(datos, w, f)].horas
                plan.ceder(w, f, PASO, f"exceso de jornada ({pendiente:.0f} h pendientes, "
                                       f"semana {sem[1]} es de las de mas holgura)")
                pendiente -= h
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_libranzas.py`
Expected: PASS. Anota el número de días cedidos que imprime — debe rondar los **606** (4.847 h ÷ 8 h).

- [ ] **Step 5: Commit**

```bash
git add src/libranzas.py tests/test_libranzas.py
git commit -m "paso 1: reparte las libranzas por exceso, ponderadas por carga

cede_h = max(0, horas_legales - objetivo). Con la moneda legal, los dos UVI salen
a cero solos y no hace falta lista de exenciones; PAT_MEDINA cede 20 h y no 60
porque su exceso aparente venia del localizado VADN177.

La granularidad se deriva de patrones.csv: es de bloque si solo una fila trabaja
cada dia (los 4 binomios). El resto admite huecos sueltos intrasemanales, que son
el 93% de las cesiones y se colocan en las semanas de mas holgura.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `criticos.py` — paso 2

**Files:**
- Create: `src/criticos.py`
- Test: `tests/test_criticos.py`

**Interfaces:**
- Consumes: `plan.Plan`, `legal.Legal`, `calendario.semana`, `calendario.turno_prescrito`
- Produces:
  - `criticos.principales(datos) -> dict[str, list[str]]` — línea → cubridores por orden `v`
  - `criticos.lineas_criticas(datos) -> list[str]` — `prioridad ≥ 2`, ordenadas por prioridad desc
  - `criticos.cubrir(datos, plan, ley) -> None`

**Reglas:**
- **La cobertura crítica GANA al patrón propio del cubridor.** Regla dura, no un peso.
- **Escape:** el cubridor queda libre si ese día ya hace otra crítica de prioridad ≥ la de esta línea.
- **ADOPCIÓN DE PLAZA — se dispara por FILA ENTERA, nunca por tocar la línea.** Esta es la regla que más cuidado exige, porque implementarla de más cuesta cobertura y de menos cuesta descansos:

  - Si al titular le **falta la fila ENTERA** esa semana (vacaciones o bloque cedido), quien cubra **adopta la plaza**: hace todos sus días de trabajo **y hereda sus LIBRE**, que se marcan con `Plan.ceder`. Nada más esa semana. *«Cubrir una plaza no es coger unos turnos sueltos: es asumir la plaza, y la plaza viene con sus descansos.»*
  - Si falta solo **parte** de la fila, o un **día suelto**: son turnos normales. Se cubren y el cubridor **sigue su propia rotación el resto de la semana**. Ningún veto semanal.

  Aplicar el veto a toda cobertura es un error medido: deja al cubridor sin poder tapar el jueves de la misma línea que ya cubría el miércoles. La rama base lo pagó y lo arregló en el commit `666cd14` («la adopción de plaza se dispara por fila entera, no por tocar la línea»).

  **Porta `AusenciaCritica` y `ausencias_criticas` desde `modelo.py:1512` y `modelo.py:1534`** — ya calculan exactamente esto: `prescritos`, `faltan`, `libres` y si la ausencia es `entera`. No lo reinventes.

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
"""Paso 2: las lineas criticas se cubren antes que nada, y una semana solo se adopta una vez."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana
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
    assert dec, "el paso 2 no ha asignado nada"

    # Nadie adopta dos veces en la misma semana ISO
    adopciones = {}
    for d in dec:
        clave = (d.trabajador, semana(d.fecha))
        adopciones.setdefault(clave, set()).add(d.turno)
    for (w, sem), turnos in adopciones.items():
        assert len(turnos) == 1, f"{w} cubre {turnos} en la semana {sem}"

    # Todo lo asignado es legal
    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    # Determinismo
    p2 = Plan()
    repartir(datos, p2)
    cubrir(datos, p2, ley)
    a = [(d.trabajador, d.fecha, d.turno) for d in p.libro if d.paso == "criticos"]
    b = [(d.trabajador, d.fecha, d.turno) for d in p2.libro if d.paso == "criticos"]
    assert a == b, "el paso 2 no es determinista"

    print(f"OK  criticos · {len(dec)} coberturas · {len(criticas)} lineas criticas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_criticos.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'criticos'`

- [ ] **Step 3: Escribir `src/criticos.py`**

```python
"""PASO 2 — cubre las líneas críticas, con el calendario aún casi vacío.

Son las que no tienen grados de libertad: cuatro de las cinco tienen UN SOLO cubridor posible en
este dataset. Decidirlas antes que nada es lo único que evita llegar al final y descubrir que la
única persona que podía hacerlas ya está ocupada — que es de donde salió toda la infactibilidad de
la rama base.

Regla dura pactada: LA COBERTURA CRÍTICA GANA AL PATRÓN PROPIO DEL CUBRIDOR. Si tu línea crítica se
queda sin nadie, dejas tu patrón. En la rama base esto era un precio que había que calibrar entre
dos constantes; aquí es una prelación explícita que se dice en una frase."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from calendario import semana, turno_prescrito
from cargar_datos import Datos
from legal import Legal
from plan import Plan

PASO = "criticos"


def principales(datos: Datos) -> dict[str, list[str]]:
    """{línea → cubridores designados, del principal al último suplente}.

    El gestor designa un principal (v=1) y suplentes (v≥2). Los cubridores pueden estar CRUZADOS
    —cada uno principal de una línea y suplente de la otra—, así que el orden es por LÍNEA, nunca
    por persona."""
    orden: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (w, s), cap in sorted(datos.capacidades.items()):
        if cap.v >= 1:
            orden[s].append((cap.v, w))
    return {s: [w for _, w in sorted(pares)] for s, pares in sorted(orden.items())}


def lineas_criticas(datos: Datos) -> list[str]:
    """Líneas de prioridad ≥ 2, de más crítica a menos. Desempate por id para determinismo."""
    return sorted((s for s, t in datos.turnos.items() if t.prioridad >= 2),
                  key=lambda s: (-datos.turnos[s].prioridad, s))


def _hace_otra_critica(datos: Datos, plan: Plan, w: str, f: date, prio: int) -> bool:
    """Escape: ese día ya hace otra crítica de prioridad MAYOR O IGUAL. No se le puede pedir más."""
    s = plan.turno_de(w, f)
    return s is not None and datos.turnos[s].prioridad >= prio


def cubrir(datos: Datos, plan: Plan, ley: Legal) -> None:
    """Asigna cubridor a cada día de línea crítica que su titular no puede hacer."""
    orden = principales(datos)
    # (cubridor, semana ISO) que ya tiene una fila adoptada. Las dos filas de un binomio son
    # COMPLEMENTARIAS: darle las dos a la misma persona en la misma semana es lunes a domingo sin
    # un solo descanso. Es lo que volvía infactibles junio, agosto, septiembre y noviembre.
    adoptada: set[tuple[str, tuple[int, int]]] = set()

    for linea in lineas_criticas(datos):
        prio = datos.turnos[linea].prioridad
        cubridores = orden.get(linea, [])
        if not cubridores:
            continue

        for f in datos.fechas:
            if not datos.opera(linea, f):
                continue
            if plan.cubierto(f, linea) >= datos.turnos[linea].dem:
                continue
            # ¿Cuántas plazas quedan por cubrir? OJO: `dem` puede ser > 1 (la línea H pide 2),
            # así que no basta con que HAYA titular — hay que contar cuántos y compararlos con la
            # demanda. Mirar solo si la lista está vacía pierde 43 días de H al año en silencio.
            titulares = [w for w in sorted(datos.trabajadores)
                         if turno_prescrito(datos, w, f) == linea
                         and datos.disponible(w, f) and not plan.cedido(w, f)]
            faltan = datos.turnos[linea].dem - len(titulares) - plan.cubierto(f, linea)
            if faltan <= 0:
                continue                        # el paso 3 la estampará

            descartados: list[str] = []
            for w in cubridores:
                sem = semana(f)
                if (w, sem) in adoptada:
                    descartados.append(f"{w}: ya adopta otra fila esta semana")
                    continue
                if _hace_otra_critica(datos, plan, w, f, prio):
                    descartados.append(f"{w}: ya hace {plan.turno_de(w, f)}, igual o mas critica")
                    continue
                ok, motivo = ley.puede(plan, w, f, linea)
                if not ok:
                    descartados.append(f"{w}: {motivo}")
                    continue
                # La cobertura crítica GANA al patrón propio: si su rotación le prescribía otra
                # cosa, la suelta. Es la prelación pactada.
                propio = turno_prescrito(datos, w, f)
                regla = (f"cobertura critica (prio {prio}); deja su {propio}"
                         if propio else f"cobertura critica (prio {prio})")
                plan.asignar(w, f, linea, PASO, regla, tuple(descartados))
                adoptada.add((w, sem))
                break
            else:
                motivo = ("ningun cubridor designado puede: " + "; ".join(descartados)
                          if descartados else "sin cubridores designados")
                plan.hueco(f, linea, motivo)
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_criticos.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/criticos.py tests/test_criticos.py
git commit -m "paso 2: cobertura critica con el calendario aun vacio

Cuatro de las cinco lineas criticas tienen UN SOLO cubridor posible. Decidirlas
antes que nada es lo unico que evita llegar al final y descubrir que esa persona
ya esta ocupada -de donde salio toda la infactibilidad de la rama base-.

Regla dura pactada: la cobertura critica gana al patron propio del cubridor. Y se
conserva 'una adopcion por semana y persona', que en la base costo junio, agosto,
septiembre y noviembre.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `rotacion.py` — paso 3

**Files:**
- Create: `src/rotacion.py`
- Test: `tests/test_rotacion.py`

**Interfaces:**
- Consumes: `plan.Plan`, `legal.Legal`, `deuda.Deuda`, `calendario.turno_prescrito`, `libranzas.es_bloque`
- Produces:
  - `rotacion.estampar(datos, plan, ley) -> None` — pone el patrón/línea donde nadie decidió otra cosa
  - `rotacion.adoptar(datos, plan, ley, dd) -> None` — ofrece las filas huérfanas a otro de patrón

Los patrones son **entrada conocida**, no una decisión: lo que este paso decide de verdad es **quién adopta las filas que los pasos 1 y 2 dejaron libres**. Se ofrece primero a otro trabajador de patrón porque mantiene la plaza con sus descansos y no gasta pool.

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
"""Paso 3: estampa la rotacion donde nadie decidio otra cosa y adopta las filas huerfanas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import turno_prescrito
from cargar_datos import cargar
from criticos import cubrir
from deuda import Deuda
from legal import Legal
from libranzas import repartir
from plan import Plan
from rotacion import adoptar, estampar


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)

    p = Plan()
    repartir(datos, p)
    cubrir(datos, p, ley)
    antes = len(p.libro)
    estampar(datos, p, ley)
    adoptar(datos, p, ley, dd)
    assert len(p.libro) > antes, "el paso 3 no ha estampado nada"

    # Un dia cedido en el paso 1 NO puede acabar con turno
    for d in p.libro:
        if d.paso == "libranzas" and d.turno is None:
            assert p.turno_de(d.trabajador, d.fecha) is None, \
                f"{d.trabajador} cedio el {d.fecha} y acabo trabajando"

    # Lo asignado por el paso 2 sigue intacto
    for d in p.libro:
        if d.paso == "criticos" and d.turno:
            assert p.turno_de(d.trabajador, d.fecha) == d.turno, \
                f"el paso 3 piso la cobertura critica de {d.trabajador} el {d.fecha}"

    # Un fijo disponible hace su linea salvo que cediera o cubriera algo critico
    fijo = next(x for x in datos.trabajadores.values() if x.tipo == "fijo" and x.linea)
    dias_suyos = [f for f in datos.fechas
                  if datos.disponible(fijo.id, f) and turno_prescrito(datos, fijo.id, f)]
    hechos = sum(1 for f in dias_suyos if p.turno_de(fijo.id, f) is not None)
    assert hechos > 0.8 * len(dias_suyos), f"{fijo.id}: solo {hechos}/{len(dias_suyos)}"

    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    print(f"OK  rotacion · {len(p.libro) - antes} decisiones del paso 3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_rotacion.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'rotacion'`

- [ ] **Step 3: Escribir `src/rotacion.py`**

```python
"""PASO 3 — estampa la rotación y decide quién adopta las filas que quedaron huérfanas.

Los patrones son ENTRADA CONOCIDA desde el minuto cero, no una decisión: el paso 1 calcula el
exceso a partir de ellos y el paso 2 elige cubridores que son gente de patrón. Así que lo que este
paso decide de verdad no es «aplicar el patrón» —eso es mecánico— sino QUIÉN SE QUEDA LAS FILAS que
los pasos 1 y 2 dejaron libres.

Se ofrecen primero a otro trabajador de patrón: adoptar mantiene la plaza con sus descansos y no
gasta pool, que es el recurso escaso (17 personas para el 53 % de los huecos estructurales)."""
from __future__ import annotations

from datetime import date

from calendario import semana, turno_prescrito
from cargar_datos import Datos
from deuda import Deuda
from legal import Legal
from plan import Plan

PASO = "rotacion"


def estampar(datos: Datos, plan: Plan, ley: Legal) -> None:
    """Pone a cada trabajador de patrón y a cada fijo lo que su rotación prescribe.

    Salta los días que los pasos 1 (cesión) y 2 (cobertura crítica) ya decidieron: `Plan.ocupado`
    devuelve True para ambos, así que la precedencia sale sola."""
    for w in sorted(datos.trabajadores):
        if datos.trabajadores[w].tipo not in ("patron", "fijo"):
            continue
        for f in datos.fechas:
            if plan.ocupado(w, f) or not datos.disponible(w, f):
                continue
            s = turno_prescrito(datos, w, f)
            if not s:
                continue
            if plan.cubierto(f, s) >= datos.turnos[s].dem:
                continue
            ok, motivo = ley.puede(plan, w, f, s)
            if not ok:
                plan.hueco(f, s, f"su titular {w} no puede: {motivo}")
                continue
            plan.asignar(w, f, s, PASO, "le toca por su rotacion")


def _huerfanas(datos: Datos, plan: Plan) -> list[tuple[date, str]]:
    """(día, línea) que la rotación prescribía y nadie está haciendo, ordenadas."""
    faltan: list[tuple[date, str]] = []
    for f in datos.fechas:
        for s in sorted(datos.turnos):
            t = datos.turnos[s]
            if t.prioridad < 1 or not datos.opera(s, f):
                continue
            if plan.cubierto(f, s) < t.dem:
                faltan.append((f, s))
    return faltan


def adoptar(datos: Datos, plan: Plan, ley: Legal, dd: Deuda) -> None:
    """Ofrece las filas huérfanas a OTRO trabajador de patrón antes de mandarlas al pool."""
    for f, s in _huerfanas(datos, plan):
        if plan.cubierto(f, s) >= datos.turnos[s].dem:
            continue
        candidatos = [w for w in sorted(datos.trabajadores)
                      if datos.trabajadores[w].tipo == "patron"
                      and not plan.ocupado(w, f)
                      and datos.elegible(w, s, f)[0]]
        if not candidatos:
            continue
        descartados: list[str] = []
        for w in dd.orden(plan, candidatos, f, s):
            ok, motivo = ley.puede(plan, w, f, s)
            if not ok:
                descartados.append(f"{w}: {motivo}")
                continue
            plan.asignar(w, f, s, PASO,
                         f"adopta la plaza de {s}, que su titular no puede hacer",
                         tuple(descartados))
            break
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_rotacion.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/rotacion.py tests/test_rotacion.py
git commit -m "paso 3: estampa la rotacion y adopta las filas huerfanas

El patron es entrada conocida, no una decision. Lo que este paso decide de verdad
es quien se queda las filas que los pasos 1 y 2 dejaron libres, y se ofrecen
primero a otro de patron: mantiene la plaza con sus descansos y no gasta pool,
que es el recurso escaso.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `reparto.py` — paso 4

**Files:**
- Create: `src/reparto.py`
- Test: `tests/test_reparto.py`

**Interfaces:**
- Consumes: `plan.Plan`, `legal.Legal`, `deuda.Deuda`, `calendario.semana`
- Produces:
  - `reparto.pendientes_semana(datos, plan, sem) -> list[tuple[date, str]]` — sin cubrir, ordenados por **escasez ascendente**
  - `reparto.candidatos(datos, plan, ley, f, turno) -> list[str]`
  - `reparto.repartir(datos, plan, ley, dd) -> None`

**Recorrido:** semana ISO a semana ISO (el convenio se mide por semana, y el correturno necesita estabilidad semanal). Dentro de cada semana, **de más difícil a más fácil**: menos candidatos elegibles primero. Cada turno va al elegible **con más deuda**.

**Desempates**, en orden: (1) estabilidad de franja respecto a lo que ya hace esa semana, (2) estabilidad de localización, (3) `id_trab` ascendente.

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
"""Paso 4: semana a semana, lo escaso primero, y al que menos lleva."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calendario import semana
from cargar_datos import cargar
from criticos import cubrir
from deuda import Deuda
from legal import Legal
from libranzas import repartir as repartir_libranzas
from plan import Plan
from reparto import candidatos, pendientes_semana, repartir
from rotacion import adoptar, estampar


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)

    p = Plan()
    repartir_libranzas(datos, p)
    cubrir(datos, p, ley)
    estampar(datos, p, ley)
    adoptar(datos, p, ley, dd)

    # Dentro de una semana, lo escaso va primero
    sem = (2026, 12)
    pend = pendientes_semana(datos, p, sem)
    n = [len(candidatos(datos, p, ley, f, s)) for f, s in pend]
    assert n == sorted(n), f"no esta ordenado por escasez: {n}"

    antes = len(p.libro)
    repartir(datos, p, ley, dd)
    assert len(p.libro) > antes, "el paso 4 no ha asignado nada"

    # Solo mueve al pool
    for d in p.libro[antes:]:
        if d.turno:
            assert datos.trabajadores[d.trabajador].tipo in ("mixto", "correturno"), \
                f"{d.trabajador} no es del pool"

    assert ley.verificar(p) == [], ley.verificar(p)[:5]

    # Determinismo
    p2 = Plan()
    repartir_libranzas(datos, p2)
    cubrir(datos, p2, ley)
    estampar(datos, p2, ley)
    adoptar(datos, p2, ley, dd)
    repartir(datos, p2, ley, dd)
    assert p.asignaciones() == p2.asignaciones(), "el paso 4 no es determinista"

    print(f"OK  reparto · {len(p.libro) - antes} asignaciones al pool")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_reparto.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'reparto'`

- [ ] **Step 3: Escribir `src/reparto.py`**

```python
"""PASO 4 — reparte lo que queda al pool (mixtos y correturnos), semana a semana.

Se recorre por SEMANA ISO y no por días porque el convenio se mide así (hmax7, cmax) y porque el
correturno necesita estabilidad de franja y de localización dentro de la semana. Dentro de cada
semana se va de MÁS DIFÍCIL A MÁS FÁCIL: asignar el lunes sin saber que el sábado hay un turno que
solo podía hacer esa misma persona es exactamente como se pierden los turnos escasos.

Cada turno va al elegible CON MÁS DEUDA. Es autoequilibrante y se verifica a ojo."""
from __future__ import annotations

from datetime import date

from calendario import semana
from cargar_datos import Datos
from deuda import Deuda
from legal import Legal
from plan import Plan

PASO = "reparto"


def candidatos(datos: Datos, plan: Plan, ley: Legal, f: date, turno: str) -> list[str]:
    """Gente del POOL que podría hacer ese turno ese día, ahora mismo y sin romper nada."""
    out = []
    for w in sorted(datos.trabajadores):
        if datos.trabajadores[w].tipo not in ("mixto", "correturno"):
            continue
        if plan.ocupado(w, f) or not datos.elegible(w, turno, f)[0]:
            continue
        if not ley.puede(plan, w, f, turno)[0]:
            continue
        out.append(w)
    return out


def pendientes_semana(datos: Datos, plan: Plan, sem: tuple[int, int]) -> list[tuple[date, str]]:
    """(día, turno) sin cubrir de esa semana, del más ESCASO al más fácil.

    La escasez se mide con la elegibilidad estática (`Datos.elegible`), no con la dinámica: es un
    orden de recorrido y tiene que ser estable mientras se asigna dentro de la semana."""
    pend: list[tuple[date, str]] = []
    for f in datos.fechas:
        if semana(f) != sem:
            continue
        for s in sorted(datos.turnos):
            t = datos.turnos[s]
            if not datos.opera(s, f):
                continue
            if plan.cubierto(f, s) >= t.dem:
                continue
            pend.append((f, s))

    def cuantos(par: tuple[date, str]) -> int:
        f, s = par
        return sum(1 for w in datos.trabajadores
                   if datos.trabajadores[w].tipo in ("mixto", "correturno")
                   and datos.elegible(w, s, f)[0])

    # Escasez ascendente; a igualdad, la línea más crítica primero; luego fecha e id de turno.
    return sorted(pend, key=lambda x: (cuantos(x), -datos.turnos[x[1]].prioridad, x[0], x[1]))


def _estabilidad(datos: Datos, plan: Plan, w: str, f: date, turno: str) -> tuple[int, int]:
    """Penalización por romper la semana del correturno: (cambia de franja, cambia de municipio).

    0 es mejor. El CLAUDE.md pide garantizarles una semana con horario similar y cierta estabilidad
    de localización; esto es lo que lo implementa, como DESEMPATE y nunca por encima de la deuda."""
    sem = semana(f)
    suyos = [plan.turno_de(w, d) for d in plan.dias_de(w) if semana(d) == sem]
    if not suyos:
        return (0, 0)
    t = datos.turnos[turno]
    franjas = {datos.turnos[s].tipo for s in suyos}
    munis = {datos.turnos[s].municipio for s in suyos}
    return (0 if t.tipo in franjas else 1, 0 if t.municipio in munis else 1)


def repartir(datos: Datos, plan: Plan, ley: Legal, dd: Deuda) -> None:
    """Recorre el año semana a semana y reparte lo que quede al pool."""
    semanas = sorted({semana(f) for f in datos.fechas})
    for sem in semanas:
        for f, s in pendientes_semana(datos, plan, sem):
            if plan.cubierto(f, s) >= datos.turnos[s].dem:
                continue
            libres = candidatos(datos, plan, ley, f, s)
            if not libres:
                plan.hueco(f, s, "ningun trabajador del pool puede ese dia")
                continue
            # La deuda manda; la estabilidad solo desempata.
            por_deuda = dd.orden(plan, libres, f, s)
            elegido = min(por_deuda,
                          key=lambda w: (_estabilidad(datos, plan, w, f, s),
                                         por_deuda.index(w)))
            descartados = tuple(f"{w}: mas horas acumuladas" for w in por_deuda[:3]
                                if w != elegido)
            plan.asignar(elegido, f, s, PASO,
                         f"del pool, el que menos carga lleva de los {len(libres)} elegibles",
                         descartados)
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_reparto.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reparto.py tests/test_reparto.py
git commit -m "paso 4: reparte al pool semana a semana, lo escaso primero

Se recorre por semana ISO porque asi se mide el convenio y porque el correturno
necesita estabilidad semanal. Dentro, de mas dificil a mas facil: asignar el
lunes sin saber que el sabado hay un turno que solo podia hacer esa persona es
como se pierden los turnos escasos. La deuda decide, la estabilidad desempata.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `generar_anual.py` — orquestación y PRIMERA MEDICIÓN

**Files:**
- Modify: `src/generar_anual.py` (reescritura completa)
- Create: `src/decisiones.py`
- Test: `tests/test_aceptacion_v2.py`

**Interfaces:**
- Consumes: todos los pasos anteriores
- Produces:
  - `decisiones.escribir(plan, ruta) -> Path` — vuelca el libro a `data/output/decisiones.csv`
  - `generar_anual.construir(datos, con_reparacion=True) -> Plan`
  - `generar_anual.main() -> int`

**Este es el hito de medición.** Con los pasos 1–4 y sin paso 5, ejecuta el año completo y anota la cobertura en crudo. Ese número decide cuánto trabajo necesita el paso 5.

- [ ] **Step 1: Escribir `src/decisiones.py`**

```python
"""Vuelca el LIBRO DE DECISIONES a CSV. Es lo que hace el cuadrante defendible.

Ante cualquier celda del calendario hay aquí una fila que dice qué paso la decidió, con qué regla,
y quién más podía haberlo hecho y por qué no. Los huecos salen igual, con su motivo: un hueco
justificado es una salida válida."""
from __future__ import annotations

import csv
from pathlib import Path

from plan import Plan

SALIDA = Path(__file__).resolve().parents[1] / "data" / "output"


def escribir(plan: Plan, ruta: Path | None = None) -> Path:
    ruta = ruta or SALIDA / "decisiones.csv"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fecha", "trabajador", "turno", "paso", "regla", "descartados", "liberado"])
        for d in sorted(plan.libro, key=lambda x: (x.fecha, x.trabajador)):
            w.writerow([d.fecha.isoformat(), d.trabajador, d.turno or "LIBRE",
                        d.paso, d.regla, " | ".join(d.descartados), "si" if d.liberado else ""])
        for h in sorted(plan.huecos, key=lambda x: (x.fecha, x.turno)):
            w.writerow([h.fecha.isoformat(), "", h.turno, "HUECO", h.motivo, "", ""])
    return ruta
```

- [ ] **Step 2: Reescribir `src/generar_anual.py`**

```python
#!/usr/bin/env python3
"""Orquesta el pipeline determinista de cinco pasos y escribe la salida.

El plan se construye por acumulación: los pasos 1 a 4 solo AÑADEN asignaciones, y el 5 es el único
autorizado a deshacer. Tras cada paso se verifica el convenio entero, para que una infracción se
detecte donde se causó y no ocho meses después."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import criticos
import decisiones
import libranzas
import reparacion
import reparto
import rotacion
import salida
import validar_datos
from cargar_datos import cargar, Datos
from deuda import Deuda
from legal import Legal
from plan import Plan


def _paso(nombre: str, plan: Plan, ley: Legal) -> None:
    fallos = ley.verificar(plan)
    if fallos:
        raise SystemExit(f"BUG en el paso «{nombre}»: {len(fallos)} infracciones de convenio\n"
                         + "\n".join(f"  · {x}" for x in fallos[:10]))
    print(f"  {nombre:14s} · {len(plan.libro):6d} decisiones · {len(plan.huecos):4d} huecos")


def construir(datos: Datos, con_reparacion: bool = True) -> Plan:
    ley, dd = Legal(datos), Deuda(datos)
    plan = Plan()

    libranzas.repartir(datos, plan);        _paso("1 libranzas", plan, ley)
    criticos.cubrir(datos, plan, ley);      _paso("2 criticos", plan, ley)
    rotacion.estampar(datos, plan, ley)
    rotacion.adoptar(datos, plan, ley, dd); _paso("3 rotacion", plan, ley)
    reparto.repartir(datos, plan, ley, dd); _paso("4 reparto", plan, ley)
    if con_reparacion:
        reparacion.reparar(datos, plan, ley, dd)
        _paso("5 reparacion", plan, ley)
    return plan


def cobertura(datos: Datos, plan: Plan) -> tuple[int, int, float]:
    """(cubiertos, demandados, %) sobre los turnos de prioridad >= 1."""
    dem = cub = 0
    for f in datos.fechas:
        for s, t in datos.turnos.items():
            if t.prioridad < 1 or not datos.opera(s, f):
                continue
            dem += t.dem
            cub += min(t.dem, plan.cubierto(f, s))
    return cub, dem, 100.0 * cub / dem if dem else 0.0


def main() -> int:
    if validar_datos.main() != 0:
        return 1
    datos = cargar()
    print(f"Resolviendo {datos.config.anio} — pipeline determinista")
    plan = construir(datos)

    cub, dem, pct = cobertura(datos, plan)
    print(f"\nCOBERTURA  {cub}/{dem} = {pct:.2f} %   ({dem - cub} turnos sin cubrir)")

    salida.generar_anual(datos, plan.asignaciones())
    ruta = decisiones.escribir(plan)
    print(f"Libro de decisiones -> {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Crear un `reparacion.py` mínimo para que el import no rompa**

```python
"""PASO 5 — reparación. Se implementa en la Task 9; este esqueleto permite medir los pasos 1-4."""
from __future__ import annotations

from cargar_datos import Datos
from deuda import Deuda
from legal import Legal
from plan import Plan

PASO = "reparacion"


def reparar(datos: Datos, plan: Plan, ley: Legal, dd: Deuda) -> None:
    return None
```

- [ ] **Step 4: MEDIR — ejecutar los pasos 1-4 sobre el año completo**

Run:
```bash
/home/samu/anaconda3/envs/ortools_env/bin/python -c "
import sys; sys.path.insert(0,'src')
from cargar_datos import cargar
from generar_anual import construir, cobertura
d = cargar()
p = construir(d, con_reparacion=False)
cub, dem, pct = cobertura(d, p)
print(f'CRUDO (pasos 1-4, sin reparacion): {cub}/{dem} = {pct:.2f} %  -> {dem-cub} sin cubrir')
"
```

**Anota el porcentaje.** Es el número que decide el alcance del paso 5:
- **≥ 98,5 %** → el paso 5 con los movimientos 1–4 basta; el movimiento 5 puede quedar fuera.
- **96–98,5 %** → hacen falta los cinco movimientos y la iteración.
- **< 96 %** → **PARA**. Hay un problema de diseño en los pasos 1–4, no de reparación. Repórtalo antes de seguir.

- [ ] **Step 5: Escribir el test de aceptación**

```python
#!/usr/bin/env python3
"""Aceptacion: el pipeline entero corre, es legal, es determinista y cubre lo pactado."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from generar_anual import cobertura, construir
from legal import Legal

UMBRAL = 96.0     # se sube a 99.0 en la Task 10, cuando el paso 5 este completo


def main() -> int:
    datos = cargar()
    ley = Legal(datos)

    plan = construir(datos)
    assert ley.verificar(plan) == [], ley.verificar(plan)[:10]

    cub, dem, pct = cobertura(datos, plan)
    assert pct >= UMBRAL, f"cobertura {pct:.2f} % < {UMBRAL} %"

    plan2 = construir(datos)
    assert plan.asignaciones() == plan2.asignaciones(), "el pipeline NO es determinista"

    print(f"OK  aceptacion · cobertura {pct:.2f} % ({cub}/{dem}) · "
          f"{len(plan.huecos)} huecos · {len(plan.libro)} decisiones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Ejecutar el test de aceptación**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_aceptacion_v2.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/generar_anual.py src/decisiones.py src/reparacion.py tests/test_aceptacion_v2.py
git commit -m "orquestacion, libro de decisiones en CSV y primera medicion

Los pasos 1-4 encadenados sobre el anio completo, con verificacion de convenio
tras cada uno: una infraccion se detecta donde se causa. El libro sale a
data/output/decisiones.csv, que es lo que hace el cuadrante defendible celda a
celda.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: `reparacion.py` — paso 5, movimientos 1 a 4

**Files:**
- Modify: `src/reparacion.py` (sustituye el esqueleto)
- Test: `tests/test_reparacion.py`

**Interfaces:**
- Consumes: `plan.Plan`, `legal.Legal`, `deuda.Deuda`, `libranzas.exceso_h`
- Produces:
  - `reparacion.huecos_reales(datos, plan) -> list[tuple[date, str]]`
  - `reparacion.mov_cambiar_cubridor(...) -> bool`
  - `reparacion.mov_mover_libranza(...) -> bool`
  - `reparacion.mov_intercambiar_semana(...) -> bool`
  - `reparacion.mov_canjear_refcal(...) -> bool`
  - `reparacion.reparar(datos, plan, ley, dd, vueltas=10) -> int` — devuelve huecos cerrados

Los movimientos se prueban **en orden** y se para en el primero que funciona. El orden está pactado y es lo que mantiene el paso narrable pese a que deshaga cosas.

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
"""Paso 5: cierra huecos deshaciendo, en el orden pactado, sin romper el convenio."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from deuda import Deuda
from generar_anual import cobertura, construir
from legal import Legal
from reparacion import huecos_reales, reparar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


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

    # Determinismo
    a = construir(datos, con_reparacion=True)
    b = construir(datos, con_reparacion=True)
    assert a.asignaciones() == b.asignaciones(), "la reparacion no es determinista"

    print(f"OK  reparacion · {huecos_antes} -> {huecos_despues} huecos "
          f"({cerrados} cerrados) · cobertura {pct_sin:.2f} -> {pct_con:.2f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_reparacion.py`
Expected: FAIL con `ImportError: cannot import name 'huecos_reales'`

- [ ] **Step 3: Escribir `src/reparacion.py`**

```python
"""PASO 5 — el único autorizado a DESHACER.

Los pasos 1 a 4 son una cascada de compromisos: cada uno decide con lo que sabe y no puede prever
que su decisión dejará sin salida a un paso posterior. Este paso es el que arregla eso, y es lo que
permite que el procedimiento no necesite backtracking global.

Sigue siendo explicable porque el ORDEN DE INTENTOS está escrito y pactado: para cada hueco se
prueban los movimientos de menos a más invasivo y se para en el primero que funciona. «Probé esto,
luego esto otro, y lo que funcionó fue lo tercero» es una frase que se dice en una reunión."""
from __future__ import annotations

from datetime import date

from calendario import semana, turno_prescrito
from cargar_datos import Datos
from deuda import Deuda
from legal import Legal
from plan import Plan

PASO = "reparacion"


def huecos_reales(datos: Datos, plan: Plan) -> list[tuple[date, str]]:
    """(día, línea) de prioridad ≥ 1 que siguen sin cubrir, de más crítico a menos.

    Se recalcula del plan y no se lee de `plan.huecos`, porque esa lista es un registro histórico
    de intentos fallidos y puede contener huecos que un paso posterior ya tapó."""
    faltan: list[tuple[date, str]] = []
    for f in datos.fechas:
        for s in sorted(datos.turnos):
            t = datos.turnos[s]
            if t.prioridad < 1 or not datos.opera(s, f):
                continue
            if plan.cubierto(f, s) < t.dem:
                faltan.append((f, s))
    return sorted(faltan, key=lambda x: (-datos.turnos[x[1]].prioridad, x[0], x[1]))


def _libres_para(datos: Datos, plan: Plan, ley: Legal, f: date, s: str) -> list[str]:
    return [w for w in sorted(datos.trabajadores)
            if not plan.ocupado(w, f) and datos.elegible(w, s, f)[0]
            and ley.puede(plan, w, f, s)[0]]


def mov_cambiar_cubridor(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                         f: date, s: str) -> bool:
    """MOVIMIENTO 1 — ¿hay alguien libre ese día que simplemente pueda hacerlo?"""
    libres = _libres_para(datos, plan, ley, f, s)
    if not libres:
        return False
    w = dd.orden(plan, libres, f, s)[0]
    plan.asignar(w, f, s, PASO, f"reparacion mov.1: quedaba libre y podia cubrir {s}")
    return True


def mov_mover_libranza(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                       f: date, s: str) -> bool:
    """MOVIMIENTO 2 — alguien que CEDIÓ ese día y cuya rotación prescribía justo esta línea.

    Se le devuelve el día y se le cede otro de la misma semana, para que su cómputo anual no cambie.
    Es el movimiento que corrige un error del paso 1: haber soltado la libranza en mal sitio."""
    for w in sorted(datos.trabajadores):
        if not plan.cedido(w, f) or turno_prescrito(datos, w, f) != s:
            continue
        # ¿hay otro día de su semana al que mover la cesión?
        sem = semana(f)
        alternativos = [d for d in datos.fechas
                        if semana(d) == sem and d != f
                        and plan.turno_de(w, d) is not None
                        and datos.turnos[plan.turno_de(w, d)].prioridad <= 1]
        if not alternativos:
            continue
        plan.liberar(w, f, PASO, "reparacion mov.2: recupera el dia cedido")
        if not ley.puede(plan, w, f, s)[0]:
            plan.ceder(w, f, PASO, "reparacion mov.2 revertido: no era legal")
            continue
        destino = alternativos[0]
        plan.liberar(w, destino, PASO, "reparacion mov.2: la cesion se muda aqui")
        plan.ceder(w, destino, PASO, f"cesion movida desde el {f} para cubrir {s}")
        plan.asignar(w, f, s, PASO, f"reparacion mov.2: recupera el dia cedido y cubre {s}")
        return True
    return False


def mov_intercambiar_semana(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                            f: date, s: str) -> bool:
    """MOVIMIENTO 3 — cambia el turno de ese día con otro compatible, liberando a quien sí puede.

    Recicla la idea de `pulido.pulir` de la rama base: mover trabajo entre dos personas compatibles
    cuando la asignación directa no cabe."""
    for w in sorted(datos.trabajadores):
        if plan.ocupado(w, f) or not datos.elegible(w, s, f)[0]:
            continue
        # `w` podría cubrir s si soltara lo que tiene... pero no tiene nada. Busca a alguien
        # OCUPADO ese día con algo menos crítico que sí pueda hacer otro.
        for v in sorted(datos.trabajadores):
            actual = plan.turno_de(v, f)
            if actual is None or datos.turnos[actual].prioridad >= datos.turnos[s].prioridad:
                continue
            if not datos.elegible(v, s, f)[0]:
                continue
            plan.liberar(v, f, PASO, f"reparacion mov.3: suelta {actual} para cubrir {s}")
            if not ley.puede(plan, v, f, s)[0]:
                plan.asignar(v, f, actual, PASO, "reparacion mov.3 revertido")
                continue
            plan.asignar(v, f, s, PASO, f"reparacion mov.3: cambia {actual} por {s}, mas critico")
            relevo = _libres_para(datos, plan, ley, f, actual)
            if relevo:
                plan.asignar(dd.orden(plan, relevo, f, actual)[0], f, actual, PASO,
                             f"reparacion mov.3: releva a {v} en {actual}")
            return True
    return False


def mov_canjear_refcal(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                       f: date, s: str) -> bool:
    """MOVIMIENTO 4 — suelta un REF CAL de CUALQUIER mes para liberar presupuesto anual.

    Un relleno de prioridad 0 siempre vale menos que un turno real, así que se canjea sin dudarlo.
    Recicla `pulido.canjear`: la rama base no podía ver un REF CAL de enero y un hueco de agosto en
    la misma ventana; aquí el año entero está delante."""
    comodines = [s2 for s2, t in datos.turnos.items() if t.prioridad == 0]
    if not comodines:
        return False
    for w in sorted(datos.trabajadores):
        if not datos.elegible(w, s, f)[0] or plan.ocupado(w, f):
            continue
        suyos = [d for d in plan.dias_de(w) if plan.turno_de(w, d) in comodines]
        if not suyos:
            continue
        soltado = suyos[0]
        turno_soltado = plan.liberar(w, soltado, PASO,
                                     f"reparacion mov.4: suelta el REF CAL del {soltado}")
        if ley.puede(plan, w, f, s)[0]:
            plan.asignar(w, f, s, PASO,
                         f"reparacion mov.4: canjea el REF CAL del {soltado} por {s}")
            return True
        plan.asignar(w, soltado, turno_soltado, PASO, "reparacion mov.4 revertido")
    return False


MOVIMIENTOS = (mov_cambiar_cubridor, mov_mover_libranza,
               mov_intercambiar_semana, mov_canjear_refcal)


def reparar(datos: Datos, plan: Plan, ley: Legal, dd: Deuda, vueltas: int = 10) -> int:
    """Cierra huecos hasta punto fijo. Devuelve cuántos ha cerrado."""
    cerrados = 0
    for _ in range(vueltas):
        antes = cerrados
        for f, s in huecos_reales(datos, plan):
            if plan.cubierto(f, s) >= datos.turnos[s].dem:
                continue
            for mov in MOVIMIENTOS:
                if mov(datos, plan, ley, dd, f, s):
                    cerrados += 1
                    break
        if cerrados == antes:
            break            # ya no mejora
    return cerrados
```

- [ ] **Step 4: Ejecutar el test y ver que pasa**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_reparacion.py`
Expected: PASS, y la cobertura debe SUBIR respecto al crudo de la Task 8.

- [ ] **Step 5: Commit**

```bash
git add src/reparacion.py tests/test_reparacion.py
git commit -m "paso 5: cuatro movimientos de reparacion en orden pactado

Los pasos 1-4 son una cascada de compromisos y ninguno puede prever que su
decision dejara sin salida a otro. Este paso arregla eso sin necesidad de
backtracking global, y sigue siendo explicable porque el orden de intentos esta
escrito: se prueba de menos a mas invasivo y se para en el primero que funciona.

Los movimientos 3 y 4 reciclan las ideas de pulido.pulir y pulido.canjear.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: movimiento 5 (deshacer y rehacer la semana) y subir el listón

**Files:**
- Modify: `src/reparacion.py`
- Modify: `tests/test_aceptacion_v2.py:UMBRAL`
- Test: `tests/test_reparacion.py` (ampliar)

**Interfaces:**
- Consumes: `reparto.pendientes_semana`, `reparto.candidatos`
- Produces: `reparacion.mov_rehacer_semana(...) -> bool`, añadido al final de `MOVIMIENTOS`

Es el movimiento con potencia suficiente para el último punto y medio: tira la asignación del pool de esa semana ISO entera y la rehace **forzando primero el turno huérfano**.

- [ ] **Step 1: Añadir el test del movimiento 5**

Añade esto a `tests/test_reparacion.py` justo antes del `print` final:

```python
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
```

- [ ] **Step 2: Ejecutar el test y ver que falla**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_reparacion.py`
Expected: FAIL con `ImportError: cannot import name 'mov_rehacer_semana'`

- [ ] **Step 3: Añadir el movimiento 5 a `src/reparacion.py`**

Añade el import y la función, y amplía `MOVIMIENTOS`:

```python
from reparto import candidatos as _candidatos_pool


def mov_rehacer_semana(datos: Datos, plan: Plan, ley: Legal, dd: Deuda,
                       f: date, s: str) -> bool:
    """MOVIMIENTO 5 — deshace la asignación del POOL de esa semana ISO y la rehace.

    El más invasivo y el último. Se tira todo lo que el paso 4 puso esa semana y se vuelve a
    repartir EMPEZANDO POR EL TURNO HUÉRFANO, que es lo que cambia el resultado: en la pasada
    original ese turno llegó tarde y ya no cabía nadie. Se cuenta en una frase: «si no cabe,
    deshacemos esa semana y la rehacemos empezando por el turno difícil».

    Si tras rehacer el hueco sigue abierto, se revierte: no vale empeorar por intentarlo."""
    sem = semana(f)
    pool = [w for w in sorted(datos.trabajadores)
            if datos.trabajadores[w].tipo in ("mixto", "correturno")]

    # Foto de lo que el paso 4 puso esa semana, para poder revertir.
    original: list[tuple[str, date, str]] = []
    for w in pool:
        for d in plan.dias_de(w):
            if semana(d) == sem:
                original.append((w, d, plan.turno_de(w, d)))
    if not original:
        return False

    for w, d, _ in original:
        plan.liberar(w, d, PASO, "reparacion mov.5: se deshace la semana")

    # Primero el huérfano.
    orden: list[tuple[date, str]] = [(f, s)]
    orden += [(d, t) for (d, t) in ((x[1], x[2]) for x in original) if (d, t) != (f, s)]

    for d, t in orden:
        if plan.cubierto(d, t) >= datos.turnos[t].dem:
            continue
        libres = _candidatos_pool(datos, plan, ley, d, t)
        if not libres:
            continue
        elegido = dd.orden(plan, libres, d, t)[0]
        plan.asignar(elegido, d, t, PASO,
                     f"reparacion mov.5: semana {sem[1]} rehecha empezando por {s}")

    if plan.cubierto(f, s) >= datos.turnos[s].dem:
        return True

    # No ha servido: se revierte a la foto original.
    for w in pool:
        for d in list(plan.dias_de(w)):
            if semana(d) == sem:
                plan.liberar(w, d, PASO, "reparacion mov.5 revertido")
    for w, d, t in original:
        if not plan.ocupado(w, d):
            plan.asignar(w, d, t, PASO, "reparacion mov.5 revertido: vuelve lo de antes")
    return False


MOVIMIENTOS = (mov_cambiar_cubridor, mov_mover_libranza, mov_intercambiar_semana,
               mov_canjear_refcal, mov_rehacer_semana)
```

**Importante:** borra la definición anterior de `MOVIMIENTOS` (la de cuatro), no dejes las dos.

- [ ] **Step 4: Ejecutar los tests**

Run:
```bash
/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_reparacion.py
```
Expected: PASS con cobertura ≥ 99 %.

**Si NO llega al 99 %:** para y reporta el número real. La especificación (§Riesgos) dice que esa decisión no se toma sobre la marcha — se elige entre apretar el paso 5, bajar el listón, o volver al tag `base-cesiones-noche`.

- [ ] **Step 5: Subir el umbral del test de aceptación**

En `tests/test_aceptacion_v2.py`, cambia:

```python
UMBRAL = 99.0     # listón pactado en la especificación
```

- [ ] **Step 6: Ejecutar la aceptación**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_aceptacion_v2.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/reparacion.py tests/test_reparacion.py tests/test_aceptacion_v2.py
git commit -m "paso 5: deshacer y rehacer la semana, y liston al 99%

El movimiento mas invasivo y el ultimo: tira la asignacion del pool de esa semana
y la rehace EMPEZANDO POR EL TURNO HUERFANO, que es lo que cambia el resultado
-en la pasada original ese turno llegaba tarde y ya no cabia nadie-. Si tras
rehacer el hueco sigue abierto, revierte: no vale empeorar por intentarlo.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: benchmark contra la rama base y limpieza

**Files:**
- Create: `tests/test_benchmark.py`
- Delete: `src/modelo.py`, `src/pulido.py`
- Delete: `tests/test_adopcion.py`, `tests/test_ausencias.py`, `tests/test_principal.py`, `tests/test_reserva.py`, `tests/test_aceptacion.py`
- Modify: `CLAUDE.md`

Los cinco tests que se borran prueban funciones de `modelo.py` que dejan de existir. El benchmark contra la rama base **no reintroduce CP-SAT en el producto**: compara contra números anotados, no ejecuta el solver.

- [ ] **Step 1: Escribir el benchmark**

```python
#!/usr/bin/env python3
"""Benchmark contra la rama base (tag base-cesiones-noche, 99,5 % de cobertura).

No ejecuta CP-SAT: compara contra las cifras anotadas de aquella corrida. El optimizador no esta en
el producto, esta en la vara de medir."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cargar_datos import cargar
from deuda import Deuda
from generar_anual import cobertura, construir
from legal import Legal

# Cifras de la rama base (tag base-cesiones-noche) sobre el mismo dataset 2026.
BASE_COBERTURA = 99.5


def main() -> int:
    datos = cargar()
    ley, dd = Legal(datos), Deuda(datos)
    plan = construir(datos)

    cub, dem, pct = cobertura(datos, plan)
    assert ley.verificar(plan) == [], ley.verificar(plan)[:10]

    pool = sorted(x.id for x in datos.trabajadores.values()
                  if x.tipo in ("mixto", "correturno"))
    sab = [dd.cuenta(plan, w, "sabado") for w in pool]
    dom = [dd.cuenta(plan, w, "domingo") for w in pool]
    horas = [dd.horas(plan, w) for w in pool]

    print(f"  cobertura      {pct:6.2f} %   (base {BASE_COBERTURA} %, "
          f"delta {pct - BASE_COBERTURA:+.2f})")
    print(f"  huecos         {dem - cub:6d}")
    print(f"  pool sabados   min {min(sab):3d}  max {max(sab):3d}  rango {max(sab)-min(sab):3d}")
    print(f"  pool domingos  min {min(dom):3d}  max {max(dom):3d}  rango {max(dom)-min(dom):3d}")
    print(f"  pool horas     min {min(horas):7.0f}  max {max(horas):7.0f}  "
          f"rango {max(horas)-min(horas):5.0f}")

    assert pct >= 99.0, f"cobertura {pct:.2f} % por debajo del liston pactado"
    print("OK  benchmark")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Ejecutar el benchmark y anotar las cifras**

Run: `/home/samu/anaconda3/envs/ortools_env/bin/python tests/test_benchmark.py`
Expected: PASS. **Anota las cifras del pool** — son las que deciden si la equidad ha aguantado.

- [ ] **Step 3: Ejecutar TODOS los tests nuevos**

Run:
```bash
for t in mudanza plan legal deuda libranzas criticos rotacion reparto reparacion aceptacion_v2 benchmark; do
  echo "--- $t ---"
  /home/samu/anaconda3/envs/ortools_env/bin/python tests/test_$t.py || echo "FALLO EN $t"
done
```
Expected: los diez imprimen `OK`.

- [ ] **Step 4: Borrar el motor viejo y sus tests**

```bash
git rm src/modelo.py src/pulido.py
git rm tests/test_adopcion.py tests/test_ausencias.py tests/test_principal.py \
       tests/test_reserva.py tests/test_aceptacion.py
```

- [ ] **Step 5: Verificar que no queda ninguna referencia**

Run: `grep -rn "modelo\|pulido\|ortools\|cp_model" src/ tests/ --include="*.py"`
Expected: sin resultados. La Task 0 ya mudó los diez símbolos que `salida.py`, `validar_datos.py` y `diagnostico.py` importaban de `modelo`, así que no debería quedar ninguna referencia. Si aparece alguna, **no la parchees a mano**: es señal de que la mudanza de la Task 0 quedó incompleta — muévela también a `calendario.py` o `metricas.py` según corresponda y vuelve a ejecutar los once tests.

- [ ] **Step 6: Actualizar `CLAUDE.md`**

Reescribe la sección **Pipeline** (los 5 puntos numerados) y el párrafo final sobre el objetivo lexicográfico. El texto nuevo debe decir:

- El pipeline es `validar_datos` → `cargar_datos` → **cinco pasos deterministas** → `salida`.
- Enumerar los cinco pasos con una frase cada uno.
- **No hay objetivo lexicográfico ni pesos**: hay una prelación de reglas escrita. `PESO_COBERTURA`, `PESO_CRITICO`, `PESO_DEV` y `PESO_DEV_COMODIN` ya no existen.
- La moneda del objetivo anual son las **horas legales**; `horas_consumo` no se usa.
- Añadir `data/output/decisiones.csv` a los ficheros de salida.
- Mantener intacta la sección **Two-layer design**: sigue siendo cierta y es la base del paso 3.

- [ ] **Step 7: Commit final**

```bash
git add -A
git commit -m "fuera el optimizador: el pipeline determinista lo sustituye

Borra modelo.py (2023 lineas) y pulido.py (777) y sus cinco tests, que probaban
funciones que ya no existen. El benchmark contra la rama base no reintroduce
CP-SAT en el producto: compara contra las cifras anotadas del tag
base-cesiones-noche.

CLAUDE.md actualizado: ya no hay objetivo lexicografico ni constantes de peso,
hay una prelacion de reglas escrita.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Notas de ejecución

**Si un paso deja el plan ilegal**, `_paso()` aborta con las primeras 10 infracciones. Eso es una funcionalidad, no un estorbo: dice exactamente qué paso rompió qué regla y para quién.

**Si la cobertura en crudo (Task 8, step 4) sale por debajo del 96 %**, para. El problema está en los pasos 1–4 y no lo va a arreglar el paso 5. Los sospechosos, por orden: (a) `libranzas.repartir` cede más días de los debidos o los coloca mal, (b) `criticos.cubrir` bloquea semanas enteras de más con la regla de adopción, (c) `reparto.pendientes_semana` mide mal la escasez.

**El punto de retorno es el tag `base-cesiones-noche`.** Está subido a `origin`. `git checkout base-cesiones-noche` devuelve el estado anterior completo.

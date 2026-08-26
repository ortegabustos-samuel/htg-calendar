# Resumen del Proyecto

Herramienta en Python que, a partir de un Plan Funcional (demandas de turno semanales extendidas a
todo el año), una plantilla de trabajadores y un calendario de festivos, elabora un cuadrante anual
provisional con el turno asignado a cada trabajador para cada día.

**Meta**: maximizar la cobertura de turnos, repartir con equidad entre trabajadores y respetar la
legalidad y los acuerdos preconcedidos.

Stack: Python · OR-Tools (CP-SAT) · openpyxl.

## Arquitectura

```
data/input/     config.toml + los 6 CSV (turnos, trabajadores, festivos,
                calendarios_municipio, capacidades, patrones)
data/output/    calendario.xlsx · horas.csv · cesiones.csv · forma_pool.csv
src/            el generador completo
```

Punto de entrada único: **`python3 src/pipeline.py`** (~4 minutos). Con `--sin-validar` se salta la
validación de entrada; `--segundos` fija el tiempo de solver por nivel del paso D.

Entorno conda: **`ortools_env`**.

## El dominio

Las **vacaciones** vienen en `trabajadores.csv`, son inamovibles y no se pintan como turno: son
ausencia (`Datos.disponible`).

Por construcción **casi todo el mundo tiene más horas de las que marca el convenio** (1776, en
`config.toml`). Ese exceso hay que devolverlo en días libres repartidos por el año, y cada día que
se libera abre un hueco que alguien tiene que cubrir. **Ceder tiempo es la mecánica central del
problema**, no un caso raro.

Cuatro naturalezas de trabajador, y confundirlas cuesta caro:

- **fijo** — una línea constante de lunes a viernes (`linea` en `trabajadores.csv`).
- **patrón** — sigue una matriz de rotación semanal (`patrones.csv`): una fila por semana, avanza
  una fila cada semana desde el lunes ancla, y cada uno arranca en la fila que declara
  `fila_inicial`. Esa columna es lo que da continuidad entre años. Todos los del grupo recorren las
  mismas filas por ciclo, así que la carga es **casi** equitativa por construcción — pero solo
  casi: con 38 filas y 52 semanas el año no es múltiplo del ciclo.
- **mixto** — **no es pool**: es un cuasi-fijo. Sus capacidades ya declaran dos naturalezas, unas
  líneas con `lv=1` de las que es titular de hecho y otras con `sab/dom/fest=1` a las que sale
  puntualmente. Sus fines de semana son **cuota de equidad**, no holgura.
- **correturno** — el único pool rodante. Absorbe lo que sobra. Se le garantiza una semana coherente
  (misma franja y zona) y, cuando tras cubrir todo lo real le sobran horas, se le completan con
  **refuerzos de calendario** (`REF CAL`, líneas con `dem=0`: horas de apoyo sin cobertura detrás).

**Los patrones se dan por válidos como están.** Incumplen el convenio por acuerdo con los
trabajadores —la semana del binomio de noche son 77 h en 7 días contra un tope de 48— y eso no se
discute. Al heredar un bloque de patrón no se comprueba nada: si era válido para el titular lo es
para quien lo hereda.

## El pipeline

Cada paso deja el cuadrante ejecutable de punta a punta y verificable abriendo el Excel. **No hay
tests**: la verificación es la salida, y el pipeline se audita solo en cada ejecución.

**A · `esqueleto.py`** — patrones rotados, fijos y vacaciones. Sin ninguna decisión libre.

**A2 · `esqueleto.colocar_mixtos`** — los mixtos ocupan su línea L-V con prioridad sobre los
correturnos y sacan su cuota de fines de semana, derivada del grupo de patrón más numeroso de su
municipio. Al darles un día de finde se les suelta uno entre semana, que es literalmente lo que hace
el planificador a mano.

**B · `libranzas.py`** — ceder el exceso de horas. La unidad se **deriva** del ritmo de cada patrón
(`ritmo.py`): el ratio descanso/trabajo. Ratio alto (noches y UVI dan 1,00) → la plaza no se
fracciona y se cede el ciclo entero; ratio bajo → **días sueltos**, nunca semanas, porque cinco días
seguidos de una línea no los tapa nadie. Las plazas con cubridor designado (`v>=1`) no se dejan
solas: se **traspasan**, tanto por ausencia del titular como por cesión. El cubridor hereda el ciclo
completo, turnos y descanso, y lo que él tuviera esos días queda como hueco.

**C · `forma.py`** — franja y zona semanal de los correturnos, derivadas de la demanda real de esa
semana. No asigna turnos: acota el dominio del paso D.

**D · `residuo.py`** — un solo **CP-SAT anual** (~24.000 booleanas) sobre lo que queda. Objetivo
**lexicográfico**, cada nivel clavado antes del siguiente y **sembrado con la solución del
anterior** — sin ese sembrado el nivel de equidad no resuelve ni en 600 s:

1. cobertura · 2. apoyos en localizado · 3. equidad de findes · 4. forma semanal

Después, fuera del modelo: relleno con REF CAL repartido por el año, y el **canje de refuerzos**
—un refuerzo no cubre nada, así que se suelta para que su dueño cubra un turno real, directamente o
encadenando con un correturno que ese día estaba de relleno—.

**E · `equidad.py`** — iguala findes dentro de cada grupo intercambiando semanas ISO y días sueltos,
con la cobertura como invariante.

## Restricciones

Solo las básicas, en `legal.py` y definidas una vez: **C4** descanso mínimo entre turnos, **C5** máx.
días por **semana ISO** y **C6** máx. horas por esa misma **semana ISO** (ambas permiten rachas de
días u horas por encima del tope a caballo de dos semanas, a sabiendas — es lo que ya toleran los
patrones pactados). C2 (un turno/día) y C3 (cualificación) salen gratis por cómo están construidos
el plan y `Datos.elegible`.

**`legal` se aplica donde el pipeline INVENTA una secuencia, y solo ahí** — nunca sobre un ciclo de
patrón heredado.

`legal.py` guarda también `domingo_ok` — nunca domingo suelto sin el sábado de ese fin de semana.
No es del convenio (no lleva número de artículo, es una regla de reparto), pero va plegada como
puerta dura dentro de `permite()`, así que se aplica en todos los sitios que ya llaman a `permite()`.

**Localizado**: las guardias de 24 h (entrada = salida, computan 8) son de localización, no de
presencia. No ocupan el día siguiente. Ojo: no vale "dura más de lo que computa", que también coge
los turnos **partidos** (10 h de reloj con 2 h de interrupción), que sí ocupan. La exención de C4 va
en falso por defecto y se paga como nivel del objetivo: solo tiene sentido para cubrir demanda que
de otro modo quedaría vacía.

## La auditoría

`legal.auditar` corre en cada ejecución. **La legalidad no se mide contando: se mide por FORMAS** —el
par de turnos consecutivos, la ventana de 7 días—. El cuadrante nace con ~1.100 incumplimientos
pactados por los propios patrones; lo que importa es cuántos tienen una forma que el **esqueleto no
produce por su cuenta**, porque esos se los ha inventado el pipeline. Se comprueba también la
**integridad**: nadie asignado en vacaciones, ni en un día que su línea no opera, ni sin capacidad,
ni por encima de la demanda.

## Preguntas abiertas con la empresa

- Los huecos que quedan son de plantilla, no de algoritmo: el **UVI tiene ~1.760 h sin usar** (su
  norma es que solo cubre sustituyendo su propio turno) y ampliar `capacidades.csv` es la otra
  palanca.
- **H tiene un único cubridor designado** y libra el día que su titular entra de vacaciones.
- El mixto **09303176K** declara una sola línea de fin de semana y ninguna con domingo, así que no
  puede equipararse con sus compañeros.
- **Cuadrante de Navidad (Art. 28)**: es la única regla del convenio que no es estructural de los
  patrones y que nadie comprueba.

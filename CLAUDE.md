# Resumen del Proyecto
Se trata de un proyecto desarrollado en Python destinado al desarrollo de una herramienta automatica
capaz de con los datos de entrada de un Plan Funcional donde se establecen las demandas de turno semanales
y que se van a extender a lo largo del año, una Plantilla de trabajadores y unas fechas de festivos elaborar 
de forma óptima o lo más cercano a la optimalidad un calendario provisional donde se establecen los turnos asignados
a cada trabajador para cada día del año.

Stack tecnologíco
- Python 
- OR-TOOLS y su librería en python
- Otras posibles librerías

Meta principal:
Obtener un calendario anual que maximice la cobertura de turnos, busque la equidad de reparto entre trabajadores y respete
la legalidad y acuerdos preconcedidos


# Arquitectura
./
   data/
      input/
      output/
   src/

El programa principal esta en src
Los ficheros de entrada estan en input y contamos con diferentes fuentes (trabajadores,turnos,festivos..)
El calendario de salida debe aparecer en xlsx en output

## Environment

Python env is the conda env `ortools_env`


## Desarrollo

Para la generación del cuadrante se siguen una serie de pasos secuenciales para reducir al optimizador el número de variables
libres y de esa manera reducir el tamaño del problema. Por un lado iniciamos superponiendo las vacaciones de todos los trabajadores
estas aparecen en el fichero `trabajadores.csv`, estas son inamovibles y deben ser respetadas y marcadas como días de vacaciones.
Por construcción los trabajadores cuentan con más horas que las que marca el convenio (actualmente 1776 pueden aumentar o reducir).
Estos excesos deben generar días libres a lo largo de todo el año en nuestros trabajadores, debemos buscar la manera de repartirlos de 
forma que minimicen la degradación del cuadrante, es decir, que su vacio no genere coberturas que a su vez generen huecos y demás.
En la plantilla contamos con una serie de trabajadores fijos cuyo turno es constante de lunes a viernes, estos generalmente tendrán exceso
y por lo tanto cuando descansen por exceso de horas su turno ha de ser cubierto
Otro tipo de trabajadores son los de patron que siguen una estructura de patrones semanales definida en `patrones.csv` que van rotando con sus compañeros dentro del patrón, en su mayoría estos patrones puede que generen o no exceso, en ese caso deberán ser cubiertos, ten en cuenta que algunos de estos patrones cuentan con un descanso que debe ser traspasado a su cubridor no únicamente el turno.
Por otro lado existen mixtos que tienen determinados una serie de turnos que pueden hacer como aparecen en `capacidades.csv`, pueden trabajar cualquier día de la semana pero deben respetar el número maximo de horas, descanso semanal etc..
Finalmente los correturnos encargados principalmente de cubrir el resto de turnos sobrantes, cubrir vacaciones, sus turnos son más aleatorios aunque en la medida de lo posible deberíamos garantizarles una semana con un horario similar (mañana, tarde) y cierta estabilidad
en localizacion.

Por otro lado los objetivos de nuestro programa deben ser sobre todo garantizar la cobertura de turnos anuales, además de ello debemos respetar las medidas legales marcadas mediante restricciones en base al convenio, sin embargo pueden existir concesiones, si los patrones 
rompen alguna de estas medidas, es indiferente puesto que estarán acordadas con los trabajadores.

Otro objetivo secundario es garantizar la equidad en horas trabajadas, todos tienen ahora mismo un valor máximo de 1776, puede ser alterado año a año, pero todos los trabajadores deben buscar estar en esas 1776 horas y minimizar las discrepancias entre ellos. Además de aquellos que hagan sabados entran tambien en el grupo de repartición de sabados, lo mismo ocurre con domingos y festivos, deben estar medianamente repartidos entre los grupos de la plantilla, lógicamente por construccion existen patrones que generan más o menos por ello se realiza esa separación en grupos.
Los correturnos entran en las equidades de Valladolid en este caso y veremos como generalizar la herramienta, además estos ya que absorben los turnos sobrantes, existirá epocas del año donde su carga de trabajo sea menor y no garanticen llegar a las 1776 horas, para ello debemos de asignarles Refuerzos de Calendario tanto de mañana como de tarde que vienen definido en `turnos.csv`

**Two-layer design.** A worker of type `patron` has a weekly rotation matrix (`patrones.csv`):
row = one week, the worker advances one row per week from a Monday anchor, and everyone in the
group starts on a different row — the one their `fila_inicial` declares in `trabajadores.csv`
(`cargar_datos.offsets_patron` resolves it, falling back to alphabetical order within the group
when the column is absent). That column is what carries the rotation across year boundaries. This *pattern* is the frozen, fair-by-construction skeleton
(everyone in a group cycles through the same rows over a cycle → identical load). The optimizer
only decides *perturbations* from that skeleton (vacation gaps, coverage holes, night-shift
handovers) — see C10 in `MODELO.md`. This is why the problem is tractable at annual scale: only
the (worker, day) pairs actually touched by something get freed as CP-SAT variables; everything
else is pinned to the pattern.

**Pipeline** (`generar_anual.py` orchestrates all of it):

1. `validar_datos.py` — five-level check (config → format → references → contract → feasibility)
   over `config.toml` + the 6 CSVs; `generar_anual.py` refuses to start on any ERROR.
2. `cargar_datos.py` (`cargar()`) — loads `config.toml` (year + convenio limits: `horas_objetivo`,
   `rmin`, `hmax7`, `cmax`, `cmax_pool` — reachable everywhere as `datos.config`, the single source;
   `anio` is mandatory and never inferred) and the 6 CSVs into `Datos`, derives everything not
   explicit in the CSVs: shift duration/type/night-hours from entrada/salida, per-worker
   capacities from patterns and fixed lines (`_anadir_capacidades_patron`,
   `_anadir_capacidad_fijo`, `_anadir_capacidades_correturno`), and equity groups
   (`_derivar_grupos_equidad`).
3. `modelo.py` (`resolver_anual()`) — the rolling horizon: solves consecutive 14-day windows
   with a 28-day frozen "tail" for continuity, stitched by (a) a 12h-rest boundary condition
   between windows and (b) accumulated equity/hours "books" (`offset`, `offset_horas`) so
   fairness is computed *across* the whole year even though it's solved in pieces. Also builds
   `calendario_cesiones` (Nivel 0: a precomputed calendar spreading night-binomial free blocks
   across the year) and `reserva_cubridores` before
   rolling, and `rellenar_refuerzos` after, to fill REF CAL (priority-0 "wildcard"/filler) slack
   with real demand.
4. `pulido.py` — post-solve passes, in this order: turn leftover REF CAL filler into real coverage,
   same day (`aprovechar`) and then against the whole year (`canjear` — drops REF CAL from *any*
   month to free annual-hours budget so a covered-nowhere shift fits, since a priority-0 filler is
   always worth less than a real shift); then equity, swapping whole *weeks* between compatible
   workers to equalize weekends/holidays (`pulir`, which also absorbs the days the two coverage
   passes moved around); then schedule coherence (`coherencia`). Runs strictly after the model
   because it can treat "coverage and legality invariant" as a hard constraint instead of a price —
   trades the model's objective is structurally forbidden from making (patrón workers are nearly
   free to move in the model's low-priority equity term, since PESO_DEV dominates it), and because
   the rolling horizon can never see a January filler and an August hole in the same window.
5. `salida.py` — turns the final `plan` dict into `data/output/calendario.xlsx` (worker × day
   grid, colored by shift type) + `metricas_trabajadores.csv` + `informe_cobertura.csv`, plus a
   console summary.

`diagnostico.py` is independent of the solve path — pure read-only analysis of `Datos` (FTE
balance, hours each pattern prescribes vs. the annual objective, legal exemptions, per-line
coverage) used to understand *why* a dataset behaves the way it does before touching the model.

**The objective is lexicographic, not a weighted sum** (`MODELO.md` §6): P1 coverage (weighted
by shift `prioridad` — 0 = wildcard filler REF CAL never a coverage target, 1 = normal patrón is
untouchable, ≥2 = critical enough to pull a worker out of their pattern) ≫ P2 fairness of
night/weekend/holiday load (fixed workers excluded) ≫ P3 location stability ≫ P4 correturno/
overtime cost ≫ P5 deviation from the frozen pattern. Each level is solved and pinned
(`obj_i ≤ best_i`) before the next is optimized — see `modelo.py`'s `PESO_COBERTURA` /
`PESO_CRITICO` / `PESO_DEV` (and `PESO_DEV_COMODIN`, the same cost when what the rotation
prescribes that day is a REF CAL: filler is never worth defending against real coverage)
constants for how the three coverage tiers relate to the cost of
breaking a pattern.

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

**Pipeline** (`generar_anual.py` orchestrates all of it): `validar_datos` → `cargar_datos` →
cinco pasos deterministas → `salida`. No queda CP-SAT ni ningún otro optimizador en el producto:
cada paso es una regla escrita, no un término de una función objetivo.

1. `validar_datos.py` — comprobación en cinco niveles (config → formato → referencias → contrato →
   factibilidad) sobre `config.toml` + los 6 CSV; `generar_anual.py` se niega a arrancar ante
   cualquier ERROR.
2. `cargar_datos.py` (`cargar()`) — carga `config.toml` (año y límites de convenio:
   `horas_objetivo`, `rmin`, `hmax7`, `cmax`, `cmax_pool` — accesibles en todas partes como
   `datos.config`, la fuente única; `anio` es obligatorio y nunca se infiere) y los 6 CSV en
   `Datos`, derivando lo que no es explícito en los CSV: duración/tipo/horas nocturnas del turno a
   partir de entrada/salida, capacidades por trabajador a partir de patrones y líneas fijas
   (`_anadir_capacidades_patron`, `_anadir_capacidad_fijo`, `_anadir_capacidades_correturno`), y
   los grupos de equidad (`_derivar_grupos_equidad`).
3. **Paso 1 — `libranzas.py`** reparte las libranzas por exceso de jornada: decide cuánto cede
   cada trabajador y dónde cae, antes que nada, porque es la decisión que más condiciona al resto.
4. **Paso 2 — `criticos.py`** cubre las líneas críticas con el calendario aún casi vacío, para no
   llegar al final y descubrir que el único cubridor posible ya está ocupado. Regla dura: la
   cobertura crítica gana al patrón propio del cubridor.
5. **Paso 3 — `rotacion.py`** estampa la rotación de cada patrón (Two-layer design, ver abajo) y
   decide quién adopta las filas que los pasos 1 y 2 dejaron huérfanas, ofreciéndolas primero a
   otro trabajador de patrón antes de gastar pool.
6. **Paso 4 — `reparto.py`** reparte lo que queda al pool (mixtos y correturnos) semana a semana,
   de más difícil a más fácil dentro de cada semana ISO, y asigna cada turno al elegible con más
   deuda (`deuda.py`); al final `rellenar_refuerzos` reparte los REF CAL sobrantes entre quienes
   quedaron por debajo de su objetivo anual.
7. **Paso 5 — `reparacion.py`**, el único autorizado a deshacer: cierra huecos probando movimientos
   de menos a más invasivo, en un orden pactado y explicable, hasta el primero que funciona.
8. `salida.py` — convierte el `Plan` final en `data/output/calendario.xlsx` (rejilla trabajador ×
   día, coloreada por tipo de turno) + `metricas_trabajadores.csv` + `informe_cobertura.csv`, más
   un resumen por consola. `decisiones.py` vuelca en paralelo el libro de decisiones a
   `data/output/decisiones.csv`: por cada celda, qué paso la decidió, con qué regla, y por qué los
   huecos que quedan están justificados.

`diagnostico.py` es independiente del camino de resolución — análisis de solo lectura sobre
`Datos` (balance FTE, horas que prescribe cada patrón frente al objetivo anual, exenciones
legales, cobertura por línea) para entender *por qué* un dataset se comporta como lo hace antes de
tocar el pipeline.

**No hay objetivo lexicográfico ni pesos: hay una prelación de reglas escrita en el código.** Cada
paso decide con lo que sabe en ese momento (`Legal`, el juez único del convenio, se consulta y se
verifica tras cada paso) y el paso 5 es el único que puede deshacer una decisión anterior para
cerrar un hueco. La cobertura crítica gana al patrón propio del cubridor porque así lo dice el paso
2, no porque una constante de peso lo empuje en esa dirección; no existen `PESO_COBERTURA`,
`PESO_CRITICO`, `PESO_DEV` ni `PESO_DEV_COMODIN`. La moneda del objetivo anual (1776 h) son las
HORAS LEGALES (`Turno.horas`, la que usa `deuda.py`) — `horas_consumo` es una cifra de informe y no
interviene en ninguna decisión del pipeline.

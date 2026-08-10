# Pipeline determinista de cinco pasos: sustituir el optimizador por un procedimiento explicable

Fecha: 2026-08-10 · Estado: aprobado, pendiente de plan de implementación
Rama: `solver-v2` · Punto de retorno: tag `base-cesiones-noche`

## Por qué se cambia

No se cambia porque el resultado sea malo. La rama base saca **99,5 % de cobertura** y es, en
métricas, el mejor cuadrante que ha producido el proyecto.

Se cambia porque **no se puede contar**. El cuadrante lo tiene que defender una persona delante
de la empresa y de los trabajadores, y hoy la respuesta a «¿por qué este señor libra el 14 de
marzo?» es «porque `PESO_DEV` vale 500 y `PESO_CRITICO` vale 2000 y la ventana del horizonte
rodante lo resolvió así». Eso no es una respuesta. Un cuadrante que nadie sabe justificar no se
firma, por bueno que sea.

El requisito es por tanto **el procedimiento entero explicable, sin caja negra en ningún paso**.
Eso implica sustituir CP-SAT por reglas deterministas. La decisión está tomada con el precio
sobre la mesa (ver «Riesgos»).

## El hallazgo que hace esto viable

La objeción evidente a un procedimiento voraz es la equidad: si asignas en cascada y no puedes
volver atrás, unos acaban con más sábados que otros. Medido sobre el dataset real, la objeción
resulta ser mucho menor de lo que parecía.

**La rotación ya reparte sola.** Recorriendo el patrón de cada trabajador durante todo 2026:

| patrón | personas | ciclos/año | rango de horas | rango de sábados | rango de domingos |
|---|---|---|---|---|---|
| PAT_GRANDE_VALL | 38 | 1,37 | **8 h** sobre 2.080 | 2 | 2 |
| PAT_MEDINA | 9 | 5,78 | 27 h | 1 | 1 |
| PAT_ISCAR | 3 | 17,3 | 16 h | 1 | 0 |
| VAL_NOCHES | 2 | 26,0 | 11 h | 0 | 0 |

Incluso con solo 1,37 ciclos en el año —el caso peor, donde la fracción sobrante podía haber
sesgado el reparto—, los 38 de `PAT_GRANDE_VALL` terminan dentro de 8 horas y 2 sábados unos de
otros. Es ruido.

Consecuencia de diseño: **el 74 % de la plantilla llega equitativa gratis, y el término P2 del
optimizador actual se está peleando por algo que el patrón ya da hecho.** La equidad solo es un
problema real en dos sitios: los 17 del pool (mixtos y correturnos, que no tienen patrón que los
reparta) y las perturbaciones que se apilan encima, porque cuando una cobertura cae sobre un
trabajador de patrón es justo lo que rompe esa equidad regalada.

De ahí el principio rector, que además es una frase que se dice en una reunión:

> **La rotación reparte; nosotros solo repartimos las perturbaciones.**

## Las cifras que condicionan el diseño

| | |
|---|---|
| Plantilla | 88 · 6 fijo, 65 patrón, 6 mixto, 11 correturno |
| Pool flexible (mixto + correturno) | 17 personas = **19 %** |
| Holgura anual | +3.491 h = +2,0 FTE (2,2 % sobre la demanda de consumo) |
| Horas que los patrones deben ceder | **3.609 h** = 2,03 FTE = 451 turnos de 8 h |
| Huecos que dejan las vacaciones de los de patrón | ~11.196 h (1.950 días) |
| Huecos estructurales / capacidad del pool | 14.805 h ÷ 30.192 h = **49 %** |

El pool llega al último paso con más de la mitad de su capacidad ya comprometida. Todo lo que el
procedimiento pueda resolver **antes** de llegar ahí es capacidad que se libera.

## Qué sobrevive y qué muere

| sobrevive | muere |
|---|---|
| `cargar_datos.py` (591) | `modelo.py` (2.023) |
| `validar_datos.py` (538) | `pulido.py` (777) |
| `salida.py` (713) | `generar_anual.py` (84), se reescribe |
| `diagnostico.py` (240, read-only) | |
| `data/`, `config.toml`, los 6 CSV | |

El contrato de entrada **no cambia en absoluto**: ningún CSV nuevo, ninguna columna nueva, ninguna
clave nueva en `config.toml`. La granularidad de cesión se deriva de `patrones.csv` (ver paso 1).

De `pulido.py` se reciclan dos algoritmos, no el archivo: el intercambio de semanas entre
compatibles (`pulir`) y el canje anual de REF CAL (`canjear`) pasan a ser dos de los movimientos
del paso 5. De `modelo.py` se recicla el calendario de cesiones de bloque (`calendario_cesiones`)
como la mitad «de bloque» del paso 1, y la regla *una adopción por semana y persona*.

## Arquitectura

Ocho módulos pequeños en lugar de un `modelo.py` de 2.023 líneas. El criterio de corte es que
cada módulo se pueda contar en un párrafo y probar solo.

| módulo | responsabilidad | líneas est. |
|---|---|---|
| `plan.py` | estructura del calendario + **libro de decisiones** | ~150 |
| `legal.py` | juez único del convenio: `rmin`, `hmax7`, `cmax`, `cmax_pool` | ~120 |
| `deuda.py` | colas de deuda: horas, sábados, domingos, festivos | ~100 |
| `libranzas.py` | paso 1 | ~200 |
| `criticos.py` | paso 2 | ~200 |
| `rotacion.py` | paso 3 | ~150 |
| `reparto.py` | paso 4 | ~250 |
| `reparacion.py` | paso 5 | ~300 |
| `generar_anual.py` | orquestación y CLI | ~100 |

### `legal.py` como juez compartido

Es la pieza que resuelve el defecto estructural de un pipeline por pasos: **la legalidad acopla
los pasos**. `rmin` (12 h entre jornadas), `hmax7` (48 h por semana ISO) y `cmax`/`cmax_pool`
(6/5 días por semana ISO) son restricciones sobre la semana de *una persona*, y un turno asignado
en el paso 2 se come el presupuesto de esa persona para el paso 4.

Todos los pasos preguntan al mismo juez antes de asignar, y **el plan entero se verifica después
de cada paso**. Un incumplimiento de convenio se detecta en el paso que lo causó, no ocho meses
después. Esto es lo que en la rama base se manifestaba como «ventana INFEASIBLE» en enero, junio,
agosto, septiembre y noviembre.

`legal.py` conoce también los `_pares_pactados`: exenciones de C4 acordadas por par de turnos
(las que necesitan PAT_MEDINA, UVI_PRIV y UVI_VAL según el diagnóstico).

### El libro de decisiones

Cada asignación del calendario se registra con:

```
(trabajador, fecha, turno, paso, regla, alternativas_descartadas, motivo)
```

Sale a un `data/output/decisiones.csv` nuevo, junto a los tres ficheros que ya produce
`salida.py`. Es la pieza que hoy no existe y la que hace el procedimiento defendible: ante
cualquier celda del cuadrante hay una frase que la justifica y la lista de quién más podría
haberlo hecho y por qué no.

Los huecos se registran igual, con su motivo. **Un hueco justificado es una salida válida.**

### Flujo

```
Datos ──> Plan vacío
      ──> paso 1  libranzas     ──> Plan + huecos
      ──> paso 2  críticos      ──> Plan + huecos
      ──> paso 3  rotación      ──> Plan + huecos
      ──> paso 4  reparto pool  ──> Plan + huecos residuales
      ──> paso 5  reparación    ──> Plan final
      ──> salida.py
```

Los pasos 1 a 4 **solo añaden** asignaciones. El paso 5 es el único autorizado a deshacer.

## Los cinco pasos

### Paso 0 (implícito) — vacaciones

Ya vienen resueltas en `Datos`: dos bloques de 15 días por trabajador (`vac1_inicio`,
`vac2_inicio`), 30 días al año, inamovibles. Se marcan y se bloquean. No hay decisión que tomar.

### Paso 1 — libranzas por exceso de horas

**Qué reparte.** Cada trabajador de patrón tiene un exceso = lo que su patrón prescribe menos
`horas_objetivo` (1776), ajustado por `factor_jornada`. `diagnostico.py` ya lo calcula.

**La moneda son las HORAS LEGALES COMPUTADAS**, no la ocupación:

```
cede_h = max(0, horas_legales_del_patrón − horas_objetivo)
```

Una sola línea, sin excepciones y sin lista que mantener. Esto **diverge a propósito de la rama
base**, cuyo libro anual (`_minutos_jornada`) se llevaba en ocupación. La divergencia no es
estética: el objetivo de 1776 h es una cifra de **convenio**, y un convenio cuenta horas legales.
Ceder por ocupación deja al trabajador por debajo de las horas que tiene pactadas.

El proyecto maneja tres monedas y conviene tenerlas separadas:

| moneda | 24 h localizado vale | para qué sirve |
|---|---|---|
| `horas` (legal) | 8,0 h | topes del convenio C4/C5/C6 **y el objetivo anual** |
| `horas_consumo` | 11,4 h (= 80 h ÷ 7) | — se retira del libro anual (ver abajo) |
| `JORNADA_LOCALIZADO_SEMANA` | 40 h/semana | solo líneas UVI, solo en la rama base |

**Lo que la regla produce**, y por qué sale sola:

| patrón | personas | h legales | cede/persona | total |
|---|---|---|---|---|
| PAT_GRANDE_VALL | 38 | 1.868 | 92 h | 3.496 h |
| PAT_ISCAR | 3 | 1.869 | 93 h | 279 h |
| PAT_MAYORGA | 4 | 1.850 | 74 h | 296 h |
| PAT_MEDINA | 9 | 1.796 | **20 h** | 180 h |
| PAT_TORDESILLAS | 3 | 1.864 | 88 h | 264 h |
| VAL_NOCHES ×2 | 4 | 1.859 | 83 h | 332 h |
| **UVI_PRIV** | 2 | **1.352** | **0 h** | 0 h |
| **UVI_VAL** | 2 | **1.344** | **0 h** | 0 h |

Los dos casos que obligaron a fijar la moneda:

- **UVI no cede** porque sus horas legales (1.352 y 1.344) están más de 400 h **por debajo** del
  objetivo. Su exceso nominal de +207 y +167 h era un artefacto de cobrar la semana de localizado
  entera. No hace falta declararlo exento: la regla lo deja en cero sola.
- **PAT_MEDINA cede 20 h, no 60.** Es el único patrón no-UVI que toca un localizado de 24 h
  (`VADN177`, 22:00→22:00, sábado y domingo de la fila 8). Le toca 12 veces al año y cada una
  carga 3,43 h de más en la moneda de consumo: 12 × 3,43 = 41 h, que es toda la diferencia entre
  sus 1.796 h legales y sus 1.836 de ocupación. Cediendo 60 h cerraría el año en 1.736 h legales,
  cuarenta por debajo de convenio.

**Consecuencia: `horas_consumo` se retira del libro anual.** Con la moneda legal, el recargo del
localizado (11,4 h frente a 8) deja de intervenir en el objetivo de 1776 y en las colas de deuda.
La disponibilidad que ese recargo pretendía representar ya está protegida por `rmin` y por los
topes semanales, que sí impiden encadenar una guardia de 24 h con otra cosa. `horas_consumo` deja
de usarse en el motor nuevo.

**Total a ceder: 3.609 h** = 451 turnos de 8 h = 2,03 FTE. De ellas, **266 h (7 %) de bloque** y
**3.343 h (93 %) de día suelto**.

> **Corrección medida en implementación (Task 4).** Esta cifra era 4.847 h en la primera versión de
> la especificación, tomada de `diagnostico.py`. Ese número es un promedio **por patrón**; el
> reparto real es **por persona**, y las horas que la rotación prescribe a cada uno van de 1.816 a
> 1.888 dentro del mismo `PAT_GRANDE_VALL` — porque las vacaciones de cada trabajador caen en
> semanas distintas de la rotación y unas semanas pesan más que otras. El caso extremo es
> `PAT_MEDINA`: solo **2 de sus 9** superan las 1.776 h; a los otros siete sus vacaciones ya los
> dejan por debajo y no tienen nada que ceder. La proporción bloque/suelto apenas se mueve (7/93
> frente a 6/94), así que la conclusión de diseño se mantiene.

**De horas a días.** El exceso se expresa en horas pero se cede en unidades enteras. Para un
patrón suelto, el número de días a ceder es `round(exceso_h / horas legales del turno que
prescribe ese día)`; el redondeo se acumula en un residuo por trabajador para que el error no se
sesgue siempre en la misma dirección. Para un patrón de bloque, el número de filas es
`round(exceso_h / horas legales de la fila)`. `PAT_GRANDE_VALL` cede 92 h ≈ 11,5 turnos de 8 h por
persona; `PAT_MEDINA` cede 20 h ≈ 2,5 turnos; `UVI_PRIV` y `UVI_VAL` no ceden.

**Dos granularidades.** La unidad de cesión **no es la misma para todos los patrones**:

| granularidad | criterio | patrones | horas a ceder |
|---|---|---|---|
| **bloque** (fila entera) | exactamente **una fila trabaja cada día de la semana** | VAL_NOCHES, VAL_NOCHES2 | 332 h (**7 %**) |
| | | UVI_VAL, UVI_PRIV | 0 h (no ceden) |
| **suelto** (días sueltos) | el resto | PAT_GRANDE_VALL, PAT_ISCAR, PAT_MEDINA, PAT_MAYORGA, PAT_TORDESILLAS | 4.515 h (**93 %**) |

El criterio se **deriva de `patrones.csv`**, no se declara: para cada día de la semana se cuenta
cuántas filas del grupo trabajan; si el mínimo es 1, el patrón es de bloque. Los cuatro binomios
dan `1 1 1 1 1 1 1`; `PAT_GRANDE_VALL` da `35 33 34 34 35 14 5`.

Granularidad y cesión son **cosas distintas**: los dos UVI siguen siendo patrones de bloque aunque
no cedan nada, porque cuando su titular se va de vacaciones la fila se adopta entera igual (lo usan
los pasos 2 y 3). Que no cedan por exceso no los saca de la mecánica de adopción.

**Que el 93 % de las cesiones sean de día suelto es la mejor noticia del diseño.** Un día suelto se
coloca exactamente en la fecha de menor carga; un bloque de siete cae donde cae. El paso 1 tiene
mucha más precisión de la que parecía, y el paso 5 recibe muchos menos huecos que arreglar.

La regla se explica en una frase: **si en tu grupo solo hay una fila trabajando cada día, tu
libranza arrastra la fila entera, porque si no ese día la línea se queda a cero.** En un patrón
con 34 filas trabajando el martes, quitar a uno baja a 33 y no rompe nada.

**No hay ninguna lista que mantener**, ni de exenciones ni de granularidad:

- La **exención** de UVI no hace falta declararla: la moneda legal la deja en cero sola.
- La **granularidad** se deriva de `patrones.csv` y acierta con los cuatro binomios.

Queda una escotilla de escape por si algún día aparece un patrón de bloque que la estructura no
revele: `libranzas.PATRONES_BLOQUE_FORZADOS`, un `frozenset` vacío en el módulo. Va ahí y no en
`config.toml` porque llevarla a la configuración obligaría a tocar `cargar_datos.Config` —que es
`frozen` y está fuera del alcance de este trabajo— para un caso que hoy no existe. Si alguna vez se
usa de verdad, se mueve.

Esto es lo que permite contar el paso 1 sin asteriscos: **una regla, cero excepciones**.

Una cesión de bloque arrastra también los **descansos** de la fila, no solo sus turnos: adoptar
una fila es llevarse la plaza entera. Una cesión suelta arrastra solo el turno de ese día.

**Cómo reparte: ponderado por carga, no uniforme.** Un reparto uniforme mete libranzas en agosto
justo donde menos capacidad hay para taparlas. Cada semana ISO del año recibe un peso:

```
peso(semana) = trabajadores no de vacaciones esa semana / demanda de turnos esa semana
```

Las cesiones caen en las semanas de **mayor peso** (más capacidad residual por turno demandado),
huyendo de los picos de vacaciones. Dentro de una semana, los días sueltos se colocan en los de
mayor holgura.

**Determinismo.** Orden fijo de trabajadores (por `id_trab` ascendente) y, dentro de cada uno,
semanas ordenadas por peso descendente con `(nº de semana ISO)` como desempate. Mismo input,
mismo output, siempre.

### Paso 2 — cobertura crítica

**Qué cubre.** Las líneas de `prioridad ≥ 2` — el escalón que CLAUDE.md define como «crítico
hasta el punto de sacar a alguien de su patrón». En el dataset actual eso son cinco líneas (no hay
ninguna de prioridad 2; las hay de 3 y 4), y el diagnóstico marca cuatro de ellas como **punto
único de fallo** (un solo cubridor posible):

| línea | prioridad | días/año | titulares | cubridores |
|---|---|---|---|---|
| VADU47127 | 4 | 368 | 2 | 1 |
| VADN051 | 3 | 368 | 2 | 1 |
| VADN052 | 3 | 368 | 2 | 1 |
| VADP003 | 3 | 368 | 2 | 1 |
| H | 3 | 251 | 2 | 2 |

Se resuelven aquí, con el calendario casi vacío, porque no tienen grados de libertad. Es donde se
originó toda la infactibilidad de la rama base.

**Regla dura pactada:** *la cobertura crítica gana al patrón propio del cubridor.* Si tu línea
crítica se queda sin nadie, dejas tu patrón. En la rama base esto era un precio a calibrar
(`PESO_CRITICO` frente a `PESO_DEV`); aquí es una prelación explícita, que es a la vez más simple
y más fácil de defender.

**Escape:** el cubridor queda liberado si ese día ya está haciendo otra línea crítica de
prioridad **mayor o igual**.

**Reserva de presupuesto.** Lo asignado aquí se descuenta ya del presupuesto legal y de horas del
cubridor, para que ningún paso posterior se lo gaste. Esto es la generalización de lo que en la
rama base se llamó `horas_forzadas`.

**Se conserva la regla *una adopción por semana y persona*.** Las dos filas de un binomio son
complementarias: darle las dos a la misma persona en la misma semana ISO es lunes a domingo sin un
solo descanso. Y una semana ya adoptada no admite además cobertura suelta. Esta regla está pagada
con las infactibilidades de junio, agosto, septiembre y noviembre; se traslada tal cual.

**Orden de elección del cubridor:** el orden `v` de `capacidades.csv` (principal antes que
suplente). Es una designación del gestor y aquí decide de verdad, en vez de diluirse en un
sumatorio.

### Paso 3 — rotación y fijos

Se estampa el patrón de cada trabajador de patrón y la línea de cada fijo, **excepto** donde los
pasos 1 o 2 ya decidieron otra cosa.

Las filas que quedan huérfanas —titular de vacaciones o de libranza— se ofrecen **primero a
adopción por otro trabajador de patrón**: mantiene la plaza con sus descansos y no gasta pool. Lo
que no encuentre adoptante pasa al montón del paso 4.

Nota de orden: los patrones son entrada conocida desde el minuto cero, no una decisión. Este paso
no «aplica» el patrón —ya está aplicado implícitamente desde el paso 1, que calcula el exceso a
partir de él—; lo que hace es **decidir quién adopta las filas que los pasos 1 y 2 dejaron
libres**.

### Paso 4 — reparto al pool, semana a semana

**Recorrido:** el año se recorre **semana ISO a semana ISO**. Dentro de cada semana, los turnos
sin cubrir se ordenan por **escasez ascendente** (menos candidatos elegibles primero) y se asignan
en ese orden.

Se recorre por semanas y no por días porque el convenio se mide por semana ISO (`hmax7`, `cmax`),
y porque el CLAUDE.md pide dar al correturno estabilidad de franja y de localización dentro de la
semana. Se ordena por escasez dentro y no cronológicamente porque asignar el lunes sin saber que
el sábado hay un turno que solo podía hacer esa misma persona es como se pierden los turnos
críticos.

**Elección:** cada turno va al elegible **con más deuda** de lo que ese turno reparte (horas,
sábado, domingo, festivo). La deuda la lleva `deuda.py` acumulada desde enero. Es la forma de
equidad que un jefe de tráfico lleva en una tabla en la pared: *le toca al que menos lleva*. Es
autoequilibrante, verificable a ojo y no necesita optimizador.

**Desempates**, en orden: (1) estabilidad de franja respecto a lo que ya hace esa semana,
(2) estabilidad de localización, (3) `id_trab` ascendente. El tercero garantiza orden total y por
tanto determinismo.

**Cierre de semana:** verificación de `hmax7` y `cmax_pool` sobre la semana completa. Lo que no se
haya podido cubrir va a la lista de huecos **con su motivo** (sin candidatos / todos agotarían
`hmax7` / todos romperían `rmin`…).

### Paso 5 — reparación

Único paso autorizado a deshacer. Recorre los huecos por prioridad de línea descendente y, para
cada uno, prueba una lista **ordenada y pactada** de movimientos, parando en el primero que
funcione:

1. **Cambiar el cubridor** por otro elegible del mismo día.
2. **Mover una libranza** del paso 1 a otra semana.
3. **Intercambiar semanas enteras** entre dos trabajadores compatibles. *(recicla `pulido.pulir`)*
4. **Canjear un REF CAL** de cualquier mes del año para liberar presupuesto anual. Un relleno de
   prioridad 0 siempre vale menos que un turno real. *(recicla `pulido.canjear`)*
5. **Deshacer y rehacer la semana**: se tira la asignación del pool de esa semana ISO entera y se
   rehace desde cero forzando primero el turno huérfano.

El movimiento 5 es el que da potencia suficiente para el último punto y medio de cobertura, y se
cuenta en una frase: *si no cabe, deshacemos esa semana y la rehacemos empezando por el turno
difícil.*

**Iteración hasta punto fijo:** el paso repite mientras siga cerrando huecos, con tope de vueltas
(por defecto 10). Determinista y explicable: *repetimos hasta que ya no mejora.*

**Segunda función:** rebalanceo fino de equidad dentro del pool, una vez la cobertura está cerrada.

## Manejo de errores

- **No existe INFEASIBLE.** El procedimiento siempre produce un calendario completo. Un turno sin
  cubrir es una salida válida, contada y con motivo en `decisiones.csv`. Esto elimina por
  construcción la clase de fallo que dominó el desarrollo de la rama base.
- `validar_datos.py` sigue siendo la puerta de entrada: cinco niveles, y `generar_anual.py` se
  niega a arrancar con cualquier ERROR.
- **Invariante verificable tras cada paso:** el plan cumple el convenio. Si no lo cumple es un bug
  del paso, y aborta ahí señalando trabajador, fecha y regla violada.

## Pruebas

- **Golden test** sobre el dataset real: cobertura ≥ umbral y **cero** violaciones de convenio.
- **Test por paso** con datasets sintéticos mínimos: un patrón de bloque y uno suelto, una línea
  crítica de cubridor único, una semana que agota `hmax7`.
- **Test de determinismo:** dos corridas del mismo input producen ficheros idénticos byte a byte.
- **Benchmark contra `base-cesiones-noche`** (99,5 %). El CP-SAT no está en el producto: está en
  el test, como vara de medir. Esto no contradice el requisito de explicabilidad.

## Criterio de éxito

| métrica | objetivo |
|---|---|
| Cobertura | **≥ 99 %** (≤ ~190 turnos descubiertos al año) |
| Violaciones de convenio | 0 |
| Rango de sábados/domingos en el pool | ≤ el de la rama base |
| Desviación de 1776 h | ≤ la de la rama base |
| Procedimiento | narrable entero en una página |
| Determinismo | mismo input → mismo output, byte a byte |

## Riesgos

**El listón del 99 % es exigente para un procedimiento sin backtracking global.** Es el riesgo
principal y se asume con los ojos abiertos. La mitigación es el paso 5 reforzado (movimiento 5 +
iteración) y el hecho de que el **93 %** de las cesiones sean de día suelto, que da al paso 1
mucha más precisión para colocarlas donde no degradan.

**Compromiso por escrito:** si al medir se aterriza por debajo del 99 %, no se decide sobre la
marcha. Se lleva el número real a la mesa y se elige entre apretar el paso 5, bajar el listón, o
volver a `base-cesiones-noche`. El tag existe exactamente para eso.

**Riesgo secundario:** la equidad del pool depende de que las colas de deuda se alimenten bien
desde el paso 2. Si la cobertura crítica carga sistemáticamente a las mismas personas, el paso 4
no puede compensarlo del todo. Se mide en el golden test.

## Fuera de alcance (YAGNI)

- No se toca el contrato de entrada: ni ficheros nuevos, ni columnas nuevas, ni claves nuevas en
  `config.toml`.
- No se generaliza a otras provincias en esta iteración.
- No se hace interfaz de usuario ni edición manual del cuadrante.
- No se conserva `modelo.py` como modo alternativo: sería mantener dos motores.

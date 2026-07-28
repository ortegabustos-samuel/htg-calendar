# Propuesta — arquitectura general en dos niveles + humano en el bucle

*(v2, 2026-07-27. La v1 proponía un "master plan" con semántica de Valladolid —libranzas de
noche, cubridores UVI—. Objeción correcta: la herramienta debe servir en zonas cuyo modo de
trabajo desconocemos. Esta versión deriva TODO de los CSV; no hay ni una regla de zona.)*

---

## 0. El hallazgo que reordena el problema

`src/diagnostico.py` (nuevo, no resuelve nada: solo lee los datos) responde en segundos las
preguntas estructurales de **cualquier** juego de datos. Sobre Valladolid 2026 dice:

**(a) Sobra capacidad: +3,8 FTE.** La demanda anual son 149.460 h y la plantilla a 1776 h
aporta 156.288 h. La media exigida es **1698 h/trabajador**. Luego el exceso de jornada que
vemos NO es falta de gente: es un problema de reparto.

**(b) Casi TODO patrón prescribe más de 1776 h/año** — no solo las noches:

| patrón | h/año prescritas | Δ vs 1776 |
|---|---|---|
| PAT_ISCAR | 1883 | **+107** |
| PAT_TORDESILLAS | 1864 | +88 |
| VAL_NOCHES / NOCHES2 | 1859 | +83 |
| PAT_GRANDE_VALL | 1858 | +82 |
| PAT_MAYORGA | 1842 | +66 |
| PAT_MEDINA | 1796 | +20 |
| UVI_PRIV / UVI_VAL | 1352 / 1344 (consumo 1931 / 1920) | −424 / −432 |

Esto reencuadra el proyecto entero: **"ceder tiempo para aterrizar en 1776" no es una
particularidad de las noches, es la mecánica central de todo patrón.** Las noches solo
llaman la atención porque su unidad de cesión es gruesa (~38 h/semana): ceder una quincena
(77 h) las deja en 1782 — es decir, *1776 con un ligero exceso*, exactamente lo que la
empresa hace a mano. Los demás patrones ceden lo mismo en días sueltos y pasa desapercibido.

**(c) El UVI no es un caso especial, es una pregunta de negocio sin responder**: computa
1344 h (−432 del objetivo) pero consume 1920 h (+144). Nadie ha decidido cuál de las dos
cuenta contra el 1776. Todo el tratamiento especial del UVI en el modelo es un parche a esa
indefinición.

**(d) NINGÚN patrón necesita las exenciones legales que el modelo les da.** Ver §1.

---

## 1. Dos bugs que el ajuste a ojo del planificador deja al descubierto

*(Ambos corregidos el 2026-07-27; se conserva el diagnóstico porque explica el porqué del
diseño actual. Ver §4.)*

El caso real relatado: *un nochero cede una semana por exceso de horas; su cubridor especial
cubre dos turnos; por exceso de trabajo esa semana el convenio le obliga a descansar dos
días; esos dos días los cubre un trabajador disponible cualquiera porque el otro cubridor
estaba ocupado.*

Eso **no es juicio humano irreducible**: es una cascada que el modelo simultáneo hace por
construcción… salvo que hoy no puede, por dos defectos:

### Bug 1 — La exención legal está concedida a la PERSONA, no a la ROTACIÓN

`modelo._exento_legal` devuelve `True` para todo `tipo == "patron"` → apaga **C5** (máx. 6
días/semana), **C6** (48 h/semana) y **C7** (descanso de finde) para **63 de 88
trabajadores (72% de la plantilla)**.

La justificación escrita en el código ("las noches encadenan 7 días / 77 h una semana de cada
dos") **es falsa en estos datos**: VAL_NOCHES hace como mucho 44 h y 4 días por semana. El
diagnóstico lo confirma patrón a patrón: ninguno supera 48 h ni 6 días; solo tres
(UVI_PRIV, UVI_VAL, PAT_MEDINA) necesitan la exención de **C4** (descanso de 12 h), por sus
localizados 24 h.

Consecuencia directa sobre el caso real: cuando el cubridor sale de su rotación para tapar
la noche, **el modelo no le aplica el descanso que el convenio sí le impone** — por eso el
planificador tuvo que meterlo a mano. Corrección: la exención debe ser **por patrón y solo
para el límite que su rotación pactada incumple** (dato que ya calcula el diagnóstico) y
**solo en las semanas en que el trabajador sigue su rotación**. En cuanto se desvía para
cubrir, manda el convenio. Con eso, los dos días de descanso salen solos, y la cobertura de
esos dos días por "un disponible cualquiera" es justo lo que hace C1.

### Bug 2 — `_handover_critico` prohíbe lo que el humano hizo

La restricción dura dice: quien hace un turno de línea crítica en una semana **no hace nada
más esa semana**. En el caso real, el segundo cubridor tapó *dos días sueltos* — bajo esta
regla, ese trabajador quedaría inmovilizado el resto de la semana (~40 h perdidas contra su
1776). La preferencia por no fragmentar es razonable; como **restricción dura es falsa**.
Debe ser penalización blanda (coste por fragmentar la semana de una línea), no prohibición.

*(Y un tercero, ya sabido: falta la guardia de estado en `resolver_anual`, que convirtió las
ventanas infactibles de diciembre en datos basura silenciosos.)*

---

## 2. La arquitectura, ahora sí general

### Nivel 0 — Plan anual de CESIÓN y COBERTURA (derivado de datos)

Un CP-SAT pequeño (semanas × trabajadores; segundos) que decide lo que solo es decidible
viendo el año. **Todos sus insumos salen del diagnóstico, ninguno de la semántica de zona**:

| Concepto general | Cómo se obtiene | En Valladolid es… |
|---|---|---|
| unidad de cesión de un patrón | su periodo `len(filas)` y las horas/semana de su rotación | quincena de noche (77 h), día suelto en los largos |
| cuánto cede cada trabajador | `h_prescritas_año − objetivo·factor` | +83 h ⇒ 1 quincena |
| qué líneas hay que tapar | huecos que deja la cesión + vacaciones, sobre `capacidades` | noches y UVI |
| quién puede taparlas | grafo de capacidades (`v=0` titular, `v=1` cubridor) | los 2–3 cubridores especiales |
| cuándo conviene ceder | semanas con cubridor disponible (tu regla: *"se busca que las libranzas caigan cuando sus cubridores están disponibles"*) | — |

Restricciones: tope anual por persona; cada semana de línea con demanda tiene responsable;
no dos cesiones simultáneas que dejen una línea huérfana; vacaciones como dato fijo.
Objetivo: `Σ|horas_año − objetivo|` + reparto uniforme de las cesiones por el año.

Si mañana llega una zona con patrones distintos, el diagnóstico calcula su tabla y el Nivel 0
funciona igual: **lo único que cambia son los números**.

### Nivel 1 — El rodante actual, simplificado

Obedece el esqueleto (qué periodos cede cada quien, quién adopta cada semana) y se ocupa de
lo suyo: legalidad diaria, cobertura fina, REF CAL, equidad de findes. Desaparecen
`objetivo_horas_noche`, `COLCHON_NOCHE_H`, `patrones_noche`/`patrones_uvi` como categorías
semánticas y buena parte del pacing ad hoc.

### Nivel 2 — Humano en el bucle (modo reparación)

Cargar un cuadrante editado, congelar las celdas tocadas (`x == valor humano`) y re-resolver
el entorno: la cascada se recalcula sin deshacer la decisión humana, y el report avisa si una
edición rompe algo legal en vez de acatarla en silencio. La maquinaria `congelar`/`cola` ya
existe. Con los bugs de §1 corregidos, la mayoría de los ajustes a ojo dejarán de hacer falta.

---

## 3. Lo que queda por confirmar con la empresa

1. **¿1776 es el tope real?** Nos dices que el máximo observado es 1776 "con un ligero
   exceso". Hoy el código usa `HMAX_AÑO = 1826` como tope duro. Si el tope real es
   ~1776 + tolerancia pequeña, hay que bajarlo: media plantilla está hoy en 1808–1834
   porque el modelo tiene permiso para llegar ahí.
2. **¿Se cede una SEMANA o una QUINCENA?** Tu ejemplo dice "se cedió una semana"; el código
   fuerza quincena entera (`activo` acopla los 14 días). Con 77 h de excedente, media
   quincena no cuadra el año. Determina la unidad de cesión → parámetro por patrón, no
   constante global.
3. **UVI: ¿computa 8 h o consume 11,43 h contra el 1776?** (§0c). Es la pregunta que
   desbloquea quitar todo el trato especial del UVI.
4. **Cubridores**: nos dices que hay uno nuevo para VADU47127 y que dos trabajadores de
   patrón cubrieron esa línea. Hay que reflejarlo en `capacidades.csv` (`v=1`); mientras no
   esté en los datos, el modelo no puede usarlo.
5. **Más ejemplos de ajustes a ojo**, con el porqué. El primero ya destapó dos bugs; es la
   fuente de información más rentable que tenemos.

*Ya resueltas: vacaciones fijas (dato); bajas/imprevistos fuera de alcance; libranzas no
pactadas pero condicionadas a disponibilidad de cubridores; criterio de aceptación = cubrir
sin excesos ni defectos de jornada.*

---

## 4. Orden de trabajo

### HECHO (2026-07-27) — pasos 1 a 3

1. **Guardia de estado** en `resolver_anual` + aviso de tope en `salida`.
2. **Tope real**: `HMAX_AÑO = HORAS_OBJETIVO + TOLERANCIA_H` = 1784 (antes 1826). La
   tolerancia cubre el redondeo de la unidad de cesión (una quincena de noche = 77 h).
3. **Exención legal atada a la ROTACIÓN, no a la persona**: C5/C6/C7 se aplican a todos (las
   rotaciones los cumplen por construcción, así que no estorban, y protegen a quien sale de
   su rotación a cubrir); de C4 sobrevive solo la salvedad por PARES pactados — 4 en todo el
   dataset.
4. **Sin handover**: la restricción dura desaparece y no se sustituye por nada. C4 ya impone
   el descanso: tras una noche solo 6 de 73 turnos son legales al día siguiente (ninguno de
   día) y tras un localizado 24 h, ninguno.
5. **Sin turnado duro de libranzas de noche**: era la causa de la infactibilidad de
   diciembre. Lo prices P1 (crítico, 300/día), no una restricción.
6. **UVI fuera de contadores de horas** (decisión de la empresa): sin tope, sin cap y sin
   equidad; sus vacaciones siguen generando demanda.

7. **NIVEL 0 — calendario de cesiones** (`calendario_cesiones` en `modelo.py`): decide sobre el
   AÑO ENTERO qué bloques libra cada trabajador de rotación acoplada. La unidad es el BLOQUE DE
   TRABAJO completo (4 noches + 3 de la semana siguiente = 77 h = un ciclo), nunca días sueltos.
   Reglas: reparto por tramos del año · nunca con el binomio de vacaciones · nunca sin cubridores
   disponibles · el binomio no cede el mismo ciclo · **no más cesiones por ciclo que cubridores
   libres** (están compartidos entre líneas, y cubrir un bloque ocupa a uno entero).

**Resultado del año 2026**: 27/27 ventanas factibles · cobertura **99.5%** (100 huecos; antes
97.7% con 429) · de ellos solo **9 críticos** (UVI en agosto y octubre) y **cero en las líneas
de noche**, que quedan cubiertas todo el año · **0** por encima del tope · jornada 1664–1776 h
con mediana 1771 y σ 19.9 · **0** violaciones de C4/C5/C6 · nocheros los cuatro en 1771 h.

### PENDIENTE

8. **Déficit del pool**: correturnos y mixtos se quedan en μ=1734 h, ~42 por debajo del
   objetivo. Es el mayor margen que queda en equidad de jornada.
9. **Huecos UVI de agosto/octubre** (9 al año): coinciden vacaciones de titulares con
   cubridores ocupados. Probablemente sea cuestión de datos (más cubridores) más que de modelo.
10. **Rachas de hasta 12 días** trabajados seguidos (38 trabajadores encadenan más de 8). Legal
    con C5 por semana ISO; si la empresa no lo acepta, pasar C5 a ventana deslizante de 7 días.
11. **Modo reparación** (humano en el bucle) y, si queda equidad residual, el pulido LNS
    (etapa 5 del roadmap).
12. Preguntas abiertas de §3 que siguen vivas: UVI computada vs consumo; reflejar en
    `capacidades.csv` los cubridores nuevos. *(La unidad de cesión ya está resuelta: el bloque.)*

Referencias: horas anualizadas (Corominas/Lusa, *Annals of OR* 2004/2007), INRC-II
(arXiv:1501.04177), rotating workforce scheduling (Musliu; Stuckey et al. 2018),
schedulingbenchmarks.org.

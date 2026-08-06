# Cobertura de líneas críticas: adopción de plaza y orden de cubridores

Fecha: 2026-08-06 · Estado: aprobado, pendiente de plan de implementación

## El problema

La corrida del año 2026 deja 31 turnos sin cubrir, de los que **11 son críticos**: siete de
VADP003 (prioridad 3) y cuatro de VADU47127 (prioridad 4), todos localizados de 24 h
(22:00→22:00). Llama la atención porque son las dos líneas más prioritarias del dataset y
quedan descubiertas en días en que todo lo demás sí se cubre.

La investigación (ver «Evidencia» al final) descarta las explicaciones cómodas: no es que la
prioridad pese poco —pesa, y gana—, ni que falte gente, ni que la reserva de Nivel 0 discrimine.
Son dos mecanismos concretos:

**1. `_handover_critico` se dispara por tocar, no por adoptar.** La restricción dice: quien hace
un turno de línea crítica en una semana ISO no hace ningún otro turno no crítico esa semana. Se
aplica igual a quien cubre los cinco días de una fila que a quien tapa un jueves suelto. El
resultado es que cubrir **un** día cuesta la semana entera del cubridor.

El caso medido, semana del 3 al 9 de agosto. `18029935M` (PAT_GRANDE_VALL) tiene prescrito
VADN030 de lunes a viernes; el plan le da martes y miércoles y lo deja libre el resto porque su
patrón prescribe un 15 % por encima del objetivo y tiene que ceder horas. El jueves 06/08 está
**completamente libre** y VADP003 se queda sin cubrir, porque tomarlo le costaría soltar el
VADN030 del martes y del miércoles y el VADN026 del sábado.

**2. El orden `v` no puede decidir quién cubre.** Los dos cubridores están cruzados a propósito:

| | VADP003 | VADU47127 |
|---|---|---|
| `18029935M` | v=2 (suplente) | v=1 (principal) |
| `01860358A` | v=1 (principal) | v=2 (suplente) |

En esa misma semana el plan pone a `01860358A` a cubrir VADU47127, donde es **suplente**,
mientras el principal de esa línea se queda en su patrón. Los dos pagan exactamente lo mismo por
cubrir —500 de desvío—, la reserva de Nivel 0 les da lo mismo (194,3 h a cada uno) y acaban el
año empatados (17 sábados, 1776 h). Todo lo que pesa mucho los ve intercambiables.

Quien debería desempatar es el orden de preferencia, y no puede. `_preferencia_cubridor` entra
en el objetivo como `PESO_ORDEN * p7` **sumado** dentro de W3, junto a la desviación de jornada,
que se mide en minutos de desviación anual y es de otro orden de magnitud. El orden no pierde
una votación: se diluye en un sumatorio.

> **Nota sobre la documentación.** `CLAUDE.md` afirma que el objetivo es lexicográfico y que cada
> nivel se resuelve y se fija (`obj_i ≤ best_i`) antes de optimizar el siguiente. No es cierto:
> `modelo.py:1197` minimiza una única suma ponderada con W1 ≫ W2 ≫ W3 que *emula* ese orden. La
> resolución lexicográfica real vivía en `resolver_lexicografico`, eliminada en el commit
> `47f2230`; en `modelo.py:1202` quedó su encabezado huérfano. Corregir `CLAUDE.md` forma parte
> de este trabajo.

## Qué se cambia

### 1. Adopción de plaza (sustituye a `_handover_critico`)

La regla pasa a dispararse por **lo que el titular deja sin hacer**, que es dato conocido antes
de resolver: las vacaciones vienen en `trabajadores.csv` y las cesiones las decide
`calendario_cesiones` antes de rodar.

Para cada línea crítica y cada semana ISO se determina qué fila del patrón la prescribe esa
semana y cuáles de sus días no puede hacer el titular. `reserva_cubridores` ya construye
exactamente eso —`prescrito[(línea, fecha)] → titular`, y comprueba `datos.disponible(titular, f)`
y si el bloque está cedido—; se extrae a una función compartida en vez de duplicarla.

De ahí salen dos casos y solo dos:

**Falta la fila entera → adopción.** Quien la cubra hace todos los días que esa fila prescribe y
**nada más en toda la semana**, ni siquiera otra línea crítica. Los LIBRE de la fila son su
descanso y quedan vacíos de verdad. Es más estricto que la regla actual, que solo vetaba lo no
crítico.

**Falta parte de la fila, o un día suelto → turnos normales.** Se cubren como cualquier otro
turno: sin herencia de descansos y sin bloqueo semanal. El cubridor sigue su propia rotación el
resto de la semana.

Los dos casos, sobre los huecos reales de 2026:

*Adopción* — semana del 3 al 9 de agosto. `12427762B` está de vacaciones del 1 al 15 y esa
semana le toca la fila 0 de UVI_PRIV, que prescribe **miércoles y jueves**. Faltan los dos, es
decir la fila completa: quien cubra hace el 05 y el 06 y nada más esa semana. (Puede encadenarlos:
`('VADP003','VADP003')` está entre los pares exentos de C4.)

*Cobertura suelta* — semana del 12 al 18 de octubre. `72918050T` está de vacaciones hasta el 15 y
esa semana le toca la fila 1, que prescribe **lunes, martes, viernes, sábado y domingo**. Faltan
solo el lunes 12 y el martes 13, dos de cinco: son turnos normales, los cubre quien corresponda y
sigue con su rotación el resto de la semana.

La justificación de la asimetría: lo que aportan los LIBRE de la fila es protección frente a la
carga **acumulada** de asumir la plaza una semana entera. El descanso **inmediato** ya lo
garantiza C4 — estas líneas son 22:00→22:00, así que tras un turno el día siguiente es imposible
(empezar a las 22:00 da 0 h de descanso, un turno de mañana da 9), y se comprueba en la corrida
actual: en los once huecos críticos, quien hizo el 24 h el día anterior aparece LIBRE sin
excepción.

*Descartado explícitamente:* respetar descansos «en los extremos» de una cobertura parcial.
Solo estaría bien definido si los días que faltan fueran contiguos, y una ausencia parcial no
tiene por qué serlo. Si al medir aparecen secuencias problemáticas, se añadirá con casos reales
delante.

### 2. El orden de cubridores como restricción dura

> Para cada día en que una línea con cubridores designados se queda sin titular, **el principal
> la cubre**, aunque tenga que dejar su propio turno. Solo cuando el principal no puede entra el
> siguiente en el orden `v`.

«No puede» son exactamente dos casos:

1. **No está disponible** en el sentido de `datos.disponible`: vacaciones.
2. **Ya está comprometido con una línea crítica de prioridad igual o superior.** Sin esta
   condición se sacaría a alguien de VADU47127 (prioridad 4) para taparle VADP003 (prioridad 3),
   al revés de lo que se quiere. Es el caso real de `72918050T`, titular de una y cubridor de la
   otra.

**No hace falta escape por bloqueo legal.** Al fijar la obligación como dura, C4 propaga hacia
atrás por sí sola: el modelo no puede asignarle al principal, el día anterior, ningún turno
incompatible con la línea que está obligado a cubrir. Ese turno se libera y queda como hueco.

**La cascada está verificada.** El turno propio que suelta el principal lo recoge un correturno:
las líneas de los cubridores (VADN030, VADP002, VADN026, VADN031, VADN020, VADN011, VADN017) no
tienen cubridor designado, así que los **11 correturnos** tienen capacidad para todas ellas. Las
vetadas a correturnos son solo H, VADN051, VADN052, VADP003 y VADU47127.

Formulación en el modelo. Para cada línea crítica `s`, cada día `f` en que su titular falta, y
`P` el principal de `s` (el de menor `v`):

```
x[P, f, s]  +  ausente[P, f]  +  Σ  x[P, f, s']   ≥  1
                                s' crítica, prioridad(s') ≥ prioridad(s)
```

Es decir: `P` cubre la línea, salvo que esté ausente por vacaciones —`ausente` es una constante
0/1 precomputada de `datos.disponible`— o que ese día esté haciendo otra línea crítica de
prioridad igual o superior. La obligación **no** se impone los días en que el titular sí puede.

Si `P` queda eximido, la misma restricción se aplica al siguiente en el orden `v`, y así
sucesivamente.

`_preferencia_cubridor` se mantiene tal cual para el orden **entre suplentes** (v=2 antes que
v=3) en los casos que la restricción no alcanza; sigue siendo una preferencia blanda.

## Qué NO se cambia

- La regla de que el binomio de UVI no cubre en su día de descanso (`_solo_rotacion_uvi`).
  Funciona y es deliberada.
- Los pesos `PESO_COBERTURA`, `PESO_CRITICO`, `PESO_DEV`. El diagnóstico descarta que el problema
  esté ahí.
- La estructura de suma ponderada del objetivo. Restaurar la resolución lexicográfica real
  arreglaría de raíz la dilución de **todos** los términos de W3, no solo el orden `v`, pero
  multiplica el tiempo de resolución por el número de niveles y merece ser trabajo propio, con
  medición delante. Queda anotado, fuera de alcance.

## Criterios de aceptación

1. **Las 27 ventanas se resuelven.** Es el criterio principal, no un detalle: se introduce una
   restricción dura y las duras pueden volver infactible una ventana. La guardia de estado de
   `resolver_anual` lo detecta, así que un fallo se ve en vez de producir datos basura.
2. **Los huecos críticos bajan de 11.** La semana del 3 al 9 de agosto es el caso de prueba
   completo, porque en ella actúan las dos reglas a la vez y debe resolverse así:
   - `18029935M`, principal de VADU47127, queda obligado a cubrirla. El titular `11804456M`
     está de vacaciones del 3 al 17, así que falta su fila entera (lunes, martes, viernes,
     sábado, domingo): la adopta y no hace nada más esa semana. Su VADN030 pasa a los
     correturnos.
   - Eso libera a `01860358A`, que hoy cubre VADU47127 siendo solo suplente. Como es el
     principal de VADP003 y está disponible, le toca esa: adopta la fila 0 (miércoles y jueves)
     y **desaparecen los huecos del 05 y del 06 de agosto**. Su VADP002 pasa a los correturnos.
   - Cada uno acaba en el papel para el que está designado, que es lo que hoy no ocurre.
3. **La cobertura global no baja del 99,8 %** (18264/18295 hoy).
5. **La equidad de horas no se degrada.** Hoy: fijos μ1773 σ6, patrones μ1774 σ6,8, correturnos
   μ1771 σ3,3, mixtos μ1733 σ23,4. Nadie por encima de 1776. Los dos efectos a vigilar son que
   los cubridores tendrán que soltar algo para absorber la cobertura obligatoria, y que los ~34
   días de línea propia liberados se reparten entre 11 correturnos (unos 3 días cada uno).

Los criterios 2 a 5 se miden sobre `informe_cobertura.csv` y `metricas_trabajadores.csv`, que ya
se generan.

## Riesgo asumido

Se buscó activamente un caso concreto de infactibilidad y **no se encontró**. Los dos candidatos
se deshacen:

- *Choque con los topes semanales (C5/C6).* No lo hay: el patrón propio del cubridor es blando,
  así que si la suma se pasa de 48 h el modelo le quita días de patrón. Cede lo blando.
- *Localizados en días consecutivos.* Tampoco: `_pares_pactados` exime de C4 al **par de
  turnos**, no a la persona, precisamente para que un cubridor pueda hacer la secuencia entera
  igual que el titular. Comprobado: `('VADP003','VADP003')` y `('VADU47127','VADU47127')` están
  entre los 86 pares exentos.

Queda el riesgo genérico de meter una restricción dura en un modelo con muchas interacciones.
Es barato de detectar y por eso el criterio 1 es el primero.

## Evidencia

Todo lo anterior está medido sobre la corrida del 05/08/2026 (`data/output/`), commit `47f2230`:

- **31 huecos**, 11 críticos: VADP003 los días 05/08, 06/08, 12/10, 13/10, 25/11, 26/11 y 30/11;
  VADU47127 los días 30/03, 25/11, 26/11 y 30/11. Los 20 restantes son de prioridad 1 y **todos
  de agosto**, el pico de vacaciones.
- En los huecos del 06/08, 12/10, 13/10 y 26/11 había cubridores designados **libres ese día**.
  En el resto, C4 explica correctamente la ausencia (quien hizo el 24 h el día anterior).
- Las vacaciones de titulares y cubridores se solapan: `72918050T` y `01860358A` coinciden del 1
  al 15 de octubre; `12427762B` y `71151995T` coinciden del 16 al 30 de noviembre. Eso explica
  por qué los huecos de octubre y noviembre caen en pares de días consecutivos.
- `18029935M` es elegible para VADP003 el 06/08: `datos.elegible` devuelve `(True, True)` —
  entra como refuerzo, porque sus flags de día son 0 y lo habilita el `v=2`. No había prohibición
  alguna.

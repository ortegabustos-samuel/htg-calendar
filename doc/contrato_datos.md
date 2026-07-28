# Contrato de los datos de entrada (`data/input/`)

Los CSV **no están versionados** (`.gitignore`: `/data/*`) porque `trabajadores.csv` contiene
DNI/NIE reales y fechas de vacaciones de empleados, y el repositorio tiene remoto en GitHub.
Este documento es, por tanto, la única referencia del formato: sin él, un clon limpio no puede
reconstruir los datos. **Si cambias el contrato, actualiza este fichero en el mismo commit.**

Todos los ficheros son CSV con cabecera y separador coma, codificación UTF-8.

---

## `turnos.csv` — las líneas de servicio

```
id_turno,municipio,lv,sabado,domingo,festivo,hora_entrada,hora_salida,horas_computadas,dem,prioridad
VADN001,Valladolid,1,1,0,1,7:00,15:00,8.0,1,1
```

| columna | significado |
|---|---|
| `lv`,`sabado`,`domingo`,`festivo` | 0/1: días en que la línea OPERA (si no opera, no hay demanda) |
| `horas_computadas` | horas legales del turno. El CONSUMO de capacidad se deriva aparte (un localizado 24 h consume ≈11,43 h aunque compute 8) |
| `dem` | nº de personas que exige por día operativo |
| `prioridad` | **0** = comodín (REF CAL): sin demanda, es relleno de horas · **1** = normal · **>=2** = crítica (se cubre aunque haya que sacar a alguien de su patrón). El peso relativo lo fija `peso_cobertura` en modelo.py |

Hoy VADU47127 tiene prioridad **4** y VADP003 prioridad **3**: así, si solo puede cubrirse una,
cae la menos prioritaria. Es el mecanismo para priorizar líneas entre sí — se declara aquí, no
en el código.

## `trabajadores.csv` — la plantilla

```
id_trab,tipo,patron,vac1_inicio,vac2_inicio,linea
71117540E,fijo,,18/05/2026,17/08/2026,H
```

| columna | significado |
|---|---|
| `tipo` | `fijo` · `patron` · `mixto` · `correturno` |
| `patron` | id del patrón, **obligatorio si `tipo=patron`** |
| `linea` | id del turno que cubre, **obligatorio si `tipo=fijo`**. Su capacidad se deriva de aquí (L-V), no va en capacidades.csv |
| `vac1_inicio`,`vac2_inicio` | inicio de cada periodo; duran 15 días naturales |
| `factor_jornada` | OPCIONAL, default 1.0. Reducción de jornada en (0,1]: escala objetivo y tope |
| `grupo` | OPCIONAL. Grupo de equidad de findes/festivos. Si se omite, cada patrón forma su propio grupo y mixtos/correturnos van al pool general |

## `patrones.csv` — las rotaciones pactadas

```
patron,fila,lun,mar,mie,jue,vie,sab,dom
UVI_VAL,0,LIBRE,LIBRE,VADU47127,VADU47127,LIBRE,LIBRE,LIBRE
```

Una **fila = una semana** de la rotación; el patrón avanza una fila por semana desde un lunes
ancla, y cada trabajador del grupo arranca en una fila distinta. Celdas: id de turno o `LIBRE`.

De aquí se derivan **automáticamente** las capacidades de los trabajadores de patrón (todos los
días), así que **esas filas no deben ponerse en capacidades.csv**.

## `capacidades.csv` — solo lo que NO se puede derivar

```
id_trab,id_turno,lv,sab,dom,fest,v
71152292K,H,0,0,0,0,1
```

Contiene exclusivamente:

1. **Mixtos**: sus líneas, con los días de cada una. Es su fuente principal.
2. **Correturnos**: sus líneas (por ahora explícitas; ver "pendiente" abajo).
3. **Coberturas excepcionales `v=1`**: quién puede tapar una línea que no es suya (los cubridores
   de las noches y del UVI, por ejemplo). `v=1` habilita el turno aunque los flags de día digan
   que no.
4. **Trabajadores de patrón con turnos AJENOS a su patrón**: capacidades reales que ninguna
   rotación declara (hoy 207 filas, sobre todo VADN004/005/008/026/151).

Lo que **no** debe aparecer: las líneas de los fijos (van en `trabajadores.csv`) ni los turnos
que un trabajador de patrón ya hace dentro de su rotación (se derivan de `patrones.csv`).

> Una fila con los cuatro días a 0 y `v=0` **no habilita nada**: equivale a no existir. Si
> aparece, es un error de datos.

## `festivos.csv` y `calendarios_municipio.csv`

```
fecha,ambito          |  municipio,calendario_festivos
01/01/2026,Comun      |  Iscar,Valladolid
```

`ambito` es `Comun` (todos) o el nombre de un calendario local. Cada municipio se asocia al
calendario que le aplica.

---

## Reglas que viven en el CÓDIGO, no en los datos

Conviene tenerlas presentes porque explican por qué ciertos flags no hacen falta:

- **Un fijo cubre su plaza de lunes a viernes.** Es lo que significa ser fijo. No se deducen los
  días de la línea a propósito: VADN022 opera sábados, domingos y festivos, pero su fijo solo la
  cubre L-V (el finde lo hace otro).
- **Un trabajador de patrón puede hacer los turnos de su rotación cualquier día** en que la línea
  opere. La rotación decide qué le toca; la capacidad solo dice qué sabe hacer.
- Un fijo o un trabajador de patrón con días atípicos puede llevar una fila explícita en
  `capacidades.csv`: la derivación **respeta** lo que ya venga del CSV.

## Pendiente

- **Correturnos sin filas**: pueden hacer casi cualquier turno (67 de 73), así que sus 723 filas
  podrían derivarse. Hay que marcar antes, en `turnos.csv`, qué líneas exigen autorización
  expresa — hoy les faltan exactamente H, VADN039 y las cuatro críticas (VADN051, VADN052,
  VADP003, VADU47127), y darles esas por defecto vaciaría de sentido a los cubridores designados.
- **`src/validar_datos.py` está roto** desde el commit inicial (importa `DATOS_DEF`, que no
  existe). Es justo la herramienta que debería vigilar este contrato.

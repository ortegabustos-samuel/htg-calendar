# Contrato de los datos de entrada (`data/input/`)

Los CSV **no están versionados** (`.gitignore`: `/data/*`) porque `trabajadores.csv` contiene
DNI/NIE reales y fechas de vacaciones de empleados, y el repositorio tiene remoto en GitHub.
Este documento es, por tanto, la única referencia del formato: sin él, un clon limpio no puede
reconstruir los datos. **Si cambias el contrato, actualiza este fichero en el mismo commit.**

Los seis ficheros de datos son CSV con cabecera y separador coma, codificación UTF-8; el
séptimo, `config.toml`, es TOML.

**Comprueba los datos antes de resolver:**

```
python3 src/validar_datos.py            # o: python3 src/validar_datos.py otro/directorio
```

Recorre `config.toml` y los seis CSV en cinco niveles —config, formato, referencias, este
contrato y viabilidad— y distingue **ERROR** (se pierde información: no resolver con esto), **aviso** (probablemente
intencionado, míralo) y **nota** (contexto: balance anual, horas que prescribe cada patrón).
`generar_anual.py` lo ejecuta al arrancar y se niega a empezar si hay errores, porque el fallo
típico aquí no es ruidoso: un espacio de más en una celda hace que el cargador la descarte en
silencio y el cuadrante salga sutilmente mal después de 45 minutos de cómputo.

---

## `config.toml` — el año y el convenio

```toml
[horizonte]
anio = 2026

[jornada]
horas_objetivo = 1776

[convenio]
rmin = 12       # descanso mínimo entre jornadas (h)              — C4
hmax7 = 48      # máx. horas de trabajo efectivo por semana ISO   — C6
cmax = 6        # máx. días trabajados por semana ISO             — C5
cmax_pool = 5   # el mismo tope para correturnos y mixtos
```

Lo que cambia al pasar de año o de provincia. Antes estaba escrito en `modelo.py` y **duplicado**
en `diagnostico.py`, con lo que el diagnóstico podía juzgar viable un dataset usando un objetivo
distinto del que luego aplicaba el modelo.

`anio` es **obligatorio**: el horizonte lo declaran los datos y no se deduce de ningún sitio.
`--anio` en la línea de órdenes lo pisa para una ejecución concreta. El validador comprueba además
que todas las fechas de `festivos.csv` sean de ese año.

Los cuatro de `[convenio]` y el de `[jornada]` son opcionales: sin ellos se usan los valores de
arriba, que son los que el código tenía escritos. Un campo desconocido es error, no se ignora en
silencio.

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
| `fila_inicial` | OPCIONAL, solo `tipo=patron`. Fila de `patrones.csv` que hace en la **primera semana del horizonte**. Entero en `[0, nº de filas)`. Si se omite, se deduce del orden alfabético del NIF dentro del grupo |

**`fila_inicial` es lo que enlaza la rotación de un año con la del anterior.** Sin ella, cada 1 de
enero la rotación vuelve a empezar y quien tenga la fila mala del patrón la repite año tras año;
además el orden alfabético depende de *quién más está en el grupo*, así que un alta o una baja
desplaza de fila a todos los que ordenan por detrás. Al preparar un año nuevo hay que mirar por
dónde iba la rotación al cerrar el anterior y poner aquí ese valor.

Ojo al calcularlo a mano: el salto entre el ancla de un año y la del siguiente **no siempre es de
52 semanas** (2028→2029 son 53, porque depende de en qué día caiga el 1 de enero). Un valor mal
puesto no rompe nada visible — sale un cuadrante perfectamente válido que sencillamente no
continúa donde tocaba.

## `patrones.csv` — las rotaciones pactadas

```
patron,fila,lun,mar,mie,jue,vie,sab,dom
UVI_VAL,0,LIBRE,LIBRE,VADU47127,VADU47127,LIBRE,LIBRE,LIBRE
```

Una **fila = una semana** de la rotación; el patrón avanza una fila por semana desde un lunes
ancla, y cada trabajador del grupo arranca en una fila distinta — la que le fija su `fila_inicial`
en `trabajadores.csv`. Celdas: id de turno o `LIBRE`.

De aquí se derivan **automáticamente** las capacidades de los trabajadores de patrón (todos los
días), así que **esas filas no deben ponerse en capacidades.csv**.

## `capacidades.csv` — solo lo que NO se puede derivar

```
id_trab,id_turno,lv,sab,dom,fest,v
71152292K,H,0,0,0,0,1
```

Contiene exclusivamente (hoy 329 filas, de las 2.575 que llegó a tener):

1. **Mixtos**: sus líneas, con los días de cada una (103 filas). Es su fuente principal y no se
   deriva de nada: cada mixto tiene entre 4 y 32 líneas, sin regla común.
2. **Coberturas designadas `v>=1`**: quién puede tapar una línea que no es suya, **con orden de
   preferencia** (12 filas). Habilita el turno aunque los flags de día digan que no.
3. **Trabajadores de patrón con turnos AJENOS a su patrón**: capacidades que ninguna rotación
   declara (207 filas, sobre todo VADN004/005/008/026/151, que comparten los 38 de
   PAT_GRANDE_VALL).
4. **Excepciones de correturno** (7 filas): los que solo cubren VADN022 en finde, porque entre
   semana la lleva su fijo.

Lo que **no** debe aparecer, porque se deriva: las líneas de los fijos (van en
`trabajadores.csv`), los turnos que un trabajador de patrón hace dentro de su rotación (salen de
`patrones.csv`) y las líneas ordinarias de los correturnos (pueden con cualquiera).

**Un correturno NO alcanza las líneas con cubridor designado.** La frontera sale sola de los
datos: si una línea tiene alguien con `v>=1`, es que hay que estar designado para ella. Se probó
a darles acceso como "último recurso" y la cobertura del año cayó del 98.9% al 97.5% — el orden
de preferencia vive en el nivel bajo del objetivo y no puede competir con el coste, en el nivel
de cobertura, de sacar al designado de su patrón. Ver `_anadir_capacidades_correturno`.

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

- **Las 190 filas de patrón repetidas**: cinco líneas (VADN004/005/008/026/151) declaradas 38
  veces, una por cada miembro de PAT_GRANDE_VALL. Es un hecho de GRUPO escrito 190 veces; cabría
  asociarlo al patrón en vez de a cada persona y el fichero bajaría a ~145 filas. Antes hay que
  medirlo: engordar o adelgazar el modelo tiene efectos grandes y poco intuitivos en la cobertura.
- **`src/validar_datos.py` está roto** desde el commit inicial (importa `DATOS_DEF`, que no
  existe). Es justo la herramienta que debería vigilar este contrato.

# Roadmap — equidad anual de findes/festivos y horas (1776/1826)

Objetivo de esta línea de trabajo: **automatizar las dos últimas etapas del proceso
manual** (igualar findes/festivos a la media, e igualar horas a ~1776), que hoy son las
más costosas porque se hacen a mano y con realimentación (quitar días a fijos → hueco →
recolocar correturnos → repetir).

Principio rector: **no reconstruir el proceso manual como un greedy por etapas.** El
modelo simultáneo ya resuelve cobertura + cascada de sustituciones por construcción. Lo
que falta es que el modelo *vea el año* en dos dimensiones globales (findes y horas) y una
**pasada de pulido** que converja lo que la ventana rodante no pudo anticipar.

---

## 0. Estado actual (lo que YA existe — no duplicar)

Leído en `src/modelo.py` (2026-07-08):

| Pieza | Función | Estado |
|---|---|---|
| Modelo simultáneo, horizonte rodante | `resolver_anual` ≈L656 | ✓ ventana 14d + cola 28d, libros acumulados |
| Cobertura ponderada por criticidad (P1) | `_coste_cobertura` ≈L399 | ✓ |
| Equidad **anual** de findes/festivos/noches (P2) | `_equidad_ponderada` ≈L448 + libro `offset` / `_actualizar_offset` | ✓ fijos EXCLUIDOS |
| Tope duro de jornada anual (C9) | `_c9_jornada_anual` ≈L491 + libro `offset_horas` | ✓ pero cap = `HMAX_AÑO` **global**, fijos fuera |
| Déficit de jornada blando (P4) | `_deficit_jornada` ≈L509 | ✓ pero prorratea desde `HMAX_AÑO`, no desde el objetivo |
| Fijación blanda de patrón (P-dev) | `_fijacion_patron` ≈L377 | ✓ |
| Congelado de fijos (C8) | `_c8_fijos` ≈L331 | ✓ **DURO** (`x==1`) |

**Bug conceptual a corregir ya:** `modelo.py:24` tiene `HMAX_AÑO = 1776`, y ese valor se
usa a la vez como tope duro (C9) y como objetivo del prorrateo (P4, `resolver_anual` ≈L684).
Según la especificación acordada:

- **Objetivo (meta de equidad, BLANDA): 1776 h/año.**
- **Tope legal (DURO, no sobrepasable): 1826 h/año.**
- **Reducción de jornada:** por trabajador, un `factor_jornada ∈ (0,1]` (default 1.0) que
  escala AMBOS: `objetivo_w = 1776·factor`, `tope_w = 1826·factor`.

**Regla sobre fijos (acordada):** un fijo es intocable **salvo** para cuadrar horas, y el
único cambio admitido es **quitarle días** (poner LIBRE). Nunca cambiarle de turno, ni
moverlo, ni añadirle. Ese día liberado pasa a demanda y lo cubre un correturno (cascada
automática por C1).

Nota de magnitud: un fijo L-V de línea 8 h hace ≈1800 h/año (≈226 días útiles tras
vacaciones y festivos). Está ~24 h por encima del objetivo y por debajo del tope → cuadrarlo
son ~3–4 días retirados. C13 es *ajuste fino*, no una perturbación grande.

---

## 1. Los cinco cambios de diseño (resumen)

- **A. Separar objetivo (1776) de tope (1826)** en dos constantes distintas.
- **B. `factor_jornada` por trabajador** (reducción de jornada) → escala objetivo y tope.
- **C. Equidad de horas simétrica** alrededor del objetivo (hoy P4 solo penaliza el déficit;
  no impide derivar hacia el tope). Los fijos ENTRAN en esta dimensión (y solo en esta).
- **D. Fijos perturbables por horas (C13):** variable `retira[w,d]` con presupuesto, único
  grado de libertad del fijo, premiado solo si el fijo está por encima de su objetivo.
- **E. Pasada de pulido global (Nivel 2):** LNS de intercambios sobre el año ya construido,
  restringido a findes+horas, con cobertura y legalidad invariantes. Es la automatización
  directa del reajuste manual.

Orden lexicográfico resultante (sin cambiar la torre de pesos actual W1>W2>W3):
`P1 cobertura ≫ P2 equidad findes/festivos ≫ (P3 perfil + P_horas + P5 estab. mixto)`.
Las horas quedan en el nivel bajo (desempate), por debajo de la equidad de findes, como
pediste.

---

## 2. Roadmap incremental (una etapa ≈ una sesión, cada una verificable sola)

### Etapa 1 — Separar objetivo (1776) de tope legal (1826)  ·  riesgo bajo
**Datos:** ninguno.
**Código:**
- `modelo.py:24`: sustituir `HMAX_AÑO = 1776` por dos constantes:
  ```python
  HORAS_OBJETIVO = 1776   # meta de equidad (blanda)
  HMAX_AÑO       = 1826   # tope legal anual (duro, no sobrepasable)
  ```
- `_c9_jornada_anual` (≈L497): sigue usando `HMAX_AÑO` (ahora 1826). Sin más cambios.
- `resolver_anual` (≈L684): el prorrateo de `objetivo_horas` pasa a partir de
  `HORAS_OBJETIVO`, no de `HMAX_AÑO`:
  ```python
  objetivo_horas[w] = round(HORAS_OBJETIVO * 60 * disp_v / disp_total)
  ```
**Criterio de hecho:** correr un tramo; comprobar en el report que (a) nadie supera 1826 h,
(b) P4 empuja hacia 1776 y no hacia 1826 (antes convergía al tope).

---

### Etapa 2 — Reducción de jornada por trabajador (`factor_jornada`)  ·  riesgo bajo
**Datos:** nueva columna en `trabajadores.csv`:
`id_trab;tipo;patron;vac1_inicio;vac2_inicio;factor_jornada` (default 1.0 si vacía).
Actualizar el contrato en la memoria `contratos-datos-cuadrantes` y el validador.
**Código:**
- `cargar_datos.py`: parsear `factor_jornada` (float, default 1.0, validar `0 < f <= 1`);
  exponer por trabajador `objetivo_w = round(HORAS_OBJETIVO*factor)` y
  `tope_w = round(HMAX_AÑO*factor)` (o guardar el factor y derivar en el modelo).
- `_c9_jornada_anual` (≈L507): `self.m.add(sum(terminos) <= tope_w*60 - off)` por trabajador.
- `resolver_anual` (≈L684): prorratear desde `objetivo_w` en vez de la constante global.
**Criterio de hecho:** un trabajador con `factor_jornada=0.5` topa en ≈913 h y su objetivo
es ≈888 h; los demás intactos.

---

### Etapa 3 — Equidad de horas simétrica  ·  riesgo medio
Hoy `_deficit_jornada` solo penaliza quedarse **por debajo** del objetivo; con el tope en
1826 nada impide que la gente derive hacia arriba. Para "equidad ~1776" hay que penalizar
también el **exceso sobre el objetivo** (dentro del tope duro).
**Código:** renombrar/ampliar `_deficit_jornada` → `_desviacion_jornada` que devuelva la
suma de `|horas_acumuladas − objetivo|`:
- déficit `d_w >= objetivo_w − (off + Σ min_ventana)` (ya está),
- exceso `e_w >= (off + Σ min_ventana) − objetivo_w` (nuevo),
- penalizar `d_w + e_w` (pueden llevar pesos distintos; recomendación: iguales).
Mantener el término en el nivel bajo `W3·(p3 + p_horas + PESO_ESTAB·p5)` en `resolver` (≈L623).
**Decisión abierta (marca en el código):** ¿penalizar exceso igual que déficit, o más
suave? Empezar iguales y ajustar viendo el histograma de horas del report.
**Criterio de hecho:** el histograma de horas anuales se estrecha en torno a 1776 en vez de
apelotonarse contra 1826.

---

### Etapa 4 — Fijos perturbables por horas (C13, "quitar día")  ·  riesgo alto
El grado de libertad ÚNICO del fijo: poner LIBRE algunos días para bajar sus horas al
objetivo. Nada más.
**Nivel 0 (precómputo, en `resolver_anual` antes de rodar):** para cada fijo con línea
`phi` (usar `_linea_fija`), calcular horas base del año = Σ sobre días operativos y
disponibles de `turnos[phi].horas`. Presupuesto de retirada
`B_w = ceil(max(0, base − objetivo_w) / horas_por_día) + margen(1)`.
**Modelo:**
- `_crear_variables`: para cada fijo y cada (día operativo, disponible), bool `retira[w,f]`.
- `_c8_fijos` (≈L331): cambiar el congelado duro `x==1` por
  `x[(w,f,phi)] == 1 - retira[(w,f)]` (sigue siendo la línea o LIBRE; nunca otro turno).
- Nueva restricción de presupuesto: `Σ_f retira[w,f] <= B_w`.
- Penalización pequeña `PESO_RETIRA · Σ retira` en el nivel bajo, para que **no retire
  gratis** (solo cuando mejora la equidad de horas del propio fijo).
- **Contabilidad:** meter los minutos del fijo (`base − Σ retira·min_por_día`) en la
  dimensión de horas (Etapa 3), de modo que retirar días *reduce* su desviación. Los fijos
  siguen FUERA de la equidad de findes/festivos (P2) y de `_coste_preferencia_trabajador`.
- **Cobertura/cascada:** el día retirado deja `phi` sin cubrir → C1 obliga a taparlo con
  correturno o lo reporta como hueco `u`. Sin lógica extra.
**Sutileza rodante:** las horas del fijo son anuales. Añadir un libro `offset_horas_fijo`
análogo a `offset_horas` (sumar minutos del fijo por ventana en `_actualizar_offset_horas`,
que hoy salta fijos ≈L651) para que cada ventana sepa cuánto lleva y decida retiradas
contra el prorrateo. *Alternativa más simple si se complica:* decidir en Nivel 0 el número
total de días a retirar por fijo y tratarlos como "vacantes" que entran ya al modelo
rodante (menos óptimo pero más controlable).
**Criterio de hecho:** un fijo que sin tocar haría ≈1800 h pierde ~3 días, queda ≈1776, y
esos días aparecen cubiertos por correturno (no como hueco) cuando hay holgura.

---

### Etapa 5 — Pasada de pulido global (Nivel 2, LNS de intercambios)  ·  riesgo alto, valor alto
Módulo nuevo `src/pulido.py`. Es **aditivo**: no toca el modelo, opera sobre el `plan`
final `{(trab,fecha):turno}` que devuelve `resolver_anual`.
```python
def pulir_anual(plan, datos, inicio, fin, tiempo=60, hilos=8) -> dict:
    """Converge la equidad residual (findes/festivos + horas) que la ventana rodante
    no pudo anticipar, mediante intercambios locales que preservan cobertura y legalidad."""
```
Piezas internas:
- `_desviaciones(plan, datos)` → por trabajador: nº findes, nº festivos, horas anuales, y su
  desviación respecto a media/objetivo.
- `_vecindario(plan, desv, datos)` → candidatos de mejora: un turno de un trabajador **por
  encima** de su objetivo que un trabajador **por debajo** pueda asumir (elegibilidad por
  `capacidades`, descanso C4/C5/C7/C11, no vacaciones, no supera su tope C9). Para horas de
  fijos: retirada de día + recobertura por correturno bajo objetivo.
- `_resolver_vecindario(...)` → CP-SAT pequeño sobre el conjunto de días afectados, TODO lo
  demás congelado; objetivo = minimizar desviación residual; **invariantes duros**: cobertura
  ≥ la actual (no empeorar P1), legalidad exacta (C4–C7, C11, C9/tope, C13 en fijos).
- Bucle: aplicar el mejor movimiento mientras mejore y haya presupuesto de tiempo.
**Criterio de hecho:** tras el pulido, el rango de findes entre trabajadores capaces baja, y
las horas se agrupan más cerca del objetivo, **sin perder cobertura**. Es la automatización
directa del reajuste manual (pasos 4–5).

---

### Etapa 6 — Report de equidad  ·  riesgo bajo
`salida.py`: añadir al Excel/HTML una tabla por trabajador con:
horas anuales vs objetivo vs tope · nº sábados/domingos/festivos vs media del grupo ·
días retirados a fijos. Es lo que la empresa necesita para *ver* que el reparto es justo y
para auditar por qué un fijo perdió un día.
**Criterio de hecho:** el responsable puede validar la equidad de un vistazo, sin abrir el
solver.

---

## 3. Dependencias y orden sugerido

```
Etapa 1 ──▶ Etapa 2 ──▶ Etapa 3 ──▶ Etapa 4
                                   └▶ Etapa 5  (necesita la contabilidad de 1–4)
Etapa 6 puede hacerse en paralelo desde que exista la contabilidad (tras Etapa 3).
```

Empieza por 1–2 (baratas, desbloquean el resto y ya corrigen el bug objetivo/tope). La 4 y
la 5 son las de más riesgo; hazlas con un tramo corto de prueba (1–2 meses) antes de correr
el año completo.

## 4. Métrica y herramienta (decisiones ya tomadas)

- **Herramienta:** OR-Tools CP-SAT, lo que ya usas. El pulido (Etapa 5) también en CP-SAT
  (vecindario pequeño con el resto congelado). No hace falta otra tecnología.
- **Métrica de equidad:** L1 / desviación proporcional (como ya hace `_equidad_ponderada`),
  no L2. Para horas, desviación absoluta respecto al objetivo (Etapa 3).
- **Lexicográfico**, sin pesos entre niveles conceptuales: cobertura ≫ equidad findes ≫ horas.

## 5. Decisiones abiertas / pendientes de confirmar con la empresa

- Peso relativo exceso vs déficit de horas (Etapa 3): arrancar iguales.
- ¿El tope 1826 y el objetivo 1776 aplican por igual a todos los convenios/municipios, o
  varían? De momento constantes globales escaladas por `factor_jornada`.
- Magnitud del margen en `B_w` (Etapa 4): arrancar en 1.

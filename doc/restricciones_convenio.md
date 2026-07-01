# Restricciones del convenio (V Convenio Transporte Sanitario Castilla y León)

Fuente: **BOCyL nº 51, 15/03/2022** — V Convenio Colectivo para las empresas y trabajadores
de Transporte Sanitario de Enfermos/as y Accidentados/as de CyL. Vigencia 1/1/2019 →
31/12/2026. Lo no previsto se rige por el convenio **estatal** + normativa laboral.

Extracto orientado al generador de cuadrantes. Marca: ✅ ya en el modelo · 🟡 falta añadir ·
📄 afecta a datos de entrada · 📤 afecta a la salida/proceso · ❓ a confirmar.

---

## 1. Parámetros legales confirmados (cifras para fijar en `modelo.py`)

| Parámetro | Valor del convenio | Artículo | Estado |
|---|---|---|---|
| Descanso mínimo entre jornadas | **12 h** | Art. 23, 31 | ✅ `RMIN=12` |
| Máx. trabajo efectivo en 7 días | **48 h** (periodo de referencia 12 meses) | Art. 23.2 | ✅ `HMAX7=48` |
| **Jornada anual efectiva** | **1.776 h** (reducida desde 1.800; bajada gradual de 8 h/año) | Art. 23 A | 🟡 fijar `HMAX_AÑO=1776` |
| **Máx. cuatrisemanal** | **160 h / 4 semanas** de trabajo efectivo | Art. 23 A | 🟡 NUEVA restricción |
| **Jornada diaria máxima** | **9 h** de trabajo efectivo (mín. 6 h a efectos de horas extra); **excepto guardias** | Art. 23 | 🟡 NUEVA (validar turnos: algunos duran 10 h) |
| Nocturnidad | franja **22:00–06:00** (+10% retributivo) | Art. 22 | ✅ `noct` usa 22-06 |
| Horas extraordinarias | +75% si no se descansan; voluntarias | Art. 25 | (retributivo, no restringe cuadrante) |
| Horas suplementarias | diferencia 1.826 (ET) − 1.776; voluntarias | Art. 23 B | (voluntario) |

> `ρ` (días de descanso tras turno de 24 h) **no aparece explícito** en este convenio para el
> no urgente. Ver §5. Revisar el `RHO=3` que usamos hoy (es una suposición).

---

## 2. Descansos y secuencias (restricciones DURAS)

- ✅ **Descanso entre jornadas ≥ 12 h** (Art. 23, 31). Ya es C4.
- 🟡 **Descanso semanal = 2 días CONSECUTIVOS** (Art. 24). "Se facilitarán dos días de
  descanso consecutivos, procurando al menos **cuatrisemanalmente con sábado y domingo**".
  Nuestra C11 (≤5 días/semana) NO garantiza que los libres sean consecutivos ni que cada
  4 semanas caiga un sáb+dom. **Reforzar C11** con la consecutividad y el sáb+dom cuatrisemanal.
- 🟡 **No encadenar diurno ↔ nocturno** (Art. 30). Los TES conductores/ayudantes que hayan
  hecho servicio **diurno no pueden hacer seguidamente uno nocturno, y a la inversa**. Es una
  incompatibilidad de secuencia adicional a las 12 h: añadir a los pares prohibidos de C4 las
  transiciones día→noche y noche→día en días consecutivos.
- 🟡 **Días consecutivos máximos**: el convenio no da un número directo para TSNU, pero el
  dispositivo de localización fija **máx. 5 días seguidos + 2 de descanso consecutivos**
  (Art. 23.1). Alinear `CMAX` ≈ 5 (hoy 6) y encadenar con el descanso semanal de 2 días.
- 📄 **Cambio de turno entre trabajadores**: permitido con 24 h de preaviso, respetando las
  12 h de descanso (Art. 31). (Operativo, no del solver.)

---

## 3. Turnos, jornada partida y guardias

- 🟡 **Turno partido** (Art. 23): jornada continuada preferente; si es partida, **una sola
  interrupción de máximo 2 h, entre las 13:00 y las 16:00**. Esto **define** el flag `partido`:
  un turno es partido si tiene ese corte. Útil para validar/derivar el campo `partido`.
- **Guardias especiales** (Art. 23.2) — régimen del **urgente (TSU)**: turnos >8 h y hasta
  24 h, alternando trabajo efectivo con pausas. Garantizan **≤48 h efectivas/7 días** en
  referencia de 12 meses. Las horas de solape se compensan con descanso.
- **Dispositivo de localización** (Art. 23.1): guardia localizada; **máx. 5 días seguidos**,
  **2 días de descanso consecutivos** al terminar (no compensables), activación ≤6 h de media
  diaria en los 5 días. Modelo alternativo de cobertura de huecos (≈ nuestro refuerzo `V`).
- ❓ **24 h en NO urgente**: las guardias de 24 h son el régimen del **urgente**. Para el
  programado (TSNU) el convenio no prevé la guardia de 24 h como norma → confirma la bandera
  roja: nuestros turnos de 24 h (VADP003, VADU47127, VADN177) en un cuadrante no urgente
  siguen siendo una anomalía a aclarar con la empresa.
- 📄 **Movilidad programado ↔ urgente** (Art. 23): un trabajador puede prestar en ambos si
  está en el calendario o con **96 h de preaviso**. Relevante para quién cubre guardias.

---

## 4. Festivos, vacaciones y Navidad (colocación / equidad)

- 🟡 **Festivos rotativos** para todo el personal del mismo servicio (Art. 24). Refuerza que
  los festivos deben repartirse (ya está en la equidad P2 como carga "festivo"), pero además
  sugiere una **rotación** explícita.
- 📄 **Vacaciones** (Art. 29): **30 días naturales/año** (= nuestras 2 quincenas ✅). Reglas:
  - **≥ la mitad** debe poder disfrutarse en **periodo estival: 1 jun – 30 sep**.
  - Turnos de vacaciones **rotativos por antigüedad** (empezando por los más antiguos).
  - Quincena disfrutada **fuera** del estival → **+1 día** de vacaciones (salvo mes completo
    ininterrumpido en otra época).
  → Validación de datos: comprobar que las vacaciones de entrada cumplen el ≥50% estival.
- 🟡 **Cuadrante de Navidad** (Art. 28) — sobre todo **emergencias**: rotativo; cada trabajador
  de una base de 24 trabaja **como máximo 1 día** de {24, 25 dic} o {31 dic, 1 ene}; el resto
  del personal de movimiento, máximo 2 días del mismo grupo. Restricción específica del periodo
  navideño si se modela.
- **Plus festivo** (Art. 27): 12 festivos nacionales + 2 locales + 24 y 31 dic (retributivo).

---

## 5. Disponibilidad / elegibilidad (afecta a `avail` y a la capacidad)

- 📄 **Embarazo** (Art. 68): la trabajadora embarazada **no puede hacer trabajo a turnos ni
  trabajo nocturno** (entre otros, desde el inicio). → restricción de elegibilidad temporal
  (no noches, no turnos) mientras dure.
- 📄 **Lactancia acumulada** (Art. 47): hasta **14 días laborables consecutivos** (resto de
  servicios) / 5 días en urgencia 24 h. → ausencia como una vacación corta.
- 📄 **Permisos y licencias retribuidas** (Art. 49): matrimonio 16 d, fallecimiento/
  hospitalización familiares 2–7 d, traslado, deberes públicos, exámenes prenatales, etc. →
  ausencias puntuales = `avail=0` esos días.
- 📄 **Tiempo de libre disposición** (Art. 50): **3 días laborables/año**, no recuperables;
  no en fin de semana, **ni 10 jul–31 ago, ni 22 dic–7 ene**; no pueden coincidir dos
  trabajadores de la misma base/zona. → ausencias con reglas de reparto.
- **Movilidad funcional / apto médico** (Art. 30, 42, 67): conductores/ayudantes necesitan
  vigilancia de la salud y aptitud; un "no apto" cambia su puesto. (Capacidad, caso borde.)

---

## 6. Salida y proceso (afecta a lo que entregamos)

- 📤 **Calendario laboral** (Art. 32, y art. 34.4 ET): debe comprender **horario, distribución
  anual de días de trabajo, festivos, descansos semanales, vacaciones y días inhábiles**. Es
  el contenido que nuestro `calendario_anual.csv` debe reflejar. Negociado con la RLT y
  publicado en el tablón.
- 📤 **Antelación de publicación de cuadros de horarios** (Art. 32): **mensual 5 días,
  quincenal 3 días, semanal 2 días, diario 2 h** antes del fin de la jornada anterior. Marca
  los plazos con los que el generador debe entregar cada ventana.

---

## 7. Resumen de acciones para el modelo

**Fijar/ajustar constantes** (`modelo.py`):
- `HMAX_AÑO = 1776` (jornada anual efectiva). ✅ dato nuevo.
- Añadir `HMAX_4SEM = 160` (máx. cuatrisemanal). 🟡
- `JORNADA_DIA_MAX = 9 h` (salvo guardias). 🟡 (validar turnos de 10 h)
- Revisar `CMAX` (≈5) y `RHO` (24 h no explícito en no urgente ❓).

**Restricciones a añadir**:
1. 🟡 Máx. **160 h / 4 semanas** (ventana móvil de 28 días) — análoga a C6 pero cuatrisemanal.
2. 🟡 Descanso semanal de **2 días consecutivos** + **sáb+dom al menos cada 4 semanas** (C11 reforzada).
3. 🟡 **Prohibir día↔noche consecutivos** para conductores/ayudantes (pares de C4).
4. 🟡 (opcional) Jornada diaria ≤ **9 h** efectivas salvo guardia.
5. 🟡 (periodo navideño) tope de días de Navidad por trabajador (Art. 28) si se modela.

**Validaciones de datos** (`validar_datos.py`):
- Vacaciones: **≥50% en verano** (1 jun–30 sep) por trabajador (Art. 29).
- Turnos: avisar si `dur > 9 h` y no es guardia (Art. 23).

**A confirmar con la empresa**:
- ❓ Legalidad/uso de la **guardia de 24 h en NO urgente** (Art. 23.2 la sitúa en el urgente).
- ❓ Valor de **`ρ`** (descanso tras 24 h) — no explícito aquí; posiblemente vía convenio estatal.
- ❓ Si se aplican los complementos (nocturnidad, festivo, programado) como coste en P4.

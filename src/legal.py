"""JUEZ ÚNICO del convenio. Todos los pasos preguntan aquí antes de asignar.

Existe porque la legalidad ACOPLA los pasos: `rmin`, `hmax7` y `cmax` son restricciones sobre la
semana de UNA persona, y un turno asignado en el paso 2 se come el presupuesto de esa persona para
el paso 4. Con un juez compartido y una verificación tras cada paso, un incumplimiento se detecta
en el paso que lo causó. En la rama base esto se manifestaba como ventanas INFEASIBLE meses
después."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from calendario import semana
from cargar_datos import DIAS, LIBRE, Datos
from plan import Plan


class Legal:
    def __init__(self, datos: Datos) -> None:
        self.datos = datos
        self.cfg = datos.config
        self.pares_c4 = self._pares_pactados()

    # -- Exención ----------------------------------------------------------- #
    def _pares_pactados(self) -> set[tuple[str, str]]:
        """Encadenamientos que la rotación PACTADA contiene y que quedan exentos de C4.

        Es la ÚNICA exención legal del sistema. Los localizados 24 h (22:00→22:00) incumplen C4 por
        diseño: VADU47127 encadena consigo mismo con 0 h de descanso y PAT_MEDINA llega a −11 h.
        La exención es del PAR DE TURNOS y de la rotación, no del trabajador: quien cubre una línea
        de localizado hace la secuencia de ESE patrón y debe poder hacerla entera igual que su
        titular. Cualquier otro encadenamiento sí exige las 12 h."""
        pares: set[tuple[str, str]] = set()

        def ocupado(s: str | None) -> bool:
            return bool(s) and s != LIBRE and s in self.datos.turnos

        for filas in self.datos.patrones.values():
            total = len(filas)
            for k, fila in enumerate(filas):
                for j, dia in enumerate(DIAS):
                    s1 = fila.get(dia)
                    # el domingo encadena con el lunes de la fila SIGUIENTE de la rotación
                    siguiente = fila if j < 6 else filas[(k + 1) % total]
                    s2 = siguiente.get(DIAS[(j + 1) % 7])
                    if ocupado(s1) and ocupado(s2):
                        pares.add((s1, s2))
        return pares

    # -- Consultas ---------------------------------------------------------- #
    def tope_dias(self, w: str) -> int:
        """C5: días máximos por semana ISO. La plantilla FLEXIBLE tiene su propio tope."""
        tipo = self.datos.trabajadores[w].tipo
        return self.cfg.cmax_pool if tipo in ("mixto", "correturno") else self.cfg.cmax

    def _descanso_h(self, s_prev: str, f_prev: date, s_next: str, f_next: date) -> float:
        """Horas entre la SALIDA de `s_prev` y la ENTRADA de `s_next`."""
        t_prev, t_next = self.datos.turnos[s_prev], self.datos.turnos[s_next]
        sale = datetime.combine(f_prev, t_prev.hora_salida)
        if t_prev.hora_salida <= t_prev.hora_entrada:      # cruza medianoche
            sale += timedelta(days=1)
        entra = datetime.combine(f_next, t_next.hora_entrada)
        return (entra - sale).total_seconds() / 3600.0

    def puede(self, plan: Plan, w: str, f: date, turno: str) -> tuple[bool, str]:
        """¿Puede `w` hacer `turno` el día `f` sin romper el convenio? (ok, motivo)."""
        if plan.ocupado(w, f):
            return (False, "el dia ya esta decidido")
        if not self.datos.disponible(w, f):
            return (False, "de vacaciones")

        dias = plan.dias_de(w)
        sem = semana(f)

        # C5 — días por semana ISO
        en_sem = [d for d in dias if semana(d) == sem]
        if len(en_sem) + 1 > self.tope_dias(w):
            return (False, f"C5: superaria {self.tope_dias(w)} dias en la semana {sem[1]}")

        # C6 — horas legales por semana ISO
        horas = sum(self.datos.turnos[plan.turno_de(w, d)].horas for d in en_sem)
        if horas + self.datos.turnos[turno].horas > self.cfg.hmax7:
            return (False, f"C6: superaria {self.cfg.hmax7} h en la semana {sem[1]}")

        # C4 — descanso mínimo con el día anterior y el siguiente
        for otro in (f - timedelta(days=1), f + timedelta(days=1)):
            s_otro = plan.turno_de(w, otro)
            if s_otro is None:
                continue
            antes, despues = (s_otro, turno) if otro < f else (turno, s_otro)
            f_antes, f_despues = (otro, f) if otro < f else (f, otro)
            if (antes, despues) in self.pares_c4:
                continue                       # encadenamiento pactado: exento
            if self._descanso_h(antes, f_antes, despues, f_despues) < self.cfg.rmin:
                return (False, f"C4: menos de {self.cfg.rmin} h de descanso con {s_otro} del {otro}")

        return (True, "")

    def verificar(self, plan: Plan) -> list[str]:
        """Repasa el plan ENTERO. Lista vacía = legal. Se llama tras cada paso."""
        fallos: list[str] = []
        por_trab: dict[str, list[date]] = {}
        for (w, f), _ in sorted(plan.asignaciones().items()):
            por_trab.setdefault(w, []).append(f)

        for w in sorted(por_trab):
            dias = sorted(por_trab[w])
            por_sem: dict[tuple[int, int], list[date]] = {}
            for d in dias:
                por_sem.setdefault(semana(d), []).append(d)

            for sem in sorted(por_sem):
                ds = por_sem[sem]
                if len(ds) > self.tope_dias(w):
                    fallos.append(f"C5 {w} semana {sem}: {len(ds)} dias > {self.tope_dias(w)}")
                horas = sum(self.datos.turnos[plan.turno_de(w, d)].horas for d in ds)
                if horas > self.cfg.hmax7:
                    fallos.append(f"C6 {w} semana {sem}: {horas} h > {self.cfg.hmax7}")

            for a, b in zip(dias, dias[1:]):
                if (b - a).days != 1:
                    continue
                s_a, s_b = plan.turno_de(w, a), plan.turno_de(w, b)
                if (s_a, s_b) in self.pares_c4:
                    continue
                d = self._descanso_h(s_a, a, s_b, b)
                if d < self.cfg.rmin:
                    fallos.append(f"C4 {w} {a}->{b}: {d:.1f} h < {self.cfg.rmin}")
        return fallos

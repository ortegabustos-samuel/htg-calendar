"""
zonas/valladolid/reglas.py — Las reglas de reparto de la zona de Valladolid.

No son del convenio (eso es legal.py), son de cómo se organiza esta zona.

  domingo_ok         nunca domingo sin el sábado de ese fin de semana
  descanso_finde_ok  si se trabajan sábado y domingo, dos días libres seguidos entre semana
"""
from __future__ import annotations

from datetime import date, timedelta

import legal
from dominio import Datos, Plan, TipoDia


def domingo_ok(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """El domingo solo se trabaja si también se trabaja el sábado de ese mismo fin de semana."""
    if datos.tipo_dia(fecha, datos.turnos[turno_id].base) != TipoDia.DOMINGO:
        return True
    return plan.turno_de(trabajador_id, fecha - timedelta(days=1)) is not None


def descanso_finde_ok(datos: Datos, plan: Plan, trabajador_id: str, lunes: date) -> bool:
    """Si esa semana ISO (`lunes`..`lunes+6`) se trabajan sábado Y domingo, exige un par de días
    consecutivos libres entre semana. No depende de un día contra el anterior sino de la semana
    entera, así que se evalúa una vez decidida la semana. Libre es no trabajar: descanso o hueco."""
    dias = [lunes + timedelta(days=i) for i in range(7)]
    if (plan.turno_de(trabajador_id, dias[5]) is None
            or plan.turno_de(trabajador_id, dias[6]) is None):
        return True
    libres = {d for d in dias[:5] if plan.turno_de(trabajador_id, d) is None}
    return any(dias[i] in libres and dias[i + 1] in libres for i in range(4))


def permite(datos: Datos, plan: Plan, trabajador_id: str, fecha: date, turno_id: str) -> bool:
    """Lo del convenio (legal.permite) más la regla del domingo de esta zona."""
    return (legal.permite(datos, plan, trabajador_id, fecha, turno_id)
            and domingo_ok(datos, plan, trabajador_id, fecha, turno_id))

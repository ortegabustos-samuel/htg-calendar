"""El calendario en construcción y su LIBRO DE DECISIONES.

No tiene lógica de negocio: no sabe qué es legal ni qué es justo. Solo guarda quién hace qué, quién
cede, qué se quedó sin cubrir, y —esto es lo que lo diferencia del `dict` que usaba el modelo
anterior— POR QUÉ. El libro es lo que hace el cuadrante defendible: ante cualquier celda hay una
frase que la justifica y la lista de quién más podía haberlo hecho."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Decision:
    """Una línea del libro. `turno=None` es una cesión; `liberado` marca lo que el paso 5 deshizo."""
    trabajador: str
    fecha: date
    turno: str | None
    paso: str
    regla: str
    descartados: tuple[str, ...] = ()
    liberado: bool = False


@dataclass(frozen=True)
class Hueco:
    fecha: date
    turno: str
    motivo: str


@dataclass
class Plan:
    _asign: dict[tuple[str, date], str] = field(default_factory=dict)
    _cesiones: set[tuple[str, date]] = field(default_factory=set)
    libro: list[Decision] = field(default_factory=list)
    huecos: list[Hueco] = field(default_factory=list)

    # -- Escritura ---------------------------------------------------------- #
    def asignar(self, w: str, f: date, turno: str, paso: str, regla: str,
                descartados: tuple[str, ...] = ()) -> None:
        if (w, f) in self._asign or (w, f) in self._cesiones:
            raise ValueError(f"{w} ya tiene algo el {f}: "
                             f"{self._asign.get((w, f), 'CESION')}")
        self._asign[(w, f)] = turno
        self.libro.append(Decision(w, f, turno, paso, regla, descartados))

    def ceder(self, w: str, f: date, paso: str, regla: str) -> None:
        """Marca el día como LIBRADO: el trabajador no hará lo que su rotación prescribe."""
        if (w, f) in self._asign or (w, f) in self._cesiones:
            raise ValueError(f"{w} ya tiene algo el {f}")
        self._cesiones.add((w, f))
        self.libro.append(Decision(w, f, None, paso, regla))

    def liberar(self, w: str, f: date, paso: str, regla: str) -> str | None:
        """Deshace lo del día y devuelve el turno que había. SOLO el paso 5 puede llamarlo."""
        turno = self._asign.pop((w, f), None)
        self._cesiones.discard((w, f))
        self.libro.append(Decision(w, f, turno, paso, regla, liberado=True))
        return turno

    def hueco(self, f: date, turno: str, motivo: str) -> None:
        self.huecos.append(Hueco(f, turno, motivo))

    # -- Consulta ----------------------------------------------------------- #
    def turno_de(self, w: str, f: date) -> str | None:
        return self._asign.get((w, f))

    def cedido(self, w: str, f: date) -> bool:
        return (w, f) in self._cesiones

    def ocupado(self, w: str, f: date) -> bool:
        """El día está decidido: o trabaja, o cede. En ambos casos no admite nada más."""
        return (w, f) in self._asign or (w, f) in self._cesiones

    def cubierto(self, f: date, turno: str) -> int:
        return sum(1 for (_, d), s in self._asign.items() if d == f and s == turno)

    def dias_de(self, w: str) -> list[date]:
        """Fechas en que `w` TRABAJA, ordenadas. Las cesiones no cuentan."""
        return sorted(d for (t, d) in self._asign if t == w)

    def asignaciones(self) -> dict[tuple[str, date], str]:
        """Copia del calendario en el formato que come `salida.generar_anual`."""
        return dict(self._asign)

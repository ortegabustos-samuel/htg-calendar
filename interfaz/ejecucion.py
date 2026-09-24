"""
ejecucion.py — Lanza src/pipeline.py sobre un escenario y sigue su avance.

El pipeline corre en un proceso aparte (tarda minutos) y un hilo va leyendo su salida. Las
ejecuciones viven en un registro compartido por toda la app (st.cache_resource), no en la sesión:
así una ejecución sigue localizable aunque se cambie de página o se recargue el navegador, y no se
pueden lanzar dos a la vez sobre el mismo escenario.

El progreso se deduce de lo que imprime el pipeline: las marcas `[n/7] …` de cada paso y, dentro
del paso 5 (el solver, casi todo el tiempo), las líneas `nivel …` que cierra cada uno de sus tres
niveles. Mientras un nivel resuelve se avanza por tiempo, sabiendo que tiene un tope de
`segundos`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

import ficheros as fx

PIPELINE = fx.RAIZ / "src" / "pipeline.py"
NIVELES_SOLVER = 3

# Peso de cada paso en la barra (suman 1). El 5 es el solver anual.
PESOS = {1: 0.02, 2: 0.05, 3: 0.05, 4: 0.08, 5: 0.70, 6: 0.05, 7: 0.05}
_MARCA = re.compile(r"^\[(\d+)/(\d+)\] (.*)$")
_NIVEL = re.compile(r"^\s*nivel [123] ")


@dataclass
class Ejecucion:
    escenario: str
    salida: Path
    segundos: int
    proc: subprocess.Popen
    inicio: float = field(default_factory=time.time)
    lineas: list[str] = field(default_factory=list)
    paso: int = 0
    texto_paso: str = "Arrancando"
    niveles: int = 0                 # niveles del solver ya cerrados
    ultimo_evento: float = field(default_factory=time.time)
    cancelada: bool = False

    @property
    def terminada(self) -> bool:
        return self.proc.poll() is not None

    @property
    def ok(self) -> bool:
        return self.proc.poll() == 0 and not self.cancelada

    def _leer(self) -> None:
        for linea in self.proc.stdout:
            linea = linea.rstrip("\n")
            self.lineas.append(linea)
            if m := _MARCA.match(linea):
                self.paso, self.texto_paso = int(m.group(1)), m.group(3)
                self.ultimo_evento = time.time()
            elif self.paso == 5 and _NIVEL.match(linea):
                self.niveles += 1
                self.ultimo_evento = time.time()

    def progreso(self) -> float:
        if self.terminada:
            return 1.0 if self.ok else self._acumulado()
        return self._acumulado()

    def _acumulado(self) -> float:
        hecho = sum(PESOS[p] for p in PESOS if p < self.paso)
        if self.paso == 5:
            # Dentro del nivel en curso se avanza por tiempo, sin llegar nunca a cerrarlo: puede
            # acabar antes del tope, pero no después.
            en_curso = min((time.time() - self.ultimo_evento) / max(self.segundos, 1), 0.95)
            hecho += PESOS[5] * (min(self.niveles, NIVELES_SOLVER) + en_curso) / NIVELES_SOLVER
        return min(hecho, 0.99)

    def duracion(self) -> str:
        s = int(time.time() - self.inicio)
        return f"{s // 60}:{s % 60:02d}"

    def cancelar(self) -> None:
        self.cancelada = True
        self.proc.terminate()


@st.cache_resource
def _registro() -> dict[str, Ejecucion]:
    return {}


def actual(escenario: str) -> Ejecucion | None:
    return _registro().get(escenario)


def lanzar(escenario: str, salida: Path, segundos: int, hilos: int) -> Ejecucion:
    previa = actual(escenario)
    if previa is not None and not previa.terminada:
        raise RuntimeError("ya hay una ejecución en marcha para este escenario")
    salida.mkdir(parents=True, exist_ok=True)
    entorno = os.environ | {"HT_DATOS": str(fx.carpeta_entrada(escenario)),
                            "HT_SALIDA": str(salida), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-u", str(PIPELINE), "--segundos", str(segundos), "--hilos", str(hilos)],
        cwd=fx.RAIZ, env=entorno, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    ejecucion = Ejecucion(escenario=escenario, salida=salida, segundos=segundos, proc=proc)
    threading.Thread(target=ejecucion._leer, daemon=True).start()
    _registro()[escenario] = ejecucion
    return ejecucion


def resolver_salida(texto: str) -> Path:
    """Una ruta relativa se toma desde la raíz del proyecto."""
    ruta = Path(texto.strip()).expanduser()
    return (ruta if ruta.is_absolute() else fx.RAIZ / ruta).resolve()


def problema_salida(ruta: Path) -> str | None:
    """Por qué no se puede escribir en `ruta`, o None si se puede (existiendo o creándola)."""
    if ruta.exists() and not ruta.is_dir():
        return "existe pero es un fichero, no una carpeta"
    padre = ruta
    while not padre.exists():
        padre = padre.parent
    if not os.access(padre, os.W_OK):
        return f"no hay permiso de escritura en {padre}"
    return None

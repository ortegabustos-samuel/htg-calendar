"""
zonas — Lo propio de cada zona: sus reglas y la receta con que se construye su cuadrante.

PASOS lleva, por nombre de zona (el de `config.toml`), el módulo con su receta: `resolver(...)`
y `N_PASOS`.
"""
from zonas.valladolid import pasos as valladolid

PASOS = {
    "Valladolid": valladolid,
}

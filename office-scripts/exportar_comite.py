"""
exportar_comite.py — Traduce el cuadrante ya generado (y editado por el comité) a un PDF: cada
turno se sustituye por el texto y el formato que el comité le haya puesto en la columna Leyenda
Comité de la hoja Conversión Comité.

No depende de `cargar_datos.Datos` ni del resto del pipeline: lee directamente el .xlsx tal cual
está en disco, con openpyxl. Localiza cada tabla por RANGO CON NOMBRE (CuadranteDNI, ComiteLinea...,
definidos por `salida.py` al generar el fichero), no buscando celdas por su texto — así no depende
de que una etiqueta como "Trabajador" o "Línea" no cambie nunca.

El fondo de cada celda parte del que YA tiene en el Cuadrante (festivo, fin de semana, laborable o
vacaciones — `salida.py` ya lo calculó bien por municipio); la leyenda del comité lo sustituye SOLO
si el comité le puso su propio color a esa línea. "V" (vacaciones) no pasa por la leyenda del comité,
solo se le pone la fuente negra en negrita, igual que en el Excel. Un turno real sin leyenda todavía
(el comité no lo ha rellenado, o hay un código suelto que no aparece en Conversión Comité) se deja
con su código original y el color de calendario de fondo, y se avisa por consola cuántas celdas
quedaron así — para que se note, no para que desaparezca en silencio.

Uso:
    python3 office-scripts/exportar_comite.py [entrada.xlsx] [salida.pdf]
"""
from __future__ import annotations

import argparse
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles.colors import COLOR_INDEX

_NS_DRAWING = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "data" / "output"

RE_TITULO = re.compile(r"(\d{2}/\d{2}/\d{4})\s*[–-]\s*(\d{2}/\d{2}/\d{4})")

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# -- Estética: una sola página GRANDE por mes (pensada para verse con zoom, no para imprimir a
# tamaño real), con todos los trabajadores juntos, sin secciones. Los colores de cada celda son los
# que ya trae el Cuadrante (festivo/finde/laborable/vacaciones) y, por encima, los que ponga el
# comité en Leyenda Comité — esto es solo el andamiaje neutro que los rodea, cabecera en blanco. -- #
COLOR_TINTA = "#1A2333"
COLOR_PAPEL = "#FBFAF7"
COLOR_LINEA = "#9AA3AD"
COLOR_ZEBRA = "#EEF1F4"
ANCHO_COL_DIA_MM = 26
ANCHO_DNI_MM = 24
ANCHO_NOMBRE_MM = 58
NDIAS_MAX = 31
ALTO_FILA_MM = 5.2


def _resolver_nombre(wb: Workbook, nombre: str):
    """Hoja y celda(s) del primer destino de un rango con nombre del propio libro. `salida.py`
    define estos nombres al generar el Excel; si faltan, el fichero no viene de nuestro pipeline o
    es de una versión anterior sin rangos con nombre."""
    try:
        definido = wb.defined_names[nombre]
    except KeyError:
        raise ValueError(f"el libro no define el rango con nombre '{nombre}' — "
                          f"¿es un calendario.xlsx generado por una versión antigua?") from None
    hoja, coords = next(iter(definido.destinations))
    return wb[hoja][coords]


def _celda_nombrada(wb: Workbook, nombre: str):
    """La celda de un rango con nombre de UNA sola celda (p.ej. "Cuadrante!$A$1")."""
    return _resolver_nombre(wb, nombre)


def _columna_nombrada(wb: Workbook, nombre: str) -> list:
    """Las celdas, en orden, de un rango con nombre de UNA sola columna (varias filas)."""
    return [fila[0] for fila in _resolver_nombre(wb, nombre)]


@dataclass
class Leyenda:
    texto: str
    fondo: str | None
    color_fuente: str | None
    negrita: bool


def _tema_colores(ruta_xlsx: Path) -> list[str]:
    """Los 10 colores del ESQUEMA DE TEMA del libro, en el mismo orden que usa `Color.theme` (0-9):
    lt1, dk1, lt2, dk2, accent1..accent6. El XML declara dk1/lt1 (y dk2/lt2) en el orden contrario
    al índice que usa openpyxl — es un gotcha conocido del formato OOXML, no un despiste."""
    with zipfile.ZipFile(ruta_xlsx) as z:
        xml_bytes = z.read("xl/theme/theme1.xml")
    esquema = ET.fromstring(xml_bytes).find(".//a:clrScheme", _NS_DRAWING)

    def rgb_de(tag: str) -> str:
        el = esquema.find(f"a:{tag}", _NS_DRAWING)
        srgb = el.find("a:srgbClr", _NS_DRAWING)
        if srgb is not None:
            return srgb.get("val")
        return el.find("a:sysClr", _NS_DRAWING).get("lastClr")

    orden = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4",
             "accent5", "accent6"]
    return [rgb_de(tag) for tag in orden]


def _aplicar_tint(rgb_hex: str, tint: float) -> str:
    """Fórmula estándar de tint/shade de OOXML sobre un color de tema: tint>0 aclara hacia blanco,
    tint<0 oscurece hacia negro (es lo que hace el selector "más claro/más oscuro %" de Excel)."""
    r, g, b = int(rgb_hex[0:2], 16), int(rgb_hex[2:4], 16), int(rgb_hex[4:6], 16)

    def ajustar(c: int) -> int:
        c = c * (1 + tint) if tint < 0 else c * (1 - tint) + 255 * tint
        return max(0, min(255, round(c)))

    return f"{ajustar(r):02X}{ajustar(g):02X}{ajustar(b):02X}"


def _color_hex(color, tema: list[str] | None = None) -> str | None:
    """RGB de un color de openpyxl en formato "#rrggbb", o None si no hay color que pintar (celda
    sin relleno/fuente por defecto). Resuelve también los colores de TEMA (los de la paleta de
    arriba del selector de Excel, los más usados) si se les pasa `tema` — sin él caen en None."""
    if color is None:
        return None
    if color.type == "rgb" and isinstance(color.rgb, str):
        return None if color.rgb == "00000000" else "#" + color.rgb[-6:]
    if color.type == "indexed":
        try:
            return "#" + COLOR_INDEX[color.indexed][-6:]
        except (IndexError, TypeError):
            return None
    if color.type == "theme" and tema is not None:
        try:
            base = tema[color.theme]
        except (IndexError, TypeError):
            return None
        return "#" + _aplicar_tint(base, color.tint or 0.0)
    return None


def leer_leyenda(wb: Workbook, tema: list[str]) -> dict[str, Leyenda]:
    """Línea -> Leyenda, leído de los rangos con nombre ComiteLinea/ComiteLeyenda. Una línea sin
    Leyenda Comité rellena simplemente no entra en el diccionario."""
    col_linea = _columna_nombrada(wb, "ComiteLinea")
    col_leyenda = _columna_nombrada(wb, "ComiteLeyenda")

    leyenda: dict[str, Leyenda] = {}
    for c_linea, c in zip(col_linea, col_leyenda):
        if not c_linea.value:
            continue
        if c.value is None or str(c.value).strip() == "":
            continue
        leyenda[c_linea.value] = Leyenda(
            texto=str(c.value),
            fondo=(_color_hex(c.fill.fgColor, tema)
                   if c.fill and c.fill.fill_type == "solid" else None),
            color_fuente=_color_hex(c.font.color, tema) if c.font else None,
            negrita=bool(c.font.bold) if c.font else False,
        )
    return leyenda


def leer_cuadrante(wb: Workbook, tema: list[str]) -> tuple[
    list[tuple[str, str, str]], list[date],
    dict[tuple[str, date], str], dict[tuple[str, date], str | None],
    dict[date, dict],
]:
    """(trabajadores, dias, calendario, colores_base, cabecera). Cada trabajador es
    (dni, nombre, grupo) — grupo es el tipo MACRO (fijo/patron/mixto/correturno); ya no se usa para
    separar secciones en el PDF (se pidió quitarlas), se deja por si hace falta más adelante.
    `calendario` guarda el valor CRUDO de cada celda: código de turno, "V" (vacaciones) o "" (libre)
    — la traducción se hace fuera de esta función. `colores_base` es el relleno que YA tiene esa
    celda en el Cuadrante (festivo/finde/laborable, o el amarillo de vacaciones): `salida.py` ya lo
    calculó bien por municipio, así que aquí solo hay que leerlo, no recalcularlo. `cabecera` es el
    texto ("L\\n05/01") y el color de esa misma fila de días, tal cual lo tiene el Cuadrante."""
    titulo = _celda_nombrada(wb, "CuadranteTitulo").value
    m = RE_TITULO.search(str(titulo or ""))
    if not m:
        raise ValueError(f"no se reconocen las fechas en el rango CuadranteTitulo: {titulo!r}")
    inicio = date(*reversed([int(x) for x in m.group(1).split("/")]))
    fin = date(*reversed([int(x) for x in m.group(2).split("/")]))
    dias = []
    d = inicio
    while d <= fin:
        dias.append(d)
        d += timedelta(days=1)

    col_dni = _columna_nombrada(wb, "CuadranteDNI")
    col_nombre = _columna_nombrada(wb, "CuadranteNombre")
    col_grupo = _columna_nombrada(wb, "CuadranteGrupo")
    filas_dias = _resolver_nombre(wb, "CuadranteDias")
    fila_cabecera = _resolver_nombre(wb, "CuadranteCabecera")[0]
    if len(col_dni) != len(filas_dias) or len(filas_dias[0]) != len(dias):
        raise ValueError("CuadranteDNI/CuadranteDias no casan en tamaño con las fechas de "
                          "CuadranteTitulo — ¿el fichero está a medio editar?")

    cabecera: dict[date, dict] = {}
    for d, c in zip(dias, fila_cabecera):
        cabecera[d] = {
            "texto": str(c.value or d.day),
            "fondo": _color_hex(c.fill.fgColor, tema) if c.fill and c.fill.fill_type == "solid"
                     else None,
        }

    trabajadores: list[tuple[str, str, str]] = []
    calendario: dict[tuple[str, date], str] = {}
    colores_base: dict[tuple[str, date], str | None] = {}
    for c_dni, c_nombre, c_grupo, fila_dias in zip(col_dni, col_nombre, col_grupo, filas_dias):
        dni = c_dni.value
        if not dni:
            continue
        trabajadores.append((dni, c_nombre.value or "", c_grupo.value or ""))
        for d, c in zip(dias, fila_dias):
            calendario[(dni, d)] = c.value or ""
            colores_base[(dni, d)] = (_color_hex(c.fill.fgColor, tema)
                                       if c.fill and c.fill.fill_type == "solid" else None)
    return trabajadores, dias, calendario, colores_base, cabecera


def transformar(
    trabajadores: list[tuple[str, str, str]], dias: list[date],
    calendario: dict[tuple[str, date], str], colores_base: dict[tuple[str, date], str | None],
    leyenda: dict[str, Leyenda],
) -> tuple[dict[tuple[str, date], dict], int]:
    """Aplica la leyenda celda a celda. El FONDO por defecto de toda celda es el que ya tenía en el
    Cuadrante (festivo en naranja, fin de semana en rojo claro, laborable en claro, vacaciones en
    amarillo) — la leyenda del comité lo SUSTITUYE solo si el comité le puso su propio color; si solo
    puso texto sin color, el fondo de calendario se mantiene por debajo, tal y como se pidió.
    Devuelve el resultado listo para pintar y cuántas celdas de turno real se quedaron sin leyenda
    (para avisar, no para fallar)."""
    resultado: dict[tuple[str, date], dict] = {}
    sin_leyenda = 0
    for dni, _, _ in trabajadores:
        for d in dias:
            valor = calendario[(dni, d)]
            base = colores_base[(dni, d)]
            if valor == "V":
                # Igual que en salida.py: fuente negra en negrita, encima del amarillo de vacaciones
                # que ya trae `base` (Datos.disponible ya hizo que esa celda sea FILL_VAC_XL).
                resultado[(dni, d)] = {"texto": valor, "fondo": base,
                                        "color_fuente": "#000000", "negrita": True}
            elif valor in leyenda:
                ly = leyenda[valor]
                resultado[(dni, d)] = {"texto": ly.texto, "fondo": ly.fondo or base,
                                        "color_fuente": ly.color_fuente, "negrita": ly.negrita}
            else:
                if valor != "":
                    sin_leyenda += 1
                resultado[(dni, d)] = {"texto": valor, "fondo": base,
                                        "color_fuente": None, "negrita": False}
    return resultado, sin_leyenda


def _estilo(celda: dict) -> str:
    partes = []
    if celda["fondo"]:
        partes.append(f"background:{celda['fondo']}")
    if celda["color_fuente"]:
        partes.append(f"color:{celda['color_fuente']}")
    if celda["negrita"]:
        partes.append("font-weight:bold")
    return ";".join(partes)


def _pagina_mes(
    mes_idx: int, dias_mes: list[date], trabajadores: list[tuple[str, str, str]],
    resultado: dict[tuple[str, date], dict], cabecera: dict[date, dict], primera: bool,
) -> str:
    """Una página (grande, pensada para zoom) con TODOS los trabajadores del mes `mes_idx` seguidos,
    sin separarlos en secciones por tipo. La cabecera de cada día lleva el mismo texto (día de la
    semana + fecha) y el mismo color de categoría que ya tiene el Cuadrante — nada calculado aquí."""
    cab_dias = "".join(
        f'<th style="{("background:" + cabecera[d]["fondo"]) if cabecera[d]["fondo"] else ""}">'
        f'{cabecera[d]["texto"].replace(chr(10), "<br>")}</th>'
        for d in dias_mes)
    filas = []
    for i, (dni, nombre, _grupo) in enumerate(trabajadores):
        zebra = " zebra" if i % 2 else ""
        celdas = "".join(
            f'<td style="{_estilo(resultado[(dni, d)])}">{resultado[(dni, d)]["texto"]}</td>'
            for d in dias_mes)
        filas.append(f'<tr class="fila{zebra}"><td class="dni">{dni}</td>'
                      f'<td class="nombre">{nombre}</td>{celdas}</tr>')
    salto = "" if primera else ' style="break-before: page"'
    return f"""<div{salto}>
<h1>Cuadrante de turnos</h1>
<p class="periodo">{MESES[mes_idx - 1]} {dias_mes[0].year}</p>
<table>
<colgroup><col class="dni"><col class="nombre">{"<col>" * NDIAS_MAX}</colgroup>
<thead><tr><th>DNI</th><th>Nombre</th>{cab_dias}</tr></thead>
<tbody>{"".join(filas)}</tbody>
</table>
</div>"""


def generar_html(
    trabajadores: list[tuple[str, str, str]], dias: list[date], resultado: dict[tuple[str, date], dict],
    cabecera: dict[date, dict],
) -> str:
    """Un documento, una página grande por mes (apaisada), pensada para leerse haciendo zoom en
    pantalla — no para imprimir a tamaño real."""
    meses_presentes = sorted({d.month for d in dias})
    paginas = [
        _pagina_mes(mes, [d for d in dias if d.month == mes], trabajadores, resultado, cabecera,
                    i == 0)
        for i, mes in enumerate(meses_presentes)
    ]
    ancho_mm = ANCHO_DNI_MM + ANCHO_NOMBRE_MM + NDIAS_MAX * ANCHO_COL_DIA_MM
    # Colchón de seguridad para la cabecera (dos líneas: día de la semana + fecha) y el título.
    alto_mm = 30 + (len(trabajadores) + 1) * ALTO_FILA_MM + 8

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Cuadrante — comité</title>
<style>
  @page {{ size: {ancho_mm}mm {alto_mm}mm; margin: 8mm; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: "Inter", sans-serif; font-variant-numeric: tabular-nums;
          color: {COLOR_TINTA}; background: {COLOR_PAPEL}; }}
  h1 {{ font-size: 12pt; font-weight: 600; margin: 0 0 1mm 0; }}
  .periodo {{ font-size: 7.5pt; color: #5A6472; margin: 0 0 2.5mm 0; }}
  table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
  th, td {{ border: 0.75pt solid {COLOR_LINEA}; padding: 0.6mm 1.2mm; font-size: 6.8pt;
            text-align: center; height: {ALTO_FILA_MM}mm; overflow: hidden;
            white-space: nowrap; text-overflow: ellipsis; }}
  col.dni {{ width: {ANCHO_DNI_MM}mm; }}
  col.nombre {{ width: {ANCHO_NOMBRE_MM}mm; }}
  td.dni, td.nombre {{ text-align: left; }}
  td.nombre {{ color: #5A6472; }}
  thead th {{ background: {COLOR_PAPEL}; color: {COLOR_TINTA}; font-weight: 600; height: auto; }}
  tr.zebra td {{ background: {COLOR_ZEBRA}; }}
</style>
</head>
<body>
{"".join(paginas)}
</body>
</html>"""


def exportar(ruta_entrada: Path, ruta_salida_pdf: Path) -> Path:
    ruta_salida_pdf = ruta_salida_pdf.resolve()
    tema = _tema_colores(ruta_entrada)
    wb = load_workbook(ruta_entrada)
    leyenda = leer_leyenda(wb, tema)
    trabajadores, dias, calendario, colores_base, cabecera = leer_cuadrante(wb, tema)
    resultado, sin_leyenda = transformar(trabajadores, dias, calendario, colores_base, leyenda)
    html = generar_html(trabajadores, dias, resultado, cabecera)
    print(f"({len(trabajadores)} trabajadores, {len(dias)} días, "
          f"{len(leyenda)} líneas con leyenda, {sin_leyenda} celdas de turno sin leyenda todavía)")

    # Chromium (vía Playwright) en vez de WeasyPrint: en Windows, WeasyPrint depende de librerías
    # nativas (Pango/Cairo) que hay que instalar aparte con MSYS2 y no tienen soporte oficial en
    # Windows Server. Nada de fichero HTML intermedio: `set_content` carga el HTML directamente en
    # la página, sin pasar por disco.
    from playwright.sync_api import sync_playwright
    ruta_salida_pdf.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # Usa el Chrome o Edge que YA esté instalado en la máquina (ambos son Chromium por debajo,
        # mismo resultado) en vez de que Playwright se descargue el suyo propio — así no hace falta
        # `playwright install chromium` (~150 MB) en la máquina de destino. Solo si no hay ninguno
        # de los dos cae en el Chromium propio de Playwright, que si acaso sí habría que instalar.
        navegador = None
        for canal in ("chrome", "msedge"):
            try:
                navegador = p.chromium.launch(channel=canal)
                break
            except Exception:
                continue
        if navegador is None:
            navegador = p.chromium.launch()
        pagina = navegador.new_page()
        pagina.set_content(html)
        # print_background: si no, Chrome omite los colores de fondo al "imprimir" a PDF.
        # prefer_css_page_size: que respete el @page (tamaño/orientación) del propio HTML.
        pagina.pdf(path=str(ruta_salida_pdf), print_background=True, prefer_css_page_size=True)
        navegador.close()
    print(f"{ruta_salida_pdf.relative_to(RAIZ)}")
    return ruta_salida_pdf


def main() -> int:
    p = argparse.ArgumentParser(description="Traduce el cuadrante a PDF con la leyenda del comité")
    p.add_argument("entrada", nargs="?", default=str(SALIDA / "calendario.xlsx"))
    p.add_argument("salida", nargs="?", default=str(SALIDA / "calendario_comite.pdf"))
    a = p.parse_args()
    exportar(Path(a.entrada), Path(a.salida))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

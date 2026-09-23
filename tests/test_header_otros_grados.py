# Copyright (C) 2026 Juvesi76
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Regresiones de `header.py`/`legend.py` encontradas con PDFs de otros
grados de la ESI (no el de Informática de referencia).

Se saltan si el PDF correspondiente no está presente (fixtures
exploratorias, no el fixture obligatorio del proyecto — ver
`tests/conftest.py`).
"""
from pathlib import Path

import pymupdf
import pytest

from horario_uca.extract import read_page
from horario_uca.parse import parse_page
from horario_uca.parse.legend import parse_legend

DATA_DIR = Path(__file__).parent.parent / "data"


def _page(pdf_name: str, page_index: int):
    pdf_path = DATA_DIR / pdf_name
    if not pdf_path.exists():
        pytest.skip(f"fixture exploratoria no presente: {pdf_name}")
    doc = pymupdf.open(pdf_path)
    return read_page(doc, page_index)


def test_doble_grado_sin_itinerario_no_confunde_la_segunda_linea_del_titulo_con_uno():
    """`_TITLE_PREFIX` (singular) no reconocía "Doble grado en
    Ingeniería..." — corregido con `_TITLE_PREFIXES`. Pero corregir SOLO
    eso habría introducido un fallo distinto: en esta página el título en
    sí ocupa dos líneas ("Doble grado en Ingeniería Eléctrica e" +
    "Ingeniería Electrónica Industrial", MISMO tamaño de fuente en las
    dos) y la página no tiene ningún itinerario real — sin discriminar por
    tamaño de fuente, la segunda línea del título se leía como si fuera el
    itinerario."""
    raw = _page("GIE-GIEI.horario2627.pdf", 4)
    page = parse_page(raw)
    assert page.curso == "3º"
    assert page.semestre == 1
    assert page.itinerario is None


def test_doble_grado_con_titulo_de_dos_lineas_y_itinerario_real():
    """Misma familia de título partido en dos líneas que el caso anterior,
    pero esta página SÍ tiene un itinerario real de tamaño de fuente menor
    justo debajo — debe leerse él, no la segunda línea del título."""
    raw = _page("GIE-GIEI.horario2627.pdf", 12)
    page = parse_page(raw)
    assert page.itinerario == "Optativas de Ingeniería Electrónica Industrial"


def test_doble_grado_con_titulo_de_tres_lineas_encadena_hasta_el_itinerario_real():
    """El título más largo visto en cualquier PDF comprobado ("Doble grado
    en Ingeniería Mecánica e Ingeniería en Diseño Industrial y Desarrollo
    del Producto") envuelve a DOS líneas de continuación (misma fuente que
    el título) antes de llegar al itinerario real, una fuente más pequeña
    — la cadena de fusión tiene que atravesar las dos, no solo una."""
    raw = _page("GIM-GIDIDP.horario2627.pdf", 10)
    page = parse_page(raw)
    assert page.itinerario == "Optativas del perfil multidisciplinar"


def test_asignatura_partida_en_dos_spans_horizontales_en_la_leyenda():
    """`GCV - Gestión del Ciclo de Vida del Producto.` y
    `PLM-PDM - 21717037` son dos spans PyMuPDF distintos de la MISMA línea
    de leyenda (split horizontal, no vertical) — sin fusionarlos, `GCV`
    desaparecía de la leyenda en silencio (ninguno de los dos fragmentos
    matchea `LEGEND_RE` por separado). Fija también que la fusión no deje
    un doble espacio: hay un span vacío/solo-espacio de PyMuPDF exactamente
    en el hueco entre los dos fragmentos reales."""
    raw = _page("GIDIDP.horario2627.pdf", 7)
    entries = parse_legend(raw)
    gcv = [e for e in entries if e.acronym == "GCV"]
    assert len(gcv) == 1
    assert gcv[0].name == "Gestión del Ciclo de Vida del Producto. PLM-PDM"
    assert gcv[0].code == "21717037"

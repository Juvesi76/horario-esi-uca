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

"""Regresión del 4º bug de "tipografía como constante absoluta" en
calendar.py, encontrado con PDFs de otros grados de la ESI (no el de
Informática de referencia): toda tolerancia geométrica derivada de la
tipografía debe fijarse como ratio, nunca en puntos absolutos.

`_MARKER_ROW_Y_TOLERANCE_RATIO` (antes fijo en `1.5` puntos) es la
tolerancia vertical entre la fila de días y su marcador de semana azul.
En una tipografía ~1.8x más grande que GII, el valor fijo dejaba TODAS
las filas de la página sin marcador — 0 `CalendarWeek`, bloques reales
sin ninguna fecha posible, sin ninguna excepción. Este test fija los dos
casos reales que lo demostraron.

Se salta si el PDF no está presente (son fixtures exploratorias, no el
fixture obligatorio del proyecto — ver `tests/conftest.py`).
"""
from pathlib import Path

import pymupdf
import pytest

from horario_uca.extract import read_page
from horario_uca.parse import parse_page

DATA_DIR = Path(__file__).parent.parent / "data"

CASOS = [
    ("GIE-GIEI.horario2627.pdf", 12),
    ("GITI.horario2627.pdf", 17),
]


@pytest.mark.parametrize("pdf_name,page_index", CASOS)
def test_pagina_con_tipografia_grande_no_pierde_su_calendario(pdf_name, page_index):
    pdf_path = DATA_DIR / pdf_name
    if not pdf_path.exists():
        pytest.skip(f"fixture exploratoria no presente: {pdf_name}")

    doc = pymupdf.open(pdf_path)
    raw = read_page(doc, page_index)
    page = parse_page(raw)

    assert len(page.blocks) >= 1, "la página debe tener bloques reales (TSCP)"
    assert len(page.calendar) in (15, 16), f"calendario vacío o incompleto: {len(page.calendar)} semanas"
    codes = {w.code for w in page.warnings}
    assert "calendario_vacio" not in codes
    assert "bloques_sin_calendario_para_fechar" not in codes

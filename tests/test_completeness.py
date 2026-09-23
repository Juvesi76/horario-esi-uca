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

"""[3] Regla estructural: una colección vacía no es "nada que reportar".

Se coló en silencio tres veces durante la Fase 2 (unparsed nunca poblado,
notes.py sin conectar, calendar.py vacío en 13/14 páginas de semestre 2) antes
de escribirse como regla explícita. Este test habría cazado los tres casos en
el momento de introducirse: recorre las 24 páginas y falla si alguna de las
colecciones mínimas esperadas por cada módulo sale vacía o corta.
"""
import pymupdf

from horario_uca.extract import read_page
from horario_uca.parse import parse_page

MIN_LEGEND_ENTRIES = 4


def test_all_pages_meet_minimum_expectations(sample_pdf_path):
    doc = pymupdf.open(sample_pdf_path)
    failures = []

    for page_index in range(doc.page_count):
        raw = read_page(doc, page_index)
        page = parse_page(raw)

        if len(page.legend) < MIN_LEGEND_ENTRIES:
            failures.append(f"page {page_index}: leyenda con {len(page.legend)} asignaturas (< {MIN_LEGEND_ENTRIES})")
        if len(page.blocks) < 1:
            failures.append(f"page {page_index}: 0 ClassBlock")
        if len(page.notes) < 1:
            failures.append(f"page {page_index}: 0 notas al pie")

        expected_weeks = {1: 16, 2: 15}.get(page.semestre)
        if expected_weeks is not None and len(page.calendar) != expected_weeks:
            failures.append(
                f"page {page_index}: {len(page.calendar)} CalendarWeek, se esperaban {expected_weeks} "
                f"(semestre {page.semestre})"
            )

    assert not failures, "\n" + "\n".join(failures)


def test_all_pages_emit_completeness_warning_codes_when_expected(sample_pdf_path):
    """Confirma que la regla está realmente conectada (no solo que las 24
    páginas del PDF de referencia pasan por casualidad): las 24 páginas
    cumplen hoy, así que ningún ParseWarning de estos 4 códigos debe
    aparecer. Si algún día una página deja de cumplir, debe aparecer aquí
    con su código propio, no como una colección vacía sin explicar."""
    doc = pymupdf.open(sample_pdf_path)
    completeness_codes = {"calendario_vacio", "notas_vacias", "leyenda_vacia", "pagina_sin_bloques"}

    for page_index in range(doc.page_count):
        raw = read_page(doc, page_index)
        page = parse_page(raw)
        fired = {w.code for w in page.warnings} & completeness_codes
        assert not fired, f"page {page_index}: {fired}"

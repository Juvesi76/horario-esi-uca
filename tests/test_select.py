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

"""Fase 4: select/filter.py — validación accionable de SubjectSelection."""
import pymupdf

from horario_uca.extract import read_page
from horario_uca.model import SubjectSelection
from horario_uca.parse import parse_page
from horario_uca.select import filter_events, validate_selection


def _all_pages(sample_pdf_path):
    doc = pymupdf.open(sample_pdf_path)
    return [parse_page(read_page(doc, i)) for i in range(doc.page_count)]


def test_seleccion_valida_sin_warnings(sample_pdf_path):
    pages = _all_pages(sample_pdf_path)
    selection = SubjectSelection(acronym="MD", curso="1ºA", groups=["A1", "B1"])
    assert validate_selection(selection, pages) == []


def test_asignatura_no_existe_en_curso(sample_pdf_path):
    pages = _all_pages(sample_pdf_path)
    selection = SubjectSelection(acronym="NOEXISTE", curso="1ºA", groups=["A1"])
    warnings = validate_selection(selection, pages)
    assert len(warnings) == 1
    assert warnings[0].code == "asignatura_no_existe_en_curso"


def test_grupo_no_existe_lista_disponibles(sample_pdf_path):
    pages = _all_pages(sample_pdf_path)
    selection = SubjectSelection(acronym="MD", curso="1ºA", groups=["A1", "Z9"])
    warnings = validate_selection(selection, pages)
    grupo_warnings = [w for w in warnings if w.code == "grupo_no_existe"]
    assert len(grupo_warnings) == 1
    assert "Z9" in grupo_warnings[0].message
    assert "A1" in grupo_warnings[0].message  # los disponibles listados incluyen el que sí existía


def test_letra_con_docencia_sin_grupo_elegido(sample_pdf_path):
    """MD tiene A (teoría) y B (problemas) reales en 1ºA — elegir solo A debe
    avisar de que hay B con docencia real no elegida."""
    pages = _all_pages(sample_pdf_path)
    selection = SubjectSelection(acronym="MD", curso="1ºA", groups=["A1"])
    warnings = validate_selection(selection, pages)
    codes = {w.code for w in warnings}
    assert "letra_con_docencia_sin_grupo_elegido" in codes
    letra_b = next(w for w in warnings if w.code == "letra_con_docencia_sin_grupo_elegido")
    assert "'B'" in letra_b.message


def test_itinerario_ambiguo_sintetico(sample_pdf_path):
    """No existe ambigüedad real de itinerario en este PDF (comprobado: 0
    acrónimos compartidos entre itinerarios de un mismo curso) — se fabrica
    el caso copiando una página real con un itinerario distinto."""
    pages = _all_pages(sample_pdf_path)
    p11 = next(p for p in pages if p.itinerario == "Itinerario de Ingeniería del Software" and p.semestre == 2)
    p12 = next(p for p in pages if p.itinerario == "Itinerario de Sistemas de Información" and p.semestre == 2)

    acronym = p11.blocks[0].subject_acronym
    p12_fake = p12.model_copy(
        update={"curso": p11.curso, "blocks": [p12.blocks[0].model_copy(update={"subject_acronym": acronym})]}
    )

    selection = SubjectSelection(acronym=acronym, curso=p11.curso, groups=["A1"])
    warnings = validate_selection(selection, [p11, p12_fake])
    assert any(w.code == "itinerario_ambiguo" for w in warnings)


def test_filter_events_selection_md_cal_1A(sample_pdf_path):
    """La selección ya verificada evento a evento en la revisión anterior:
    0 warnings de validación, 82 eventos filtrados."""
    from horario_uca.resolve import resolve_events

    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 0)
    page = parse_page(raw)
    pages = _all_pages(sample_pdf_path)

    selections = [
        SubjectSelection(acronym="MD", curso="1ºA", groups=["A1", "B1"]),
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1", "B3", "C1"]),
    ]
    for s in selections:
        assert validate_selection(s, pages) == [], s.acronym

    events, warnings = resolve_events(page)
    assert warnings == []
    filtered = filter_events(events, selections)
    assert len(filtered) == 82

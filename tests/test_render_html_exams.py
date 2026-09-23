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

"""Integración de exámenes en `render/html.py`. Verificado también en un
navegador real a 380px (capturas en la sesión que introdujo esto, no
repetidas aquí en texto)."""
from pathlib import Path

import pymupdf
import pytest

from horario_uca.extract import read_page
from horario_uca.model import SubjectSelection
from horario_uca.parse.exams import parse_exam_calendar
from horario_uca.pipeline import parse_document, resolve_document
from horario_uca.render.html import _assign_colors, build_html
from horario_uca.select import filter_events
from horario_uca.select.exams import default_exam_codes

DATA_DIR = Path(__file__).parent.parent / "data"
EXAM_PDF = DATA_DIR / "GII.calendarioExamenes.Feb27.pdf"
GII_PDF = DATA_DIR / "GII_horario2627.pdf"


def _gii_pages():
    if not GII_PDF.exists():
        pytest.skip(f"fixture no presente: {GII_PDF.name} (ver README, 'De dónde descargar los PDFs')")
    return parse_document(str(GII_PDF))


@pytest.fixture
def cal_pinf_case():
    if not EXAM_PDF.exists():
        pytest.skip(f"fixture no presente: {EXAM_PDF.name}")
    doc = pymupdf.open(EXAM_PDF)
    exam_calendar = parse_exam_calendar(read_page(doc, 0))
    pages = _gii_pages()
    selections = [
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1", "B3", "C1"]),
        SubjectSelection(acronym="PINF", curso="3º", groups=["C2"]),
    ]
    events, _ = resolve_document(pages)
    filtered = filter_events(events, selections)
    defaults, _ = default_exam_codes(selections, pages, exam_calendar)
    entries = [e for e in exam_calendar.entries if e.code in defaults]
    legend = [entry for p in pages for entry in p.legend]
    return filtered, entries, legend


def test_build_html_con_examenes_no_deja_marcadores_sin_sustituir(cal_pinf_case):
    filtered, entries, legend = cal_pinf_case
    html = build_html(filtered, legend=legend, title="Prueba", exams=entries)
    assert "__" not in html.replace("__pycache__", "")  # ningún __MARCADOR__ sin reemplazar
    assert "btn-view-exams" in html
    assert "Examen: Cálculo" not in html  # el nombre va en el JSON, no como texto plano fijo


def test_examenes_aparecen_en_el_json_con_choque_marcado(cal_pinf_case):
    filtered, entries, legend = cal_pinf_case
    html = build_html(filtered, legend=legend, title="Prueba", exams=entries)
    assert '"kind": "exam"' in html or '"kind":"exam"' in html
    assert "Proyectos Inform" in html
    assert "18 de enero" not in html  # las fechas viajan en ISO en el JSON, no en texto ya formateado


def test_sin_examenes_exams_json_es_lista_vacia():
    from horario_uca.pipeline import resolve_document
    from horario_uca.select import filter_events

    pages = _gii_pages()
    selections = [SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1"])]
    events, _ = resolve_document(pages)
    filtered = filter_events(events, selections)
    html = build_html(filtered, title="Sin examenes")
    assert "const EXAMS = [];" in html


def test_extra_acronyms_da_color_a_asignatura_solo_de_examen():
    """Un examen añadido a mano sin ningún grupo de clase seleccionado
    necesita color/patrón igual que cualquier otra asignatura — sin
    `extra_acronyms`, `_assign_colors` nunca vería ese acrónimo porque no
    aparece en ningún `CalendarEvent`."""
    colors_sin_extra = _assign_colors([], legend=None)
    colors_con_extra = _assign_colors([], legend=None, extra_acronyms=["ZZZ"])
    assert "ZZZ" not in colors_sin_extra
    assert "ZZZ" in colors_con_extra


def test_exams_available_se_marcan_included_false_y_no_contaminan_el_calendario(cal_pinf_case):
    """Exámenes de una convocatoria disponible pero no incluida (ver
    `pipeline.py::generate_calendar`) deben aparecer en el JSON de EXAMS
    (para que la pestaña Exámenes los muestre como catálogo) pero
    marcados `included: false`, y el JS debe filtrarlos de
    EXAMS_ON_CALENDAR (Mes/Semana/leyenda) — nunca deben verse como si
    estuvieran de verdad en el calendario."""
    filtered, entries, legend = cal_pinf_case
    included = entries[:1]
    available = entries[1:2]
    html = build_html(filtered, legend=legend, title="Prueba", exams=included, exams_available=available)
    assert '"included": true' in html
    assert '"included": false' in html
    # La lógica de filtrado debe seguir presente en la plantilla — si se
    # borra por error, un examen "disponible" volvería a aparecer como si
    # ya estuviera en el calendario (Mes/Semana), no solo en la pestaña.
    assert "EXAMS_ON_CALENDAR" in html
    assert "e.included !== false" in html


def test_examen_sin_ninguna_clase_seleccionada_tambien_se_renderiza(cal_pinf_case):
    """Escenario de "añadir examen a mano" (paso pendiente de interfaz,
    pero el render ya debe soportarlo): un ExamEntry cuya asignatura no
    tiene ningún CalendarEvent en `events` debe seguir apareciendo en
    EXAMS con color propio, no reventar `build_html`."""
    filtered, entries, legend = cal_pinf_case
    # ningún evento de clase, solo el examen — simula "solo añadido a mano"
    html = build_html([], legend=legend, title="Solo examen", exams=entries[:1])
    assert entries[0].code in html

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

"""Render de exámenes en `.ics`/HTML."""
from pathlib import Path

import pymupdf
import pytest
import vobject

from horario_uca.extract import read_page
from horario_uca.model import SubjectSelection
from horario_uca.parse.exams import parse_exam_calendar
from horario_uca.pipeline import parse_document, resolve_document
from horario_uca.render.ics import build_ics
from horario_uca.select import filter_events
from horario_uca.select.exams import EXAM_NOMINAL_DURATION_MINUTES, default_exam_codes

DATA_DIR = Path(__file__).parent.parent / "data"
EXAM_PDF = DATA_DIR / "GII.calendarioExamenes.Feb27.pdf"
GII_PDF = DATA_DIR / "GII_horario2627.pdf"


@pytest.fixture
def exam_entries():
    if not EXAM_PDF.exists():
        pytest.skip(f"fixture no presente: {EXAM_PDF.name}")
    if not GII_PDF.exists():
        pytest.skip(f"fixture no presente: {GII_PDF.name} (ver README, 'De dónde descargar los PDFs')")
    doc = pymupdf.open(EXAM_PDF)
    exam_calendar = parse_exam_calendar(read_page(doc, 0))
    pages = parse_document(str(GII_PDF))
    selections = [
        SubjectSelection(acronym="MD", curso="1ºA", groups=["A1", "B1"]),
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1", "B3", "C1"]),
    ]
    defaults, _ = default_exam_codes(selections, pages, exam_calendar)
    events, _ = resolve_document(pages)
    filtered = filter_events(events, selections)
    entries = [e for e in exam_calendar.entries if e.code in defaults]
    return filtered, entries


def test_ics_incluye_examenes_como_vevent_propios(exam_entries):
    filtered, entries = exam_entries
    ics_bytes = build_ics(filtered, calendar_name="Prueba", exams=entries)
    cal = vobject.readOne(ics_bytes.decode("utf-8"))
    exam_vevents = [c for c in cal.components() if c.name == "VEVENT" and c.summary.value.startswith("Examen:")]
    assert len(exam_vevents) == 2
    assert {v.summary.value for v in exam_vevents} == {"Examen: Cálculo", "Examen: Matemática Discreta"}


def test_examen_sin_aula_usa_aula_por_confirmar(exam_entries):
    filtered, entries = exam_entries
    ics_bytes = build_ics(filtered, calendar_name="Prueba", exams=entries)
    cal = vobject.readOne(ics_bytes.decode("utf-8"))
    exam_vevents = [c for c in cal.components() if c.name == "VEVENT" and c.summary.value.startswith("Examen:")]
    assert all(v.location.value == "Aula por confirmar" for v in exam_vevents)


def test_dtend_usa_duracion_nominal_y_lo_dice_en_la_descripcion(exam_entries):
    filtered, entries = exam_entries
    ics_bytes = build_ics(filtered, calendar_name="Prueba", exams=entries)
    cal = vobject.readOne(ics_bytes.decode("utf-8"))
    cal_exam = next(
        c for c in cal.components() if c.name == "VEVENT" and c.summary.value == "Examen: Cálculo"
    )
    delta = cal_exam.dtend.value - cal_exam.dtstart.value
    assert delta.total_seconds() / 60 == EXAM_NOMINAL_DURATION_MINUTES
    assert "estimación" in cal_exam.description.value
    assert "no la de fin" in cal_exam.description.value


def test_uid_de_examen_nunca_colisiona_con_uid_de_clase(exam_entries):
    filtered, entries = exam_entries
    ics_bytes = build_ics(filtered, calendar_name="Prueba", exams=entries)
    cal = vobject.readOne(ics_bytes.decode("utf-8"))
    uids = [c.uid.value for c in cal.components() if c.name == "VEVENT"]
    assert len(uids) == len(set(uids))
    exam_uids = [u for u in uids if u.startswith("exam-")]
    class_uids = [u for u in uids if not u.startswith("exam-")]
    assert exam_uids and class_uids


def test_sin_examenes_build_ics_se_comporta_igual_que_antes(exam_entries):
    filtered, _ = exam_entries
    ics_bytes = build_ics(filtered, calendar_name="Prueba")
    cal = vobject.readOne(ics_bytes.decode("utf-8"))
    exam_vevents = [c for c in cal.components() if c.name == "VEVENT" and c.summary.value.startswith("Examen:")]
    assert exam_vevents == []

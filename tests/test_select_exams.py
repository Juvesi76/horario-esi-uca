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

"""Fase 4 de exámenes: reglas de inclusión por defecto y los dos tipos de
choque propios (examen↔examen, examen↔clase) — ver `select/exams.py`.
"""
from pathlib import Path

import pymupdf
import pytest

from horario_uca.extract import read_page
from horario_uca.model import SubjectSelection
from horario_uca.parse.exams import parse_exam_calendar
from horario_uca.pipeline import parse_document
from horario_uca.select.exams import default_exam_codes, find_exam_class_conflicts, find_exam_conflicts

DATA_DIR = Path(__file__).parent.parent / "data"
EXAM_PDF = DATA_DIR / "GII.calendarioExamenes.Feb27.pdf"
GII_PDF = DATA_DIR / "GII_horario2627.pdf"


@pytest.fixture
def exam_calendar():
    if not EXAM_PDF.exists():
        pytest.skip(f"fixture no presente: {EXAM_PDF.name}")
    doc = pymupdf.open(EXAM_PDF)
    return parse_exam_calendar(read_page(doc, 0))


@pytest.fixture
def gii_pages():
    if not GII_PDF.exists():
        pytest.skip(f"fixture no presente: {GII_PDF.name} (ver README, 'De dónde descargar los PDFs')")
    return parse_document(str(GII_PDF))


def test_seleccion_md_cal_anade_exactamente_dos_examenes_de_semestre_1_sin_choques(exam_calendar, gii_pages):
    selections = [
        SubjectSelection(acronym="MD", curso="1ºA", groups=["A1", "B1"]),
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1", "B3", "C1"]),
    ]
    defaults, infos = default_exam_codes(selections, gii_pages, exam_calendar)
    entries_by_code = {e.code: e for e in exam_calendar.entries}

    assert len(defaults) == 2
    assert entries_by_code[list(defaults)[0]].acronym in ("MD", "CAL")
    dates = {entries_by_code[c].acronym: entries_by_code[c].date for c in defaults}
    assert dates == {"CAL": "2027-01-18", "MD": "2027-02-02"}
    assert not any(i.code == "examen_semestre_2_no_automatico" for i in infos)

    default_entries = [entries_by_code[c] for c in defaults]
    assert find_exam_conflicts(default_entries) == []


def test_asignatura_de_semestre_2_no_anade_examen_por_defecto(exam_calendar, gii_pages):
    """ALG (Álgebra) es de semestre 2 — su examen de esta convocatoria de
    febrero es para quien ya la cursó, nunca automático aunque el alumno
    tenga clases seleccionadas."""
    selections = [SubjectSelection(acronym="ALG", curso="1ºA", groups=["A1"])]
    defaults, infos = default_exam_codes(selections, gii_pages, exam_calendar)

    assert defaults == set()
    assert any(
        i.code == "examen_semestre_2_no_automatico" and "ALG" in i.message for i in infos
    )


def test_convocatoria_sin_regla_verificada_no_anade_nada(gii_pages):
    from horario_uca.model import ExamCalendar, ExamEntry

    junio = ExamCalendar(
        convocatoria="JUNIO DE 2027",
        grado="GRADO EN INGENIERÍA INFORMÁTICA",
        sections=["Asignaturas propias del título"],
        entries=[
            ExamEntry(
                code="21714009", name="Cálculo", acronym="CAL", curso=1, semestre=1,
                date="2027-06-15", start_time="16:00", section="Asignaturas propias del título",
            )
        ],
    )
    selections = [SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1"])]
    defaults, infos = default_exam_codes(selections, gii_pages, junio)
    assert defaults == set()
    assert any(i.code == "regla_convocatoria_no_verificada" for i in infos)


def test_choque_real_cal_pinf_18_enero(exam_calendar):
    """CAL (1º) y PINF (3º) coinciden el 18/01/2027 a las 16:00 — fixture
    de regresión real."""
    entries = [e for e in exam_calendar.entries if e.acronym in ("CAL", "PINF")]
    conflicts = find_exam_conflicts(entries)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.date == "2027-01-18"
    assert c.certain is True
    assert {c.exam_a.acronym, c.exam_b.acronym} == {"CAL", "PINF"}


def test_choque_de_dia_igual_hora_distinta_es_posible_no_seguro():
    from horario_uca.model import ExamEntry

    a = ExamEntry(code="00000001", name="A", acronym="A", curso=1, semestre=1, date="2027-01-18", start_time="09:00", section="s")
    b = ExamEntry(code="00000002", name="B", acronym="B", curso=1, semestre=1, date="2027-01-18", start_time="16:00", section="s")
    conflicts = find_exam_conflicts([a, b])
    assert len(conflicts) == 1
    assert conflicts[0].certain is False


def test_examen_no_choca_consigo_mismo_ni_entre_dias_distintos(exam_calendar):
    conflicts = find_exam_conflicts(exam_calendar.entries)
    for c in conflicts:
        assert c.exam_a.code != c.exam_b.code
        assert c.exam_a.date == c.date == c.exam_b.date


def test_ningun_choque_examen_clase_en_esta_convocatoria(exam_calendar, gii_pages):
    """Verificado real: las clases de semestre 1 acaban el 12/01/2027 y las
    de semestre 2 empiezan en febrero — ningún examen de esta convocatoria
    (todos entre el 18/01 y el 02/02) debería chocar con una clase real de
    1ºA."""
    from horario_uca.pipeline import resolve_document

    events, _ = resolve_document([p for p in gii_pages if p.curso == "1ºA"])
    conflicts = find_exam_class_conflicts(exam_calendar.entries, events)
    assert conflicts == []

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

"""Tests del calendario de exámenes — ver `parse/exams.py`. Fixture de referencia:
`data/GII.calendarioExamenes.Feb27.pdf` (convocatoria de febrero 2027,
GII), verificado: 65 filas (1º:10, 2º:10, 3º:25, 4º:20), todas a 16:00,
0 ParseWarning.
"""
from collections import Counter
from pathlib import Path

import pymupdf
import pytest

from horario_uca.extract import read_page
from horario_uca.parse.exams import cross_check_exam_codes, parse_exam_calendar
from horario_uca.pipeline import parse_document

DATA_DIR = Path(__file__).parent.parent / "data"
EXAM_PDF = DATA_DIR / "GII.calendarioExamenes.Feb27.pdf"
GII_PDF = DATA_DIR / "GII_horario2627.pdf"


def _gii_pages():
    if not GII_PDF.exists():
        pytest.skip(f"fixture no presente: {GII_PDF.name} (ver README, 'De dónde descargar los PDFs')")
    return parse_document(str(GII_PDF))


@pytest.fixture
def exam_calendar():
    if not EXAM_PDF.exists():
        pytest.skip(f"fixture no presente: {EXAM_PDF.name}")
    doc = pymupdf.open(EXAM_PDF)
    raw = read_page(doc, 0)
    return parse_exam_calendar(raw)


def test_cabecera_de_convocatoria(exam_calendar):
    assert exam_calendar.convocatoria == "FEBRERO DE 2027"
    assert exam_calendar.grado == "GRADO EN INGENIERÍA INFORMÁTICA"
    assert exam_calendar.sections == ["Asignaturas propias del título"]


def test_65_filas_sin_avisos(exam_calendar):
    assert len(exam_calendar.entries) == 65
    assert exam_calendar.warnings == []


def test_distribucion_por_curso(exam_calendar):
    assert Counter(e.curso for e in exam_calendar.entries) == {1: 10, 2: 10, 3: 25, 4: 20}


def test_todas_las_horas_son_16_00(exam_calendar):
    assert {e.start_time for e in exam_calendar.entries} == {"16:00"}


def test_ninguna_entrada_trae_aula(exam_calendar):
    assert all(e.room is None for e in exam_calendar.entries)


def test_alg_es_de_semestre_2_y_su_examen_es_antes_de_empezar_a_cursarla(exam_calendar):
    """ÁLG (Álgebra) es de semestre 2 (clases feb-may 2027) pero su examen
    de esta convocatoria de FEBRERO es el 19/01/2027 — antes de la primera
    clase. Confirma la regla de dominio: la convocatoria de febrero de una
    asignatura de semestre 2 es para quien YA la cursó, no para quien
    empieza a cursarla ahora."""
    alg = [e for e in exam_calendar.entries if e.acronym == "ALG"]
    assert len(alg) == 1
    assert alg[0].semestre == 2
    assert alg[0].date == "2027-01-19"


def test_cal_examen_real_fijado_como_fixture(exam_calendar):
    cal = [e for e in exam_calendar.entries if e.acronym == "CAL"]
    assert len(cal) == 1
    assert cal[0].date == "2027-01-18"
    assert cal[0].semestre == 1


def test_cruce_de_codigos_contra_el_horario_gii(exam_calendar):
    """`ApC` (Aprendizaje Computacional, 21714028) SÍ tiene clases reales en
    el horario (4º, Itinerario de Computación: A1 teoría + C1 prácticas) —
    la primera versión de este test daba por buena una discrepancia sin
    investigarla ("probablemente optativa no ofertada") cuando en realidad
    `LEGEND_RE` perdía la entrada por ser el único acrónimo del documento
    con una minúscula (`[A-Z\\-]+` no la admitía). Tras el arreglo
    (`legend.py`, acrónimo = cualquier secuencia sin espacios), el único
    código sin pareja debe ser TFG — clases sin examen, sí legítimo (un TFG
    no se examina así)."""
    pages = _gii_pages()
    infos = cross_check_exam_codes(exam_calendar, pages)
    by_code = {"examen_sin_clases_en_horario": [], "clases_sin_examen_en_convocatoria": []}
    for i in infos:
        by_code[i.code].append(i.message)

    assert by_code["examen_sin_clases_en_horario"] == []
    assert len(by_code["clases_sin_examen_en_convocatoria"]) == 1
    assert "21714064" in by_code["clases_sin_examen_en_convocatoria"][0]
    assert "TFG" in by_code["clases_sin_examen_en_convocatoria"][0]


def test_apc_aparece_en_la_leyenda_con_su_minuscula():
    """Regresión directa del bug: `ApC` es el único acrónimo del PDF de
    referencia con una minúscula. Antes del arreglo desaparecía de
    `page.legend` en silencio (LEGEND_RE no lo admitía), aunque sus 3
    ClassBlock reales sí llevaban el acrónimo correcto (la lectura del
    bloque no pasa por LEGEND_RE, solo la de la leyenda)."""
    pages = _gii_pages()
    page = next(p for p in pages if p.curso == "4º" and p.semestre == 1 and p.itinerario and "omputaci" in p.itinerario)
    acronyms = {e.acronym for e in page.legend}
    assert "ApC" in acronyms
    apc_blocks = [b for b in page.blocks if b.subject_acronym == "ApC"]
    assert len(apc_blocks) == 3

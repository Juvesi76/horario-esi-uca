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

"""select/conflicts.py: detección de combinaciones de grupos incompatibles
dentro de una selección, consumida tanto por el aviso de solapes del
informe de confirmación como por el del HTML."""
import pymupdf

from horario_uca.extract import read_page
from horario_uca.model import SubjectSelection
from horario_uca.parse import parse_page
from horario_uca.resolve import resolve_events
from horario_uca.select import filter_events, find_conflicts


def _page0(sample_pdf_path):
    doc = pymupdf.open(sample_pdf_path)
    return parse_page(read_page(doc, 0))


def test_sin_conflictos_en_seleccion_de_referencia(sample_pdf_path):
    page = _page0(sample_pdf_path)
    events, warnings = resolve_events(page)
    assert warnings == []
    selections = [
        SubjectSelection(acronym="MD", curso="1ºA", groups=["A1", "B1"]),
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1", "B3", "C1"]),
    ]
    filtered = filter_events(events, selections)
    assert find_conflicts(filtered) == []


def test_conflicto_real_ig_c1_cal_c1(sample_pdf_path):
    """Caso real verificado a mano: IG C1 (18:00-20:00, D09) y CAL C1
    (18:00-20:00, B07) chocan en 6 fechas, una de ellas trasladada por nota
    al pie (08/01/2027)."""
    page = _page0(sample_pdf_path)
    events, warnings = resolve_events(page)
    assert warnings == []
    selections = [
        SubjectSelection(acronym="IG", curso="1ºA", groups=["C1"]),
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["C1"]),
    ]
    filtered = filter_events(events, selections)
    conflicts = find_conflicts(filtered)
    assert len(conflicts) == 6
    dates = {c.date for c in conflicts}
    assert dates == {
        "2026-10-14", "2026-10-28", "2026-11-11",
        "2026-11-25", "2026-12-09", "2027-01-08",
    }
    for c in conflicts:
        acronyms = {c.event_a.subject_acronym, c.event_b.subject_acronym}
        assert acronyms == {"IG", "CAL"}
        assert c.event_a.start_time == c.event_b.start_time == "18:00"


def test_mismo_grupo_nominal_no_es_conflicto():
    """Dos ocurrencias del MISMO (asignatura, grupo) nunca deben marcarse
    como conflicto entre sí, aunque coincidieran en fecha/hora (no deberían,
    por la invariante de 'no deduplicar' de la Fase 2, pero la función no
    debe depender de esa invariante externa para no producir un falso
    positivo)."""
    from horario_uca.model import CalendarEvent

    a = CalendarEvent(
        subject_acronym="MD", subject_name="Matemática Discreta", curso="1ºA",
        semestre=1, itinerario=None, group_code="A1", group_type="Clases de teoría",
        date="2026-09-21", weekday=0, start_time="08:30", end_time="10:00",
        room="D01", moved_from=None,
    )
    b = a.model_copy()
    assert find_conflicts([a, b]) == []

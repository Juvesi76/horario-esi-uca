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
from horario_uca.pipeline import parse_document, resolve_document
from horario_uca.report import build_confirmation_report
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


def test_find_conflicts_da_un_par_por_cada_solape_no_uno_por_fecha(sample_pdf_path):
    """`find_conflicts` cuenta PARES evento-evento, no fechas distintas —
    un día con tres o más grupos elegidos coincidiendo aporta varios pares,
    no uno. Fijado con el fixture del "repetidor" (5 asignaturas de 1ºA +
    4 de 2ºA, ya documentado): 36 pares, pero solo 32 fechas distintas
    (12 dentro del mismo curso + 20 entre cursos). La interfaz (`render/
    html.py`, `web/static/index.html`, `report.py`) agrupa estos pares por
    fecha antes de contar — este test fija el dato crudo del que depende
    esa agrupación, para que un cambio futuro en `find_conflicts` no
    rompa esa cuenta en silencio."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    selections = [
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1"]),
        SubjectSelection(acronym="IG", curso="1ºA", groups=["A1"]),
        SubjectSelection(acronym="IP", curso="1ºA", groups=["C1"]),
        SubjectSelection(acronym="MD", curso="1ºA", groups=["A1"]),
        SubjectSelection(acronym="SDIG", curso="1ºA", groups=["D3"]),
        SubjectSelection(acronym="AAED", curso="2ºA", groups=["A1"]),
        SubjectSelection(acronym="AC", curso="2ºA", groups=["A1"]),
        SubjectSelection(acronym="OGE", curso="2ºA", groups=["A1"]),
        SubjectSelection(acronym="RC", curso="2ºA", groups=["A1"]),
    ]
    filtered = filter_events(events, selections)
    conflicts = find_conflicts(filtered)
    assert len(conflicts) == 36
    dates = {c.date for c in conflicts}
    assert len(dates) == 32

    report = build_confirmation_report(selections, pages, filtered)
    assert "12 fechas dentro del mismo curso" in report
    assert "20 fechas entre cursos distintos" in report
    # El informe también anida el detalle por par bajo su fecha — nunca
    # menos pares que fechas (cada fecha lleva al menos uno) ni menos de
    # 36 líneas de detalle en total.
    assert report.count(" choca con ") == 36

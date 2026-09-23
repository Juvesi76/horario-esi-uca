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

import pymupdf

from horario_uca.extract import read_page
from horario_uca.parse import parse_page


def _page0(sample_pdf_path):
    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 0)
    return parse_page(raw)


def test_page0_header(sample_pdf_path):
    page = _page0(sample_pdf_path)
    assert page.curso == "1ºA"
    assert page.semestre == 1
    assert page.itinerario is None


def test_page0_no_unparsed(sample_pdf_path):
    page = _page0(sample_pdf_path)
    assert page.unparsed == []


def test_page0_infos_match_footnotes_no_warnings(sample_pdf_path):
    """[2] Las 14 semana_activa_en_dia_no_lectivo y los 2
    fila_calendario_sin_semana de la página 0 son resultados ESPERADOS
    (semana origen de una nota de traslado; tramo de vacaciones íntegramente
    NO_LECTIVO) — deben salir como ParseInfo, no ParseWarning. page.warnings
    debe estar limpio de ambos códigos: si aparece aquí una anomalía real,
    este test debe fallar mostrando que algo dejó de estar explicado."""
    page = _page0(sample_pdf_path)
    warning_codes = {w.code for w in page.warnings}
    assert "semana_activa_en_dia_no_lectivo" not in warning_codes
    assert "fila_calendario_sin_semana" not in warning_codes

    by_code: dict[str, int] = {}
    for i in page.infos:
        by_code[i.code] = by_code.get(i.code, 0) + 1
    assert by_code == {
        "fila_calendario_sin_semana": 2,
        "semana_activa_en_dia_no_lectivo": 14,
    }

    swap_weeks = {(4, 0), (7, 0), (15, 2), (2, 3)}  # (semana, weekday) de las notas
    for i in page.infos:
        if i.code != "semana_activa_en_dia_no_lectivo":
            continue
        assert any(f"semana {week} " in i.message for week, _ in swap_weeks)


def test_page0_calendar_full_weeks_have_seven_days(sample_pdf_path):
    """[C] Toda CalendarWeek de 1..15 debe tener exactamente 7 días; la 16
    (última, incompleta por fin de periodo) puede tener menos. Ancla de
    regresión del bug de fusión de semana partida cuando un mes empieza con
    una fila de un solo día (1/11/2026, domingo, semana 6)."""
    page = _page0(sample_pdf_path)
    by_week = {w.week_number: w for w in page.calendar}
    for week_number in range(1, 16):
        assert len(by_week[week_number].days) == 7, f"semana {week_number} tiene {len(by_week[week_number].days)} días"
    assert len(by_week[16].days) <= 7


def test_page0_notes(sample_pdf_path):
    """[A] Las 4 notas de la página 0 se parsean sobre el modelo (no contando
    la palabra "impartir" en texto plano) y la validación cruzada contra el
    minicalendario no produce ninguna incoherencia."""
    page = _page0(sample_pdf_path)
    assert len(page.notes) == 4
    origins = {(n.from_week, n.from_weekday) for n in page.notes}
    assert origins == {(2, 3), (4, 0), (7, 0), (15, 2)}
    assert not any(w.code.startswith("nota_") for w in page.warnings)


def test_page0_day_columns_include_empty_friday(sample_pdf_path):
    """[H] La página 0 debe producir 5 cadenas de columna de día por fila,
    incluida la de viernes aunque no tenga bloques, y ninguna cadena debe
    quedar sin día asignado. Si viernes desaparece aquí, en 1ºB (que sí
    tiene clases el viernes) fallaría en silencio."""
    import pymupdf

    from horario_uca.extract import read_page
    from horario_uca.parse.grid import find_day_columns

    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 0)
    day_columns, warnings = find_day_columns(raw)
    assert set(day_columns) == {0, 1, 2, 3, 4}
    assert warnings == []


def test_page0_legend_has_five_subjects(sample_pdf_path):
    page = _page0(sample_pdf_path)
    assert {e.acronym for e in page.legend} == {"CAL", "IG", "IP", "MD", "SDIG"}


def test_page0_block_count(sample_pdf_path):
    page = _page0(sample_pdf_path)
    assert len(page.blocks) == 34
    assert sum(len(b.groups) for b in page.blocks) == 38


def test_ig_ip_a1_fixture(sample_pdf_path):
    """Fixture de regresión permanente: dos rects contiguos en X, mismo
    (asignatura=IG, grupo=A1), horas solapadas, semanas activas disjuntas."""
    page = _page0(sample_pdf_path)
    ig_a1_blocks = [
        b
        for b in page.blocks
        if b.subject_acronym == "IG"
        and b.day_of_week == 0
        and any(g.group_code == "A1" for g in b.groups)
    ]
    assert len(ig_a1_blocks) == 2

    by_start = {b.start_time: b for b in ig_a1_blocks}
    assert set(by_start) == {"11:30", "12:00"}

    early = by_start["11:30"]
    assert early.end_time == "13:00"
    assert early.room == "C01"
    early_weeks = next(g for g in early.groups if g.group_code == "A1").weeks_active
    assert early_weeks == [1]

    late = by_start["12:00"]
    assert late.end_time == "13:30"
    assert late.room == "C01"
    late_weeks = next(g for g in late.groups if g.group_code == "A1").weeks_active
    assert late_weeks == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13]

    assert set(early_weeks).isdisjoint(late_weeks)


def test_md_lunes_0830_has_two_groups(sample_pdf_path):
    """Caso explícito pedido: el bloque de lunes 08:30 de MD contiene DOS
    grupos (A1 teoría y B1 problemas)."""
    page = _page0(sample_pdf_path)
    block = next(
        b
        for b in page.blocks
        if b.subject_acronym == "MD" and b.day_of_week == 0 and b.start_time == "08:30"
    )
    assert block.end_time == "10:00"
    assert block.room == "D01"
    assert {g.group_code for g in block.groups} == {"A1", "B1"}

    a1 = next(g for g in block.groups if g.group_code == "A1")
    b1 = next(g for g in block.groups if g.group_code == "B1")
    assert a1.group_type == "Clases de teoría"
    assert b1.group_type == "Clases de problemas"
    assert a1.weeks_active == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13]
    assert b1.weeks_active == [14, 16]

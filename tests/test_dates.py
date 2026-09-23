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

"""Fase 3: casos frontera de resolve/dates.py sobre la página 0 (1ºA/1º).

Estos son los tests que deciden si la Fase 3 está bien — en particular, si el
filtro `weeks_active` se evalúa con la semana de ORIGEN de la nota (correcto)
o con la de destino (incorrecto, pero produce un calendario que también
"parece" plausible).
"""
import pymupdf

from horario_uca.extract import read_page
from horario_uca.model import ClassBlock, WeekGroup
from horario_uca.parse import parse_page
from horario_uca.resolve import resolve_events, validate_events_lectivo


def _page0(sample_pdf_path):
    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 0)
    return parse_page(raw)


def _events_by_date(sample_pdf_path):
    page = _page0(sample_pdf_path)
    events, warnings = resolve_events(page)
    assert warnings == []
    by_date = {}
    for e in events:
        by_date.setdefault(e.date, []).append(e)
    return by_date


def test_jueves_semana2_movido_a_viernes_25_09(sample_pdf_path):
    """SDIG B1 (jueves, semana 2 activa) genera su evento el 25/09/2026
    (viernes de la semana 1), con moved_from=2026-10-01."""
    by_date = _events_by_date(sample_pdf_path)
    events = by_date.get("2026-09-25", [])
    sdig_b1 = [e for e in events if e.subject_acronym == "SDIG" and e.group_code == "B1"]
    assert len(sdig_b1) == 1
    e = sdig_b1[0]
    assert e.moved_from == "2026-10-01"
    assert e.weekday == 4  # viernes


def test_jueves_01_10_2026_vacio(sample_pdf_path):
    """01/10/2026 es la fecha de ORIGEN del traslado (jueves semana 2) — no
    debe quedar ningún evento ahí, todo lo que disparaba esa semana/día se
    movió al 25/09."""
    by_date = _events_by_date(sample_pdf_path)
    assert by_date.get("2026-10-01", []) == []


def test_lunes_semana4_y_7_movidos_a_viernes(sample_pdf_path):
    """MD A1, CAL A1, IG A1 (lunes, semanas 4 y 7 activas) generan sus
    eventos el 16/10/2026 y el 06/11/2026 (viernes de esas semanas)."""
    by_date = _events_by_date(sample_pdf_path)

    for fecha, origen in (("2026-10-16", "2026-10-12"), ("2026-11-06", "2026-11-02")):
        events = by_date.get(fecha, [])
        found = {(e.subject_acronym, e.group_code) for e in events if e.moved_from == origen}
        assert found == {("MD", "A1"), ("CAL", "A1"), ("IG", "A1")}, (fecha, found)


def test_lunes_12_10_y_02_11_vacios(sample_pdf_path):
    """12/10/2026 y 02/11/2026 son las fechas de ORIGEN de los traslados de
    lunes — vacías, todo lo que disparaba se movió al viernes."""
    by_date = _events_by_date(sample_pdf_path)
    assert by_date.get("2026-10-12", []) == []
    assert by_date.get("2026-11-02", []) == []


def test_miercoles_semana15_movido_a_viernes_08_01(sample_pdf_path):
    """Los 7 bloques de miércoles activos en semana 15 (MD B1, IG B1, MD B3,
    CAL B1, IG C5, IG C1, CAL C1) generan su evento el 08/01/2027, con
    moved_from=2027-01-06."""
    by_date = _events_by_date(sample_pdf_path)
    events = by_date.get("2027-01-08", [])
    found = {(e.subject_acronym, e.group_code) for e in events if e.moved_from == "2027-01-06"}
    assert found == {
        ("MD", "B1"),
        ("IG", "B1"),
        ("MD", "B3"),
        ("CAL", "B1"),
        ("IG", "C5"),
        ("IG", "C1"),
        ("CAL", "C1"),
    }


def test_lunes_21_12_2026_lleva_md_b1_no_md_a1(sample_pdf_path):
    """Semana 14 (lunes 21/12/2026): MD A1 NO está activo esa semana (excluye
    12, 14, 15, 16), MD B1 SÍ (activo en 14 y 16). Fecha normal, sin
    traslado — comprueba la resolución de fechas "de toda la vida", no solo
    el caso de traslados."""
    by_date = _events_by_date(sample_pdf_path)
    events = by_date.get("2026-12-21", [])
    md_events = {(e.subject_acronym, e.group_code) for e in events if e.subject_acronym == "MD"}
    assert md_events == {("MD", "B1")}
    assert all(e.moved_from is None for e in events if e.subject_acronym == "MD")


def test_semana_origen_no_destino(sample_pdf_path):
    """El caso que distingue evaluar con la semana de ORIGEN de evaluar con
    la de destino: un bloque de jueves activo en la semana 3 pero NO en la 2
    no debe generar nada el 25/09/2026, aunque ese día sea el destino del
    traslado de la semana 2. Si `resolve_events` evaluara por error con la
    semana de DESTINO en vez de la de ORIGEN, este bloque dispararía ahí (su
    weeks_active no contiene la 2, así que con la regla correcta no debe
    aparecer)."""
    page = _page0(sample_pdf_path)
    # localizar un bloque real de jueves activo en semana 3 pero no en 2
    candidates = [
        (block, group)
        for block in page.blocks
        if block.day_of_week == 3  # jueves
        for group in block.groups
        if 3 in group.weeks_active and 2 not in group.weeks_active
    ]
    assert candidates, "se necesita al menos un bloque de jueves activo en semana 3 pero no en 2 para este test"

    events, warnings = resolve_events(page)
    assert warnings == []
    by_date = {}
    for e in events:
        by_date.setdefault(e.date, []).append(e)

    for block, group in candidates:
        on_25_09 = [
            e
            for e in by_date.get("2026-09-25", [])
            if e.subject_acronym == block.subject_acronym and e.group_code == group.group_code
        ]
        assert on_25_09 == [], (block.subject_acronym, group.group_code)


def test_recuento_de_control_eventos_por_grupo(sample_pdf_path):
    """Para cada (ClassBlock, WeekGroup), el número de CalendarEvent
    generados debe ser exactamente igual al número de weeks_active — un
    traslado cambia la FECHA de un evento, nunca añade ni quita eventos."""
    page = _page0(sample_pdf_path)
    events, warnings = resolve_events(page)
    assert warnings == []

    total_expected = sum(len(g.weeks_active) for b in page.blocks for g in b.groups)
    assert len(events) == total_expected

    # Caso explícito del enunciado: MD A1 tiene 12 semanas activas el lunes.
    # MD también tiene A1 el martes a la misma hora (el mismo grupo teórico,
    # dos sesiones semanales) — mismo start_time/end_time/room, así que no
    # basta con filtrar por esos campos para aislar solo las del lunes. Se
    # aíslan por lo que de verdad las distingue: las del lunes son las que NO
    # se movieron (weekday sigue en 0) o las que sí se movieron desde un
    # lunes conocido (moved_from en las dos fechas de origen de lunes de esta
    # página, ver test_lunes_semana4_y_7_movidos_a_viernes).
    md_a1_lunes = next(
        g
        for b in page.blocks
        if b.subject_acronym == "MD" and b.day_of_week == 0
        for g in b.groups
        if g.group_code == "A1"
    )
    assert len(md_a1_lunes.weeks_active) == 12

    lunes_moved_from = {"2026-10-12", "2026-11-02"}
    md_a1_lunes_events = [
        e
        for e in events
        if e.subject_acronym == "MD"
        and e.group_code == "A1"
        and (e.weekday == 0 or e.moved_from in lunes_moved_from)
    ]
    assert len(md_a1_lunes_events) == 12


def test_traslado_jueves_semana2_caso_real_ip_b2_pagina1B(sample_pdf_path):
    """[1a] Caso real (no vacuo) que sí discrimina origen de destino: página
    2 (1ºB), IP·B2, jueves, weeks_active = {2..13} — incluye la semana 2
    (origen del traslado) pero NO la 1 (destino). Con la regla correcta
    (evaluar origen) SÍ debe generar un evento el 25/09/2026 con
    moved_from=2026-10-01, aunque la semana 1 no esté en weeks_active. Con la
    regla incorrecta (evaluar destino) NO lo generaría, porque 1 no está en
    weeks_active."""
    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 2)
    page = parse_page(raw)

    ip_b2 = next(
        g
        for b in page.blocks
        if b.subject_acronym == "IP" and b.day_of_week == 3
        for g in b.groups
        if g.group_code == "B2"
    )
    assert 2 in ip_b2.weeks_active
    assert 1 not in ip_b2.weeks_active  # confirma que el caso sigue siendo discriminante

    events, warnings = resolve_events(page)
    assert warnings == []
    ip_b2_events = [e for e in events if e.subject_acronym == "IP" and e.group_code == "B2"]

    on_25_09 = [e for e in ip_b2_events if e.date == "2026-09-25"]
    assert len(on_25_09) == 1
    assert on_25_09[0].moved_from == "2026-10-01"
    assert on_25_09[0].weekday == 4  # viernes

    assert [e for e in ip_b2_events if e.date == "2026-10-01"] == []


def test_traslado_jueves_semana2_caso_real_mps_a1_pagina16_4o_isw(sample_pdf_path):
    """Segundo caso real que discrimina origen de destino, independiente del
    IP·B2 de página 2 (1ºB) — encontrado verificando el calendario de 4º,
    Itinerario de Ingeniería del Software, semestre 1 (página 16): MPS·A1
    tiene DOS bloques de jueves distintos bajo el mismo grupo nominal (mismo
    patrón que MD/IG·A1 de p.0 — varios ClassBlock por grupo, cada uno con su
    propio horario y weeks_active):
      - 09:00-11:00, weeks_active incluye la 1 (entre otras) — sesión normal.
      - 09:00-13:00 (4h), weeks_active = {2} ÚNICAMENTE — incluye la semana
        de ORIGEN del traslado (2) pero NO la de destino (1).
    Con la regla correcta, el bloque de 4h genera exactamente UN evento, el
    25/09/2026 (destino), con moved_from=2026-10-01 — pese a que su
    weeks_active no contiene la 1. Un segundo caso real en una página de
    itinerario (no solo en páginas simples de 1º/2º) protege mejor la regla
    que un único caso real."""
    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 16)
    page = parse_page(raw)
    assert page.curso == "4º" and page.itinerario == "Itinerario de Ingeniería del Software"

    mps_a1_4h = next(
        group
        for block in page.blocks
        if block.subject_acronym == "MPS" and block.day_of_week == 3 and block.start_time == "09:00" and block.end_time == "13:00"
        for group in block.groups
        if group.group_code == "A1"
    )
    assert mps_a1_4h.weeks_active == [2]  # confirma que el caso sigue siendo discriminante

    events, warnings = resolve_events(page)
    assert warnings == []
    mps_events = [
        e for e in events
        if e.subject_acronym == "MPS" and e.group_code == "A1" and e.start_time == "09:00" and e.end_time == "13:00"
    ]
    assert len(mps_events) == 1
    assert mps_events[0].date == "2026-09-25"
    assert mps_events[0].moved_from == "2026-10-01"
    assert mps_events[0].weekday == 4  # viernes


def _thursday_block(weeks_active: list[int]) -> ClassBlock:
    return ClassBlock(
        subject_acronym="TEST",
        day_of_week=3,  # jueves
        start_time="09:00",
        end_time="10:00",
        room="X01",
        groups=[WeekGroup(group_code="A1", group_type="Clases de teoría", weeks_active=weeks_active)],
        bbox=(0.0, 0.0, 1.0, 1.0),
    )


def test_sintetico_semana_origen_vs_destino(sample_pdf_path):
    """[1b] Test unitario sintético, independiente del PDF: dos ClassBlock
    de jueves fabricados a mano, contra el calendario y las notas REALES de
    la página 0.
      - weeks_active={3,4,5} (sin la 2): NO debe generar nada el 25/09/2026.
      - weeks_active={2}: SÍ debe generar un evento el 25/09/2026, con
        moved_from=2026-10-01.
    Si la implementación se invierte para evaluar con la semana de DESTINO en
    vez de la de ORIGEN, este test debe fallar (comprobado a mano invirtiendo
    la condición en session... es decir en resolve/dates.py durante esta
    revisión: al cambiar `note_origins.get((week, ...))` por una búsqueda
    contra `to_week`, el segundo assert de este test pasó a fallar, como se
    esperaba; revertido de inmediato)."""
    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 0)
    page = parse_page(raw)

    no_participa = page.model_copy(update={"blocks": [_thursday_block([3, 4, 5])]})
    events, warnings = resolve_events(no_participa)
    assert warnings == []
    assert [e for e in events if e.date == "2026-09-25"] == []

    si_participa = page.model_copy(update={"blocks": [_thursday_block([2])]})
    events, warnings = resolve_events(si_participa)
    assert warnings == []
    assert len(events) == 1
    assert events[0].date == "2026-09-25"
    assert events[0].moved_from == "2026-10-01"
    assert events[0].weekday == 4


def test_fusion_viernes_propio_mas_lunes_trasladado_sintetico(sample_pdf_path):
    """[2] La fusión (un viernes con su propia clase Y una clase de lunes
    trasladada a la vez) no aparece en ninguna de las 24 páginas del PDF de
    referencia — comprobado: 0 bloques de viernes activos en semana 4, 7 o 15
    en las 10 páginas de semestre 1 (el propio generador del PDF evita esa
    colisión). No hay caso real que fijar como fixture, así que se fabrica
    uno: un bloque de viernes normal con semana 4 activa + un bloque de lunes
    (reusando uno real de p.0, MD A1, que sí tiene la semana 4 activa) en la
    misma página. El 16/10/2026 debe llevar ambos, con moved_from correcto
    en cada uno (None en el propio, la fecha de origen en el trasladado) —
    no hay lógica de "fusión" especial que probar: los eventos simplemente
    coexisten en la misma fecha porque resolve_events no deduplica ni
    sobrescribe por fecha, cada (bloque, grupo, semana) es independiente."""
    doc = pymupdf.open(sample_pdf_path)
    raw = read_page(doc, 0)
    page = parse_page(raw)

    md_a1_lunes = next(
        b
        for b in page.blocks
        if b.subject_acronym == "MD" and b.day_of_week == 0 and b.start_time == "08:30"
    )
    viernes_propio = ClassBlock(
        subject_acronym="PROPIO",
        day_of_week=4,
        start_time="09:00",
        end_time="11:00",
        room="Y01",
        groups=[WeekGroup(group_code="A1", group_type="Clases de teoría", weeks_active=[4])],
        bbox=(0.0, 0.0, 1.0, 1.0),
    )

    fusion_page = page.model_copy(update={"blocks": [md_a1_lunes, viernes_propio]})
    events, warnings = resolve_events(fusion_page)
    assert warnings == []

    day = [e for e in events if e.date == "2026-10-16"]
    by_subject = {e.subject_acronym: e for e in day}
    assert set(by_subject) == {"MD", "PROPIO"}
    assert by_subject["MD"].moved_from == "2026-10-12"
    assert by_subject["MD"].weekday == 4
    assert by_subject["PROPIO"].moved_from is None
    assert by_subject["PROPIO"].weekday == 4


def test_cero_bloques_martes_semana15_en_las_14_paginas_de_semestre_2(sample_pdf_path):
    """[A] Evidencia directa, no por ausencia de búsqueda: de la que depende
    la conclusión de que la fusión (propio + trasladado el mismo día) no
    ocurre nunca en este PDF para la nota de fin de semestre (jueves semana
    15 → MARTES semana 15, único traslado a un día lectivo normal, no a
    viernes). Recuento página a página, no solo el total agregado, para que
    un fallo futuro señale la página exacta."""
    doc = pymupdf.open(sample_pdf_path)
    counts_by_page: dict[int, int] = {}
    sem2_pages = 0
    for page_index in range(doc.page_count):
        page = parse_page(read_page(doc, page_index))
        if page.semestre != 2:
            continue
        sem2_pages += 1
        counts_by_page[page_index] = sum(
            1 for b in page.blocks if b.day_of_week == 1 for g in b.groups if 15 in g.weeks_active
        )

    assert sem2_pages == 14
    assert counts_by_page == {i: 0 for i in counts_by_page}, counts_by_page


def test_25_05_2027_solo_lleva_trasladados_en_todo_el_documento(sample_pdf_path):
    """[A] Complemento del test anterior: con 0 bloques de martes activos en
    semana 15 confirmado, ESTE test comprueba el otro lado — que el
    25/05/2027 (destino de la nota de fin de semestre en las 14 páginas de
    semestre 2) no lleva NINGÚN evento propio en ninguna página del
    documento, solo trasladados desde el 27/05/2027. Listado exhaustivo
    sobre las 24 páginas, no una muestra."""
    doc = pymupdf.open(sample_pdf_path)
    eventos_25_05 = []
    for page_index in range(doc.page_count):
        page = parse_page(read_page(doc, page_index))
        events, warnings = resolve_events(page)
        assert warnings == []
        eventos_25_05.extend(e for e in events if e.date == "2027-05-25")

    assert eventos_25_05, "se esperaban eventos trasladados el 25/05/2027 en alguna de las 24 páginas"
    assert all(e.moved_from == "2027-05-27" for e in eventos_25_05), [
        (e.subject_acronym, e.group_code, e.moved_from) for e in eventos_25_05
    ]
    assert not any(e.moved_from is None for e in eventos_25_05)


def test_todos_los_eventos_caen_en_dia_lectivo_24_paginas(sample_pdf_path):
    """[4] La aserción más decisiva de la Fase 3: el recuento 5475==5475
    garantiza CUÁNTOS eventos hay, no DÓNDE caen. Un evento podría aterrizar
    en un día NO_LECTIVO o FUERA_DE_PERIODO (p.ej. semana 16 de p.0, donde
    miércoles-viernes son FUERA_DE_PERIODO) sin que el recuento lo note.
    Verificado sobre las 24 páginas: 0 violaciones."""
    doc = pymupdf.open(sample_pdf_path)
    total_events = 0
    all_warnings = []
    for page_index in range(doc.page_count):
        raw = read_page(doc, page_index)
        page = parse_page(raw)
        events, resolve_warnings = resolve_events(page)
        assert resolve_warnings == []
        total_events += len(events)
        all_warnings.extend(validate_events_lectivo(page, events))

    assert total_events == 5475
    assert all_warnings == [], [w.message for w in all_warnings]

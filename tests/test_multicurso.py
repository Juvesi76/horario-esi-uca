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

"""Selección con asignaturas de más de un curso a la vez ("repetidor" que
lleva asignaturas de un curso distinto al principal). Verifica de punta a
punta contra el pipeline real (sin mocks): eventos de ambos cursos en el
mismo calendario, fechas correctas, y detección de conflictos entre cursos
distintos.
"""
from horario_uca.model import SubjectSelection
from horario_uca.pipeline import parse_document, resolve_document
from horario_uca.select import filter_events, find_conflicts


def test_calendario_identico_entre_paginas_del_mismo_semestre(sample_pdf_path):
    """Invariante de la que depende combinar eventos de páginas (cursos)
    distintas en un único calendario: si el calendario académico (mapa
    semana+día -> fecha/lectivo) no fuera el mismo en todas las páginas de
    un semestre, unir eventos de dos cursos podría desalinear fechas sin
    que ningún test de una sola página lo detectase."""
    pages = parse_document(str(sample_pdf_path))

    def canonical(calendar):
        return tuple(
            (week.week_number, tuple((d.date, d.status) for d in week.days))
            for week in calendar
        )

    by_semestre: dict[int, list] = {}
    for page in pages:
        if page.semestre is None:
            continue
        by_semestre.setdefault(page.semestre, []).append(page)

    assert set(by_semestre) == {1, 2}
    assert len(by_semestre[1]) == 10
    assert len(by_semestre[2]) == 14

    for semestre, group_pages in by_semestre.items():
        reference = canonical(group_pages[0].calendar)
        for page in group_pages[1:]:
            assert canonical(page.calendar) == reference, (
                f"calendario distinto en página {page.page_index} frente a la "
                f"página {group_pages[0].page_index} (ambas semestre {semestre})"
            )


def test_seleccion_con_asignaturas_de_dos_cursos_a_la_vez(sample_pdf_path):
    pages = parse_document(str(sample_pdf_path))
    events, warnings = resolve_document(pages)
    assert warnings == []

    selections = [
        SubjectSelection(acronym="MD", curso="1ºA", groups=["A1"]),
        SubjectSelection(acronym="RC", curso="2ºA", groups=["A1"]),
    ]
    filtered = filter_events(events, selections)

    # Eventos de los dos cursos presentes en el mismo calendario resuelto.
    cursos_presentes = {e.curso for e in filtered}
    assert cursos_presentes == {"1ºA", "2ºA"}
    assert sum(1 for e in filtered if e.curso == "1ºA" and e.subject_acronym == "MD") == 24
    assert sum(1 for e in filtered if e.curso == "2ºA" and e.subject_acronym == "RC") == 20

    # Fechas correctas: cada evento cae en un día que el calendario de SU
    # PROPIA página de origen marca como lectivo — comprobación posible
    # porque el calendario es idéntico entre páginas del mismo semestre
    # (ver test anterior), así que no hace falta rastrear de qué página
    # exacta vino cada evento para validar su fecha.
    lectivas_por_fecha = {
        d.date: d.status for page in pages if page.semestre == 1 for week in page.calendar for d in week.days
    }
    for e in filtered:
        assert lectivas_por_fecha[e.date].value == "lectivo", f"{e.date} no es lectivo para {e.subject_acronym}"

    # Conflictos entre cursos distintos: MD A1 (1ºA) y RC A1 (2ºA) coinciden
    # en horario un número real de veces — verificado contra el pipeline
    # antes de fijarlo aquí, no un valor inventado.
    conflicts = find_conflicts(filtered)
    assert len(conflicts) == 20
    for c in conflicts:
        cursos = {c.event_a.curso, c.event_b.curso}
        acronyms = {c.event_a.subject_acronym, c.event_b.subject_acronym}
        assert cursos == {"1ºA", "2ºA"}
        assert acronyms == {"MD", "RC"}

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

"""Filtra CalendarEvent por la selección del usuario, con validación
accionable ANTES de filtrar.

`SubjectSelection` es una entrada de usuario, no un dato ya validado: puede
referirse a una asignatura que no existe en ese curso, ser ambigua entre
itinerarios, pedir un grupo que no existe, o dejarse sin elegir un tipo de
grupo (letra) que sí tiene docencia real. Las cuatro cosas se comprueban
contra los `SchedulePage` ya parseados, no contra listas hardcodeadas.
"""
from __future__ import annotations

from collections import defaultdict

from horario_uca.model import (
    CalendarEvent,
    ClassBlock,
    GROUP_TYPES,
    ParseWarning,
    SchedulePage,
    SubjectSelection,
)


def _matching_pages(selection: SubjectSelection, pages: list[SchedulePage]) -> list[SchedulePage]:
    return [
        page
        for page in pages
        if page.curso == selection.curso
        and any(b.subject_acronym == selection.acronym for b in page.blocks)
    ]


def validate_selection(selection: SubjectSelection, pages: list[SchedulePage]) -> list[ParseWarning]:
    """Valida una SubjectSelection contra los SchedulePage ya parseados.
    Cada código de warning es accionable: dice explícitamente qué hacer, no
    solo que algo falló.
    """
    warnings: list[ParseWarning] = []

    candidates = _matching_pages(selection, pages)
    if not candidates:
        warnings.append(
            ParseWarning(
                code="asignatura_no_existe_en_curso",
                message=(
                    f"{selection.acronym!r} no tiene ningún bloque de clase en curso "
                    f"{selection.curso!r} en ninguna página parseada"
                ),
                page_index=-1,
            )
        )
        return warnings

    itinerarios_disponibles = sorted({p.itinerario for p in candidates if p.itinerario is not None})
    if selection.itinerario is None and len(itinerarios_disponibles) > 1:
        warnings.append(
            ParseWarning(
                code="itinerario_ambiguo",
                message=(
                    f"{selection.acronym!r} en curso {selection.curso!r} aparece en varios itinerarios "
                    f"({itinerarios_disponibles}) — hay que indicar `itinerario` en la selección para "
                    "saber cuál"
                ),
                page_index=-1,
            )
        )
        return warnings

    if selection.itinerario is not None:
        candidates = [p for p in candidates if p.itinerario == selection.itinerario]
        if not candidates:
            warnings.append(
                ParseWarning(
                    code="asignatura_no_existe_en_itinerario",
                    message=(
                        f"{selection.acronym!r} no aparece en curso {selection.curso!r}, itinerario "
                        f"{selection.itinerario!r} (sí en: {itinerarios_disponibles})"
                    ),
                    page_index=-1,
                )
            )
            return warnings

    page = candidates[0]
    blocks: list[ClassBlock] = [b for b in page.blocks if b.subject_acronym == selection.acronym]

    available_by_letter: dict[str, set[str]] = defaultdict(set)
    for block in blocks:
        for group in block.groups:
            letter = group.group_code[0]
            available_by_letter[letter].add(group.group_code)
    available_codes = {code for codes in available_by_letter.values() for code in codes}

    for code in selection.groups:
        if code not in available_codes:
            warnings.append(
                ParseWarning(
                    code="grupo_no_existe",
                    message=(
                        f"{selection.acronym!r} grupo {code!r} no existe — disponibles: "
                        f"{sorted(available_codes)}"
                    ),
                    page_index=page.page_index,
                )
            )

    # `solo_examen`: no ir a clase es la propia intención, no un olvido —
    # avisar de cada letra sin grupo elegido sería ruido, no información.
    if selection.solo_examen:
        return warnings

    chosen_letters = {code[0] for code in selection.groups if available_codes and code in available_codes}
    for letter, codes in available_by_letter.items():
        if letter not in chosen_letters:
            warnings.append(
                ParseWarning(
                    code="letra_con_docencia_sin_grupo_elegido",
                    message=(
                        f"{selection.acronym!r} tiene grupos de tipo {letter!r} "
                        f"({GROUP_TYPES.get(letter, '?')}: {sorted(codes)}) con docencia real, pero la "
                        "selección no eligió ninguno — si es intencional (se descarta ese tipo entero), "
                        "ignorar este aviso"
                    ),
                    page_index=page.page_index,
                )
            )

    return warnings


def _matches(event: CalendarEvent, selection: SubjectSelection) -> bool:
    return (
        event.subject_acronym == selection.acronym
        and event.curso == selection.curso
        and (selection.itinerario is None or event.itinerario == selection.itinerario)
        and event.group_code in selection.groups
    )


def filter_events(events: list[CalendarEvent], selections: list[SubjectSelection]) -> list[CalendarEvent]:
    return [event for event in events if any(_matches(event, s) for s in selections)]

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

"""Resolución de fechas reales: semana + día de la semana + CalendarWeek de
la página → fecha concreta, aplicando los traslados de `notes.py`.

Regla crítica: `weeks_active` se evalúa
SIEMPRE con la semana de ORIGEN de la nota, nunca con la de destino. Un
bloque genera un evento en la fecha de DESTINO de una nota si y solo si su
`weeks_active` contiene el número de semana de ORIGEN de esa nota — el número
de semana de destino es irrelevante para decidir si el bloque "dispara". Es
el error silencioso más probable de esta fase: evaluar con la semana de
destino produce un calendario que parece plausible pero está mal, y ningún
test genérico de forma/tipos lo detecta — solo el fixture de casos frontera
de `tests/test_dates.py` lo haría.
"""
from __future__ import annotations

from horario_uca.model import CalendarEvent, DayLectivoStatus, ParseWarning, SchedulePage, ScheduleNote


def _date_lookup(page: SchedulePage) -> dict[tuple[int, int], str]:
    """{(week_number, weekday 0-6): fecha ISO} a partir de page.calendar."""
    lookup: dict[tuple[int, int], str] = {}
    for week in page.calendar:
        for weekday, day in enumerate(week.days):
            lookup[(week.week_number, weekday)] = day.date
    return lookup


def validate_events_lectivo(page: SchedulePage, events: list[CalendarEvent]) -> list[ParseWarning]:
    """[4] El recuento de eventos (== suma de weeks_active) garantiza que un
    traslado mueve un evento sin crear ni perder ninguno, pero no garantiza
    DÓNDE cae — un evento podría aterrizar en un día NO_LECTIVO o
    FUERA_DE_PERIODO sin que el recuento lo note. Verificado sobre las 24
    páginas (5475 eventos): 0 violaciones — ningún bloque tiene una semana
    activa en un día de la semana cuyo status en esa semana no sea LECTIVO,
    ni de forma natural ni tras un traslado. Se deja como función reutilizable
    (no solo un script de una vez) porque es la aserción más decisiva de la
    fase y debe volver a correr si cambia el PDF de referencia o se añade
    uno nuevo.
    """
    warnings: list[ParseWarning] = []
    date_status = {d.date: d.status for week in page.calendar for d in week.days}
    for event in events:
        status = date_status.get(event.date)
        if status != DayLectivoStatus.LECTIVO:
            warnings.append(
                ParseWarning(
                    code="evento_en_dia_no_lectivo",
                    message=(
                        f"{event.subject_acronym} {event.group_code} el {event.date}: "
                        f"día marcado {status.value if status else 'SIN_FECHA_EN_CALENDARIO'}"
                        + (f" (trasladado desde {event.moved_from})" if event.moved_from else "")
                    ),
                    page_index=page.page_index,
                )
            )
    return warnings


def resolve_events(page: SchedulePage) -> tuple[list[CalendarEvent], list[ParseWarning]]:
    """Devuelve los CalendarEvent de una página ya con fecha real, aplicando
    los traslados. Nunca lanza excepción (misma regla que el resto de
    parse/): si una semana activa no tiene fecha en el calendario de la
    página, se registra ParseWarning y esa ocurrencia concreta se descarta,
    el resto de la página se resuelve igual.
    """
    warnings: list[ParseWarning] = []
    date_lookup = _date_lookup(page)

    note_origins: dict[tuple[int, int], ScheduleNote] = {}
    for note in page.notes:
        key = (note.from_week, note.from_weekday)
        if key in note_origins:
            warnings.append(
                ParseWarning(
                    code="nota_origen_duplicado",
                    message=(
                        f"dos notas con el mismo origen (semana {note.from_week}, weekday "
                        f"{note.from_weekday}): {note_origins[key].raw_text!r} y {note.raw_text!r}"
                    ),
                    page_index=page.page_index,
                )
            )
        note_origins[key] = note

    legend_by_acronym = {e.acronym: e for e in page.legend}

    events: list[CalendarEvent] = []
    for block in page.blocks:
        legend_entry = legend_by_acronym.get(block.subject_acronym)
        subject_name = legend_entry.name if legend_entry else block.subject_acronym

        for group in block.groups:
            for week in group.weeks_active:
                note = note_origins.get((week, block.day_of_week))
                if note is not None:
                    # Traslado: la semana de ORIGEN de la nota coincide con
                    # esta ocurrencia — el evento cae en la fecha de DESTINO
                    # de la nota, no en la fecha "natural" de (week,
                    # block.day_of_week). moved_from es la fecha de origen.
                    event_date = note.to_date
                    weekday = note.to_weekday
                    moved_from = note.from_date
                else:
                    event_date = date_lookup.get((week, block.day_of_week))
                    weekday = block.day_of_week
                    moved_from = None
                    if event_date is None:
                        warnings.append(
                            ParseWarning(
                                code="semana_sin_fecha",
                                message=(
                                    f"{block.subject_acronym} {group.group_code}: semana {week} "
                                    "no tiene fecha en el calendario de la página"
                                ),
                                page_index=page.page_index,
                                bbox=block.bbox,
                            )
                        )
                        continue

                events.append(
                    CalendarEvent(
                        subject_acronym=block.subject_acronym,
                        subject_name=subject_name,
                        curso=page.curso,
                        semestre=page.semestre,
                        itinerario=page.itinerario,
                        group_code=group.group_code,
                        group_type=group.group_type,
                        date=event_date,
                        weekday=weekday,
                        start_time=block.start_time,
                        end_time=block.end_time,
                        room=block.room,
                        moved_from=moved_from,
                    )
                )

    return events, warnings

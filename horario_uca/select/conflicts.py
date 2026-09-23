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

"""Detección de conflictos de horario DENTRO de una selección: dos eventos de
grupos distintos que caen en la misma fecha con horas solapadas son una
combinación de grupos incompatible — un alumno no puede estar en dos aulas a
la vez. No es un fallo del parser ni del render: el solape se calcula
sobre la SELECCIÓN ya filtrada del alumno, no sobre la página completa.

`find_conflicts` es la única función que lo calcula: `report.py` (Fase 4,
antes de generar nada) y `render/html.py` (Fase 5, para explicarlo en el
propio calendario) consumen su resultado en vez de recalcularlo cada uno por
su lado, para que el aviso en el informe y el aviso en el HTML nunca puedan
divergir entre sí.
"""
from __future__ import annotations

from horario_uca.model import CalendarEvent, ScheduleConflict


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def find_conflicts(events: list[CalendarEvent]) -> list[ScheduleConflict]:
    by_date: dict[str, list[CalendarEvent]] = {}
    for event in events:
        by_date.setdefault(event.date, []).append(event)

    conflicts: list[ScheduleConflict] = []
    for event_date in sorted(by_date):
        day_events = sorted(by_date[event_date], key=lambda e: e.start_time)
        for i in range(len(day_events)):
            for j in range(i + 1, len(day_events)):
                a, b = day_events[i], day_events[j]
                if a.subject_acronym == b.subject_acronym and a.group_code == b.group_code:
                    continue  # mismo grupo nominal (dos bloques del mismo grupo en subcolumnas contiguas, no un conflicto real)
                if _to_minutes(a.start_time) < _to_minutes(b.end_time) and _to_minutes(b.start_time) < _to_minutes(a.end_time):
                    conflicts.append(ScheduleConflict(date=event_date, event_a=a, event_b=b))
    return conflicts

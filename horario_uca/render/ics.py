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

"""Export .ics: un VEVENT por sesión de clase real (evento ya resuelto a
fecha concreta).

Conformidad RFC 5545:
- El fichero incluye un VTIMEZONE de Europe/Madrid porque todo VEVENT usa
  `DTSTART;TZID=Europe/Madrid` y la norma exige que ese TZID se resuelva
  dentro del propio objeto — sin él, un cliente que no reconozca el nombre
  Olson puede desplazar los eventos una hora, y el semestre 1 cruza el cambio
  de hora del 25/10/2026, así que el fallo partiría el calendario en dos.
- El VTIMEZONE se construye a mano con dos reglas RRULE (DAYLIGHT/CEST desde
  el último domingo de marzo, STANDARD/CET desde el último domingo de
  octubre) — NO con `icalendar.Timezone.from_tzid("Europe/Madrid")`, que se
  probó primero: genera un VTIMEZONE basado en listas RDATE (una entrada por
  año 1974-2037) técnicamente válido, pero un segundo parser independiente
  (`vobject`) lo interpretó mal — resolvía el 21/09/2026 a +01:00 (CET) en
  vez de +02:00 (CEST), un fallo real de exactamente el tipo que motivó este
  apartado. Con la versión RRULE, el mismo parser (`vobject`) resuelve
  correctamente +02:00 antes del cambio de hora y +01:00 después — verificado
  explícitamente para ambos lados del 25/10/2026 antes de fijar esta versión.
- Cada VEVENT lleva DTSTAMP (obligatorio en RFC 5545), la marca de generación
  del fichero en UTC — no confundir con el UID: DTSTAMP SÍ cambia entre
  regeneraciones (es la hora de creación de ESTE fichero), el UID no debe
  cambiar nunca.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone, TimezoneDaylight, TimezoneStandard

from horario_uca.model import CalendarEvent, ExamEntry
from horario_uca.select.exams import EXAM_NOMINAL_DURATION_MINUTES

_UID_DOMAIN = "horario-uca.local"
_TZ = ZoneInfo("Europe/Madrid")


def _vtimezone_europe_madrid() -> Timezone:
    tz = Timezone()
    tz.add("TZID", "Europe/Madrid")
    tz.add("X-LIC-LOCATION", "Europe/Madrid")

    daylight = TimezoneDaylight()
    daylight.add("TZNAME", "CEST")
    daylight.add("DTSTART", datetime(1970, 3, 29, 2, 0, 0))
    daylight.add("TZOFFSETFROM", timedelta(hours=1))
    daylight.add("TZOFFSETTO", timedelta(hours=2))
    daylight.add("RRULE", {"FREQ": "YEARLY", "BYMONTH": 3, "BYDAY": "-1SU"})
    tz.add_component(daylight)

    standard = TimezoneStandard()
    standard.add("TZNAME", "CET")
    standard.add("DTSTART", datetime(1970, 10, 25, 3, 0, 0))
    standard.add("TZOFFSETFROM", timedelta(hours=2))
    standard.add("TZOFFSETTO", timedelta(hours=1))
    standard.add("RRULE", {"FREQ": "YEARLY", "BYMONTH": 10, "BYDAY": "-1SU"})
    tz.add_component(standard)

    return tz


def _parse_time(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _uid(event: CalendarEvent) -> str:
    """Solo campos estables del propio evento — nada posicional (un
    índice de enumeración rompía el UID en cada regeneración, 82
    duplicados al reimportar). date+start_time+acronym+group_code ya
    identifica el evento sin ambigüedad (dos bloques del mismo grupo nunca
    comparten fecha con horas solapadas — invariante verificada en las 24
    páginas); el aula se añade
    igualmente como desempate barato y estable.
    """
    parts = [event.date, event.start_time, event.subject_acronym, event.group_code]
    if event.room:
        parts.append(event.room)
    return "-".join(parts) + f"@{_UID_DOMAIN}"


def _exam_uid(exam: ExamEntry) -> str:
    """El código de 8 dígitos ya es la clave estable de una asignatura (ver
    `parse/exams.py`); se añade la fecha por si una convocatoria futura
    repitiera examen de la misma asignatura en más de un día (no ocurre en
    la convocatoria de referencia, pero el UID no debe asumirlo). Prefijo
    `exam-` para que nunca pueda colisionar por casualidad con el UID de
    una clase (`_uid`, arriba), que nunca empieza así."""
    return f"exam-{exam.code}-{exam.date}@{_UID_DOMAIN}"


def build_ics(
    events: list[CalendarEvent],
    calendar_name: str = "Horario ESI (UCA)",
    exams: list[ExamEntry] | None = None,
) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//horario-uca//parser PDF -> ICS//ES")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)
    cal.add("x-wr-timezone", "Europe/Madrid")
    cal.add_component(_vtimezone_europe_madrid())

    dtstamp = datetime.now(timezone.utc)

    for event in sorted(events, key=lambda e: (e.date, e.start_time)):
        y, m, d = (int(p) for p in event.date.split("-"))
        vevent = Event()
        vevent.add("uid", _uid(event))
        vevent.add("dtstamp", dtstamp)
        vevent.add("summary", f"{event.subject_acronym} {event.group_code} ({event.group_type})")
        vevent.add("dtstart", datetime.combine(date(y, m, d), _parse_time(event.start_time), tzinfo=_TZ))
        vevent.add("dtend", datetime.combine(date(y, m, d), _parse_time(event.end_time), tzinfo=_TZ))
        if event.room:
            vevent.add("location", f"Aula {event.room}")

        description_lines = [
            f"Asignatura: {event.subject_name} ({event.subject_acronym})",
            f"Grupo: {event.group_code} — {event.group_type}",
        ]
        if event.curso:
            curso_line = f"Curso: {event.curso}"
            if event.semestre:
                curso_line += f" · Semestre {event.semestre}"
            if event.itinerario:
                curso_line += f" · {event.itinerario}"
            description_lines.append(curso_line)
        if event.moved_from:
            description_lines.append(f"Clase trasladada desde el {event.moved_from} (nota al pie del horario).")
        vevent.add("description", "\n".join(description_lines))

        cal.add_component(vevent)

    # Los exámenes son VEVENT propios, no CalendarEvent disfrazados — no
    # hay hora de fin real en el PDF de convocatoria, así que DTEND se
    # estima con una duración nominal configurable, y esa estimación se deja constando
    # en DESCRIPTION en vez de dejar que parezca una hora de fin oficial.
    for exam in sorted(exams or [], key=lambda e: (e.date, e.start_time)):
        y, m, d = (int(p) for p in exam.date.split("-"))
        start_dt = datetime.combine(date(y, m, d), _parse_time(exam.start_time), tzinfo=_TZ)
        vevent = Event()
        vevent.add("uid", _exam_uid(exam))
        vevent.add("dtstamp", dtstamp)
        vevent.add("summary", f"Examen: {exam.name}")
        vevent.add("dtstart", start_dt)
        vevent.add("dtend", start_dt + timedelta(minutes=EXAM_NOMINAL_DURATION_MINUTES))
        vevent.add("location", f"Aula {exam.room}" if exam.room else "Aula por confirmar")
        description_lines = [
            f"Examen de {exam.name} ({exam.acronym}), curso {exam.curso}º.",
            (
                f"La duración mostrada ({EXAM_NOMINAL_DURATION_MINUTES // 60}h) es una estimación — "
                "el calendario oficial de la convocatoria solo da la hora de inicio, no la de fin."
            ),
        ]
        vevent.add("description", "\n".join(description_lines))
        cal.add_component(vevent)

    return cal.to_ical()

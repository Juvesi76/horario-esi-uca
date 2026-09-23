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

"""Fase 4 para el calendario de exámenes: qué exámenes se añaden por
defecto a partir de la selección de clases del alumno, y los dos tipos de
choque que le son propios (examen↔examen, examen↔clase).
"""
from __future__ import annotations

from collections import defaultdict

from horario_uca.model import (
    CalendarEvent,
    ExamCalendar,
    ExamClassConflict,
    ExamConflict,
    ExamEntry,
    ParseInfo,
    SchedulePage,
    SubjectSelection,
)

# No hay hora de fin en el PDF de convocatoria — se usa una duración
# nominal para dos cosas distintas, cada una documentada donde se usa:
# (1) DTEND del VEVENT en el .ics (`render/ics.py`), con una nota en
# DESCRIPTION de que la duración real no figura en el calendario oficial;
# (2) decidir si un examen se solapa con una clase real
# (`find_exam_class_conflicts`, más abajo) — un choque examen↔examen NO
# usa esta constante (ver su propia regla: mismo día y distinta hora se
# avisa como POSIBLE sin más, la duración nominal no lo descarta ni lo
# confirma).
EXAM_NOMINAL_DURATION_MINUTES = 180

# Regla de qué exámenes se añaden por defecto, UNA por convocatoria — no
# cableada a "febrero". Verificado con datos reales solo para febrero: un
# examen de semestre 1 de una asignatura en la que el alumno tiene alguna
# clase seleccionada se añade automáticamente; uno de semestre 2 NUNCA se
# añade automáticamente (`ALG`, Álgebra, semestre 2, examen el 19/01/2027
# — antes de su primera clase de curso: esta convocatoria es para quien YA
# la cursó, no para quien empieza ahora). Una convocatoria sin regla
# verificada (junio, septiembre, o cualquier otra que no sea "febrero")
# NO añade nada automáticamente — más seguro no asumir por simetría con
# febrero que adivinar mal; la regla de cada convocatoria nueva se añade
# aquí solo después de comprobarse con datos reales de esa convocatoria.
_FEBRERO_RULE = "semestre_1_con_clases_seleccionadas"
CONVOCATORIA_RULES: dict[str, str] = {
    "febrero": _FEBRERO_RULE,
    # "junio": PENDIENTE DE VERIFICAR — no añadir hasta comprobar con un PDF real.
    # "septiembre": PENDIENTE DE VERIFICAR — no añadir hasta comprobar con un PDF real.
}


def _convocatoria_month_key(convocatoria: str | None) -> str | None:
    """`"FEBRERO DE 2027"` -> `"febrero"`. `None` si no hay convocatoria
    reconocible (cabecera no encontrada, ver `parse_exam_calendar`)."""
    if not convocatoria:
        return None
    first_word = convocatoria.strip().split()[0] if convocatoria.strip() else ""
    return first_word.lower() or None


def _codes_with_selected_classes(selections: list[SubjectSelection], pages: list[SchedulePage]) -> set[str]:
    """Códigos de 8 dígitos de toda asignatura con AL MENOS un grupo
    elegido en `selections` — resuelto contra la leyenda del propio
    horario (nunca contra el calendario de exámenes), porque el código es
    la clave estable y las siglas de una `SubjectSelection` son las del
    horario, no las del PDF de exámenes."""
    codes: set[str] = set()
    for selection in selections:
        if not selection.groups:
            continue
        for page in pages:
            if page.curso != selection.curso:
                continue
            if selection.itinerario is not None and page.itinerario != selection.itinerario:
                continue
            if not any(b.subject_acronym == selection.acronym for b in page.blocks):
                continue
            for entry in page.legend:
                if entry.acronym == selection.acronym:
                    codes.add(entry.code)
    return codes


def default_exam_codes(
    selections: list[SubjectSelection], pages: list[SchedulePage], exam_calendar: ExamCalendar
) -> tuple[set[str], list[ParseInfo]]:
    """Códigos de examen que se añaden por defecto a la selección del
    alumno. Devuelve también `ParseInfo` explicando por qué (regla
    aplicada, o regla no verificada para esta convocatoria)."""
    infos: list[ParseInfo] = []
    month_key = _convocatoria_month_key(exam_calendar.convocatoria)
    rule = CONVOCATORIA_RULES.get(month_key) if month_key else None

    if rule is None:
        infos.append(
            ParseInfo(
                code="regla_convocatoria_no_verificada",
                message=(
                    f"convocatoria {exam_calendar.convocatoria!r}: no hay regla de inclusión automática "
                    "verificada para esta convocatoria — ningún examen se añade por defecto, solo a mano"
                ),
                page_index=0,
            )
        )
        return set(), infos

    selected_codes = _codes_with_selected_classes(selections, pages)
    defaults = {
        e.code for e in exam_calendar.entries if e.semestre == 1 and e.code in selected_codes
    }
    excluded_sem2 = {
        e.code for e in exam_calendar.entries if e.semestre == 2 and e.code in selected_codes
    }
    for code in sorted(excluded_sem2):
        entry = next(e for e in exam_calendar.entries if e.code == code)
        infos.append(
            ParseInfo(
                code="examen_semestre_2_no_automatico",
                message=(
                    f"{entry.acronym} ({entry.name}): asignatura de semestre 2 con clases seleccionadas — "
                    "esta convocatoria es para quien ya cursó la asignatura, no se añade automáticamente"
                ),
                page_index=0,
            )
        )
    return defaults, infos


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def find_exam_conflicts(entries: list[ExamEntry]) -> list[ExamConflict]:
    """Choque examen↔examen: mismo día. `certain=True` si además comparten
    hora de inicio exacta; si no, `certain=False` (posible, no confirmable
    sin hora de fin)."""
    by_date: dict[str, list[ExamEntry]] = defaultdict(list)
    for e in entries:
        by_date[e.date].append(e)

    conflicts: list[ExamConflict] = []
    for exam_date in sorted(by_date):
        day_entries = sorted(by_date[exam_date], key=lambda e: e.start_time)
        for i in range(len(day_entries)):
            for j in range(i + 1, len(day_entries)):
                a, b = day_entries[i], day_entries[j]
                if a.code == b.code:
                    continue
                conflicts.append(
                    ExamConflict(date=exam_date, exam_a=a, exam_b=b, certain=a.start_time == b.start_time)
                )
    return conflicts


def find_exam_class_conflicts(
    entries: list[ExamEntry], events: list[CalendarEvent], nominal_duration_minutes: int = EXAM_NOMINAL_DURATION_MINUTES
) -> list[ExamClassConflict]:
    """Choque examen↔clase: mismo día, con el examen estimado usando la
    duración nominal (no hay hora de fin real que comparar)."""
    by_date: dict[str, list[CalendarEvent]] = defaultdict(list)
    for ev in events:
        by_date[ev.date].append(ev)

    conflicts: list[ExamClassConflict] = []
    for exam in entries:
        exam_start = _to_minutes(exam.start_time)
        exam_end = exam_start + nominal_duration_minutes
        for event in by_date.get(exam.date, []):
            ev_start, ev_end = _to_minutes(event.start_time), _to_minutes(event.end_time)
            if exam_start < ev_end and ev_start < exam_end:
                conflicts.append(ExamClassConflict(exam=exam, event=event))
    return conflicts

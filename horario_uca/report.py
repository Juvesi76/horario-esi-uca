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

"""Informe de confirmación previo al render: lo que se va a generar, en texto
legible, antes de escribir HTML/ICS. Por defecto solo `ParseWarning`
(anomalías que podrían ser un fallo real de extracción); `ParseInfo`
(comprobaciones con el resultado esperado, no anomalías) solo con
`verbose=True`.
"""
from __future__ import annotations

from collections import Counter

from horario_uca.model import CalendarEvent, ParseWarning, SchedulePage, SubjectSelection
from horario_uca.select import find_conflicts, validate_selection


def build_confirmation_report(
    selections: list[SubjectSelection],
    pages: list[SchedulePage],
    events: list[CalendarEvent],
    verbose: bool = False,
) -> str:
    lines: list[str] = []

    lines.append("=== Selección ===")
    for s in selections:
        itin = f" ({s.itinerario})" if s.itinerario else ""
        grupos = "solo examen (por libre)" if s.solo_examen else ", ".join(s.groups)
        lines.append(f"  {s.acronym} · {s.curso}{itin}: {grupos}")

    validation_warnings: list[ParseWarning] = []
    for s in selections:
        validation_warnings.extend(validate_selection(s, pages))

    lines.append("")
    lines.append("=== Validación de la selección ===")
    if not validation_warnings:
        lines.append("  sin avisos")
    for w in validation_warnings:
        lines.append(f"  [{w.code}] {w.message}")

    conflicts = find_conflicts(events)
    lines.append("")
    lines.append("=== CONFLICTOS DE HORARIO EN LA SELECCIÓN (combinación de grupos incompatible) ===")
    if not conflicts:
        lines.append("  sin conflictos: ningún par de eventos de la selección se solapa en horas")
    else:
        # `find_conflicts` da un `ScheduleConflict` por CADA PAR de eventos
        # que se solapan — si tres o más grupos elegidos coinciden el mismo
        # día, ese único día aporta varios pares, no una fecha extra. El
        # número accionable para quien lee el informe es "en cuántos días
        # tiene un problema", no cuántos pares hay en los datos (verificado
        # con datos reales: el fixture del repetidor da 36 pares pero solo
        # 32 fechas distintas). El detalle por par no se
        # pierde, se anida bajo su fecha.
        by_date: dict[str, list] = {}
        for c in conflicts:
            by_date.setdefault(c.date, []).append(c)

        def _pair_line(c) -> str:
            a, b = c.event_a, c.event_b
            a_room = f", aula {a.room}" if a.room else ""
            b_room = f", aula {b.room}" if b.room else ""
            cross = a.curso != b.curso
            a_curso = f" ({a.curso})" if cross else ""
            b_curso = f" ({b.curso})" if cross else ""
            return (
                f"      {a.subject_acronym} {a.group_code}{a_curso} ({a.start_time}-{a.end_time}{a_room}) "
                f"choca con {b.subject_acronym} {b.group_code}{b_curso} ({b.start_time}-{b.end_time}{b_room})"
            )

        def _date_block(dates: dict[str, list]) -> list[str]:
            block = []
            for date in sorted(dates):
                block.append(f"    {date}:")
                block.extend(_pair_line(c) for c in dates[date])
            return block

        # Una fecha va a "entre cursos distintos" si ALGUNO de sus pares lo
        # es — es el caso más difícil de resolver de los dos, y esconderlo
        # bajo "mismo curso" porque esa fecha también tuviera un par más
        # sencillo sería lo contrario de útil.
        same_course_dates = {
            d: pairs for d, pairs in by_date.items()
            if all(c.event_a.curso == c.event_b.curso for c in pairs)
        }
        cross_course_dates = {
            d: pairs for d, pairs in by_date.items()
            if any(c.event_a.curso != c.event_b.curso for c in pairs)
        }

        if same_course_dates:
            fecha_word = "1 fecha" if len(same_course_dates) == 1 else f"{len(same_course_dates)} fechas"
            lines.append(
                f"  {fecha_word} dentro del mismo curso — se resuelven eligiendo otro grupo:"
            )
            lines.extend(_date_block(same_course_dates))

        if cross_course_dates:
            fecha_word = "1 fecha" if len(cross_course_dates) == 1 else f"{len(cross_course_dates)} fechas"
            lines.append(
                f"  {fecha_word} entre cursos distintos — al cursar asignaturas de más de un "
                "curso a la vez puede que no exista ninguna combinación de grupos sin choques:"
            )
            lines.extend(_date_block(cross_course_dates))

    lines.append("")
    lines.append("=== Resumen de eventos ===")
    lines.append(f"  total: {len(events)}")
    if events:
        dates = sorted(e.date for e in events)
        lines.append(f"  rango de fechas: {dates[0]} a {dates[-1]}")
        by_subject = Counter(e.subject_acronym for e in events)
        for acronym, count in by_subject.most_common():
            lines.append(f"  {acronym}: {count} eventos")
        moved = sum(1 for e in events if e.moved_from is not None)
        lines.append(f"  trasladados por nota al pie: {moved}")

    selected_acronyms = {s.acronym for s in selections}
    relevant_pages = [
        p for p in pages if any(b.subject_acronym in selected_acronyms for b in p.blocks)
    ]

    lines.append("")
    lines.append("=== Avisos de las páginas de origen (ParseWarning) ===")
    any_warning = False
    for page in relevant_pages:
        if not page.warnings:
            continue
        any_warning = True
        lines.append(f"  página {page.page_index} ({page.curso}, semestre {page.semestre}):")
        for w in page.warnings:
            lines.append(f"    [{w.code}] {w.message}")
    if not any_warning:
        lines.append("  sin avisos")

    if verbose:
        lines.append("")
        lines.append("=== Info (--verbose): comprobaciones OK, no anomalías ===")
        any_info = False
        for page in relevant_pages:
            if not page.infos:
                continue
            any_info = True
            lines.append(f"  página {page.page_index} ({page.curso}, semestre {page.semestre}):")
            for i in page.infos:
                lines.append(f"    [{i.code}] {i.message}")
        if not any_info:
            lines.append("  sin infos")

    return "\n".join(lines)

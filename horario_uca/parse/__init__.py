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

from horario_uca.extract import RawPage
from horario_uca.model import ClassBlock, ParseInfo, ParseWarning, ScheduleNote, SchedulePage, SubjectLegendEntry
from horario_uca.parse.calendar import build_calendar_weeks, parse_calendar_day_status
from horario_uca.parse.header import HeaderInfo, parse_header
from horario_uca.parse.legend import parse_legend
from horario_uca.parse.notes import parse_notes, validate_notes_against_calendar
from horario_uca.parse.session import build_blocks, flatten_sessions, validate_weeks_active_lectivo

MIN_LEGEND_ENTRIES = 4


def _check_non_empty(
    page_index: int,
    semestre: int | None,
    legend: list[SubjectLegendEntry],
    blocks: list[ClassBlock],
    calendar_weeks: list,
    notes: list[ScheduleNote],
) -> list[ParseWarning]:
    """[3] Regla estructural: una colección vacía no es "nada que reportar",
    es sospechosa por sí misma. Cada módulo de parse/ declara su expectativa
    mínima aquí, con código propio, aunque no haya habido ninguna excepción.
    Se coló en silencio tres veces en esta fase (unparsed, notes, calendar)
    antes de escribirse como regla explícita: una colección vacía no es
    éxito por sí sola, cada módulo declara qué mínimo espera.
    """
    warnings: list[ParseWarning] = []

    expected_weeks = {1: 16, 2: 15}.get(semestre)
    if expected_weeks is not None and len(calendar_weeks) != expected_weeks:
        warnings.append(
            ParseWarning(
                code="calendario_vacio",
                message=f"se esperaban {expected_weeks} semanas (semestre {semestre}), se obtuvieron {len(calendar_weeks)}",
                page_index=page_index,
            )
        )

    # Regla dura, separada de `calendario_vacio`: un desajuste de 14 vs 15
    # semanas es una anomalía menor, pero una página con bloques reales y
    # CERO semanas de calendario significa "hay asignaturas cuyas clases no
    # se pueden fechar" — el mismo fallo silencioso (bloque real, cero
    # eventos, sin ningún aviso) que ya ha costado varias rondas de este
    # proyecto en otros puntos del pipeline. No se puede dejar que quede
    # implícito dentro de `calendario_vacio`, que también dispara por
    # desajustes mucho menos graves.
    if len(blocks) >= 1 and len(calendar_weeks) == 0:
        warnings.append(
            ParseWarning(
                code="bloques_sin_calendario_para_fechar",
                message=(
                    f"{len(blocks)} ClassBlock en la página pero 0 semanas de calendario — "
                    "ninguno de esos bloques puede generar una fecha real"
                ),
                page_index=page_index,
            )
        )

    if len(notes) < 1:
        warnings.append(
            ParseWarning(code="notas_vacias", message="0 notas al pie en la página", page_index=page_index)
        )

    if len(legend) < MIN_LEGEND_ENTRIES:
        warnings.append(
            ParseWarning(
                code="leyenda_vacia",
                message=f"solo {len(legend)} asignaturas en la leyenda, se esperaban >= {MIN_LEGEND_ENTRIES}",
                page_index=page_index,
            )
        )

    if len(blocks) < 1:
        warnings.append(
            ParseWarning(code="pagina_sin_bloques", message="0 ClassBlock en la página", page_index=page_index)
        )

    return warnings


def _check_legend_block_consistency(
    page_index: int, legend: list[SubjectLegendEntry], blocks: list[ClassBlock]
) -> tuple[list[ParseWarning], list[ParseInfo]]:
    """Cruce interno DENTRO de la misma página, barato y ya disponible sin
    ningún documento externo — habría cazado el bug de `ApC` (acrónimo con
    minúscula que `LEGEND_RE` no admitía) desde el
    primer día: `ApC` tenía 3 `ClassBlock` reales con acrónimo `"ApC"` Y
    0 entradas en `page.legend` de esa misma página, y nada lo señalaba
    hasta que llegó el PDF de exámenes y lo contradijo por casualidad.

    Un `ClassBlock` sin entrada en la leyenda de su propia página es
    `ParseWarning` — no hay ninguna razón legítima para que exista un
    bloque real de una asignatura que la leyenda de esa página no
    menciona en absoluto. Una entrada de leyenda sin ningún `ClassBlock`
    es `ParseInfo`, no warning: puede ser legítimo (el TFG está en la
    leyenda de varias páginas sin tener bloques de clase — no se examina,
    pero se lista igual, ver "Calendario de exámenes")."""
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []
    legend_acronyms = {e.acronym for e in legend}
    block_acronyms = {b.subject_acronym for b in blocks}

    for acronym in sorted(block_acronyms - legend_acronyms):
        warnings.append(
            ParseWarning(
                code="bloque_sin_leyenda",
                message=f"{acronym!r} tiene ClassBlock reales en la página pero ninguna entrada en page.legend",
                page_index=page_index,
            )
        )
    for acronym in sorted(legend_acronyms - block_acronyms):
        infos.append(
            ParseInfo(
                code="leyenda_sin_bloque",
                message=(
                    f"{acronym!r} está en la leyenda pero no tiene ningún ClassBlock en esta página "
                    "(puede ser legítimo, p.ej. TFG)"
                ),
                page_index=page_index,
            )
        )
    return warnings, infos


def parse_page(raw: RawPage) -> SchedulePage:
    header, header_warnings = parse_header(raw)
    legend = parse_legend(raw)
    blocks, unparsed, block_warnings, block_infos = build_blocks(raw, header)
    notes, note_warnings = parse_notes(raw)

    if header.academic_year is not None:
        day_status, calendar_warnings, calendar_infos = parse_calendar_day_status(raw, header.academic_year)
    else:
        day_status, calendar_warnings, calendar_infos = {}, [], []
    calendar_weeks = build_calendar_weeks(day_status)
    cross_check_warnings, cross_check_infos = validate_weeks_active_lectivo(blocks, day_status, notes, raw.page_index)
    note_cross_check_warnings, note_cross_check_infos = validate_notes_against_calendar(
        notes, day_status, raw.page_index
    )
    completeness_warnings = _check_non_empty(raw.page_index, header.semestre, legend, blocks, calendar_weeks, notes)
    legend_block_warnings, legend_block_infos = _check_legend_block_consistency(raw.page_index, legend, blocks)

    return SchedulePage(
        page_index=raw.page_index,
        academic_year=header.academic_year,
        curso=header.curso,
        semestre=header.semestre,
        itinerario=header.itinerario,
        generation_timestamp=header.generation_timestamp.isoformat() if header.generation_timestamp else None,
        approval_date=header.approval_date.isoformat() if header.approval_date else None,
        legend=legend,
        blocks=blocks,
        calendar=calendar_weeks,
        notes=notes,
        unparsed=unparsed,
        warnings=(
            header_warnings
            + block_warnings
            + calendar_warnings
            + cross_check_warnings
            + note_warnings
            + note_cross_check_warnings
            + completeness_warnings
            + legend_block_warnings
        ),
        infos=block_infos + calendar_infos + cross_check_infos + note_cross_check_infos + legend_block_infos,
    )


__all__ = [
    "HeaderInfo",
    "parse_header",
    "parse_legend",
    "parse_notes",
    "build_blocks",
    "flatten_sessions",
    "parse_page",
]

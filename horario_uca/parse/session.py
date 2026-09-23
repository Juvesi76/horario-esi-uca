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

"""Construcción de ClassBlock/Session a partir de los rectángulos de clase.

Reglas de emparejamiento cabecera↔rectángulo, fusión de líneas de grupo
partidas y semántica de color de la tira de semanas.
"""
from __future__ import annotations

import re

from horario_uca.extract import RawDrawing, RawPage, RawSpan
from horario_uca.model import (
    ClassBlock,
    DayLectivoStatus,
    DayStatus,
    DIA_NOMBRE,
    GROUP_TYPES,
    ParseInfo,
    ParseWarning,
    ScheduleNote,
    Session,
    SubjectLegendEntry,
    UnparsedBlock,
    WeekGroup,
)
from horario_uca.parse.grid import day_of_week_for_rect, find_class_rects, find_day_columns
from horario_uca.parse.header import HeaderInfo

TIME_RE = re.compile(r"^(\d{2}:\d{2}) a (\d{2}:\d{2})$")
ROOM_RE = re.compile(r"^Aula\s+(.+)$")
GROUP_LINE_RE = re.compile(r"^([A-Z])(\d+)\s*-\s*(.+)$")
WEEK_STRIP_TEXT_RE = re.compile(r"^[\d\s]+$")
DARK = (0, 0, 0)

# Tamaños de fuente calibrados sobre la página 0 (referencia: cabecera de
# hora a 4.513pt). Verificado que TODA la tipografía de un bloque de clase
# escala junto con el resto de la página según cuántas subcolumnas tenga la
# rejilla (de ~3.6pt a ~7pt vistos en las 24 páginas) — usar estos valores
# como puntos absolutos rompe en cualquier página que no escale igual que la
# 0. Se guardan como RATIO sobre el tamaño de la cabecera de hora de ESA
# página (detectada por contenido, `HH:MM a HH:MM`, inmune al escalado) y se
# reconstruyen en `_size_bands()` por página.
_REF_TIME_SIZE = 4.513
ACRONYM_SIZE_RATIO = (4.8 / _REF_TIME_SIZE, 5.3 / _REF_TIME_SIZE)
GROUP_LINE_SIZE_RATIO = (3.7 / _REF_TIME_SIZE, 4.3 / _REF_TIME_SIZE)
WEEK_STRIP_SIZE_RATIO = (2.7 / _REF_TIME_SIZE, 3.3 / _REF_TIME_SIZE)

# Igual que los tres tamaños de arriba: cualquier tolerancia de POSICIÓN
# derivada de esta tipografía (no solo el tamaño de fuente en sí) escala con
# la página y tiene que expresarse como ratio sobre `time_size`, nunca en
# puntos absolutos — encontrado auditando el resto del parser después de que
# el mismo patrón (constante fija sin convertir) dejara 2 páginas de otro
# grado con 0 `CalendarWeek` en `calendar.py`. Estas cuatro NO habían
# fallado todavía contra ninguna página comprobada, pero tienen la misma
# forma exacta del bug ya encontrado tres veces — no hace falta esperar a
# la cuarta.
_HEADER_MATCH_Y_RATIO = (-1.0 / _REF_TIME_SIZE, 5.0 / _REF_TIME_SIZE)
_ROOM_SAME_ROW_Y_RATIO = 1.0 / _REF_TIME_SIZE
_GROUP_LINE_MERGE_Y_GAP_RATIO = 8.0 / _REF_TIME_SIZE
_GROUP_LINE_MERGE_X_TOL_RATIO = 6.0 / _REF_TIME_SIZE
PAD = 1.0


def _page_time_header_size(raw: RawPage) -> float:
    sizes = [s.size for s in raw.spans if TIME_RE.match(s.text.strip())]
    return sizes[0] if sizes else _REF_TIME_SIZE


def _size_bands(raw: RawPage) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    time_size = _page_time_header_size(raw)
    acronym = (ACRONYM_SIZE_RATIO[0] * time_size, ACRONYM_SIZE_RATIO[1] * time_size)
    group_line = (GROUP_LINE_SIZE_RATIO[0] * time_size, GROUP_LINE_SIZE_RATIO[1] * time_size)
    week_strip = (WEEK_STRIP_SIZE_RATIO[0] * time_size, WEEK_STRIP_SIZE_RATIO[1] * time_size)
    return acronym, group_line, week_strip


def _spans_inside(rect: tuple[float, float, float, float], spans: list[RawSpan]) -> list[RawSpan]:
    x0, y0, x1, y1 = rect
    return [
        s
        for s in spans
        if s.bbox[0] >= x0 - PAD and s.bbox[2] <= x1 + PAD and s.bbox[1] >= y0 - PAD and s.bbox[3] <= y1 + PAD
    ]


def _match_header(rect: tuple[float, float, float, float], spans: list[RawSpan], time_size: float) -> RawSpan | None:
    # La cabecera NO está por encima del rectángulo: su y0 cae ~1-1.5pt DENTRO
    # de él, pegada al borde superior (verificado: header.y0 - rect.y0 ≈ 1.2 a
    # 1.5 en varios bloques, a time_size~4.5pt). La regla es "la cabecera más
    # pegada al borde superior del rectángulo, con rango X solapado", no "la
    # más cercana por encima" en sentido estricto de y1 <= rect.y0. La
    # ventana es ratio sobre `time_size`, no puntos absolutos — ver
    # `_HEADER_MATCH_Y_RATIO` más arriba.
    x0, y0, x1, _ = rect
    y_lo, y_hi = _HEADER_MATCH_Y_RATIO[0] * time_size, _HEADER_MATCH_Y_RATIO[1] * time_size
    candidates = [
        s
        for s in spans
        if TIME_RE.match(s.text.strip())
        and s.bbox[2] > x0
        and s.bbox[0] < x1
        and y_lo <= (s.bbox[1] - y0) <= y_hi
    ]
    if not candidates:
        return None

    def overlap(s: RawSpan) -> float:
        return min(s.bbox[2], x1) - max(s.bbox[0], x0)

    min_dy = min(abs(s.bbox[1] - y0) for s in candidates)
    top = [s for s in candidates if abs(s.bbox[1] - y0) - min_dy < 0.01]
    return max(top, key=overlap)


def _match_room(header: RawSpan, spans: list[RawSpan], time_size: float) -> str | None:
    """Devuelve solo el identificador de aula (`"D01"`), sin el prefijo
    "Aula" — decisión explícita para no arrastrar el prefijo a Session/ICS y
    tener que quitarlo dos veces. La capa de render añade "Aula " al mostrar.
    """
    same_row_tol = _ROOM_SAME_ROW_Y_RATIO * time_size
    same_row = [s for s in spans if abs(s.bbox[1] - header.bbox[1]) < same_row_tol and s.bbox[0] > header.bbox[0]]
    for s in same_row:
        m = ROOM_RE.match(s.text.strip())
        if m:
            return m.group(1)
    return None


def _merge_group_lines(candidates: list[RawSpan], time_size: float) -> list[tuple[float, str]]:
    y_gap_max = _GROUP_LINE_MERGE_Y_GAP_RATIO * time_size
    x_tol = _GROUP_LINE_MERGE_X_TOL_RATIO * time_size
    candidates = sorted(candidates, key=lambda s: s.bbox[1])
    merged: list[tuple[float, str]] = []
    i = 0
    while i < len(candidates):
        span = candidates[i]
        text = span.text.strip()
        m = GROUP_LINE_RE.match(text)
        if m and i + 1 < len(candidates):
            rest = m.group(3)
            is_prefix = any(
                rest != full and full.startswith(rest) for full in GROUP_TYPES.values()
            )
            nxt = candidates[i + 1]
            gap = nxt.bbox[1] - span.bbox[1]
            same_x = abs(nxt.bbox[0] - span.bbox[0]) < x_tol
            is_strip = WEEK_STRIP_TEXT_RE.match(nxt.text.strip()) is not None
            if is_prefix and 0 < gap < y_gap_max and same_x and not is_strip:
                sep = "" if text.endswith("-") else " "
                merged.append((span.bbox[1], text + sep + nxt.text.strip()))
                i += 2
                continue
        merged.append((span.bbox[1], text))
        i += 1
    return merged


def _weeks_active(strip_spans: list[RawSpan]) -> list[int]:
    weeks: list[int] = []
    for s in sorted(strip_spans, key=lambda s: s.bbox[0]):
        if s.color != DARK:
            continue
        for n in s.text.split():
            if n.isdigit():
                weeks.append(int(n))
    return sorted(weeks)


def _strip_total_count(strip_spans: list[RawSpan]) -> int:
    return sum(1 for s in strip_spans for n in s.text.split() if n.isdigit())


def build_blocks(
    raw: RawPage, header: HeaderInfo
) -> tuple[list[ClassBlock], list[UnparsedBlock], list[ParseWarning], list[ParseInfo]]:
    day_columns, warnings = find_day_columns(raw)
    infos: list[ParseInfo] = []
    class_rects = find_class_rects(raw)
    blocks: list[ClassBlock] = []
    unparsed: list[UnparsedBlock] = []
    expected_weeks = {1: 16, 2: 15}.get(header.semestre)
    ACRONYM_SIZE, GROUP_LINE_SIZE, WEEK_STRIP_SIZE = _size_bands(raw)
    time_size = _page_time_header_size(raw)

    for drawing in class_rects:
        rect = drawing.rect
        inside = _spans_inside(rect, raw.spans)
        raw_text = " | ".join(s.text.strip() for s in inside if s.text.strip())

        day = day_of_week_for_rect(rect, day_columns)
        if day is None:
            unparsed.append(UnparsedBlock(bbox=rect, raw_text=raw_text, reason="columna_sin_dia"))
            continue

        header_span = _match_header(rect, raw.spans, time_size)
        if header_span is None:
            unparsed.append(UnparsedBlock(bbox=rect, raw_text=raw_text, reason="cabecera_no_encontrada"))
            continue

        m = TIME_RE.match(header_span.text.strip())
        start_time, end_time = m.group(1), m.group(2)
        room = _match_room(header_span, raw.spans, time_size)

        acronym_candidates = [s for s in inside if ACRONYM_SIZE[0] <= s.size <= ACRONYM_SIZE[1]]
        if not acronym_candidates:
            unparsed.append(UnparsedBlock(bbox=rect, raw_text=raw_text, reason="acronimo_no_encontrado"))
            continue
        acronym = min(acronym_candidates, key=lambda s: s.bbox[1]).text.strip()

        group_line_candidates = [s for s in inside if GROUP_LINE_SIZE[0] <= s.size <= GROUP_LINE_SIZE[1]]
        merged_lines = _merge_group_lines(group_line_candidates, time_size)

        strip_candidates = [s for s in inside if WEEK_STRIP_SIZE[0] <= s.size <= WEEK_STRIP_SIZE[1]]

        groups: list[WeekGroup] = []
        for idx, (y0, text) in enumerate(merged_lines):
            gm = GROUP_LINE_RE.match(text)
            if not gm:
                warnings.append(
                    ParseWarning(
                        code="grupo_sin_match_group_types",
                        message=f"línea de grupo no reconocida: {text!r}",
                        page_index=raw.page_index,
                        bbox=rect,
                    )
                )
                continue
            letter, num, rest = gm.groups()
            group_code = f"{letter}{num}"
            if rest != GROUP_TYPES.get(letter):
                warnings.append(
                    ParseWarning(
                        code="grupo_letra_no_coincide_tipo",
                        message=f"grupo {group_code}: texto {rest!r} no coincide con GROUP_TYPES[{letter!r}]={GROUP_TYPES.get(letter)!r}",
                        page_index=raw.page_index,
                        bbox=rect,
                    )
                )

            next_y = min((y for y, _ in merged_lines if y > y0 + 1), default=rect[3])
            strip_spans = [s for s in strip_candidates if y0 < s.bbox[1] < next_y]

            count = _strip_total_count(strip_spans)
            if expected_weeks is not None and count != expected_weeks:
                warnings.append(
                    ParseWarning(
                        code="tira_semanas_incompleta",
                        message=f"grupo {group_code}: {count} números en la tira, se esperaban {expected_weeks}",
                        page_index=raw.page_index,
                        bbox=rect,
                    )
                )

            weeks_active = _weeks_active(strip_spans)
            week_ceiling = expected_weeks if expected_weeks is not None else 16
            if any(w < 1 or w > week_ceiling for w in weeks_active):
                warnings.append(
                    ParseWarning(
                        code="semana_fuera_de_rango",
                        message=f"grupo {group_code}: semanas fuera de 1..{week_ceiling}: {weeks_active}",
                        page_index=raw.page_index,
                        bbox=rect,
                    )
                )
            if not weeks_active:
                warnings.append(
                    ParseWarning(
                        code="bloque_sin_semanas_activas",
                        message=f"grupo {group_code}: ninguna semana activa",
                        page_index=raw.page_index,
                        bbox=rect,
                    )
                )

            groups.append(WeekGroup(group_code=group_code, group_type=rest, weeks_active=weeks_active))

        if not groups:
            unparsed.append(UnparsedBlock(bbox=rect, raw_text=raw_text, reason="grupo_sin_match_group_types"))
            continue

        start_min = int(start_time[:2]) * 60 + int(start_time[3:])
        end_min = int(end_time[:2]) * 60 + int(end_time[3:])
        duration = end_min - start_min
        # Umbral recalibrado (5ª revisión): 60-180min asumía que las clases
        # de teoría/problemas de páginas simples eran representativas de
        # todo el PDF. Contrastado con el PNG de depuración en páginas de
        # itinerario (3º/4º): sesiones de "Clases de teoría" de hasta 300min
        # (`PNET A1`, `SIE A1`, ambas de un solo rectángulo limpio, sin
        # cruce de cabecera) son legítimas — varias cabeceras anidadas de
        # `08:30`-`09:00`-... con semanas distintas dentro del mismo rect,
        # el patrón IG/IP de p.0 a mayor escala.
        #
        # Dos niveles, no uno (6ª revisión): un umbral fijado justo en el
        # máximo observado (300) no puede dispararse nunca sobre este PDF y
        # pierde la constancia de qué bloques son largos. `duracion_larga`
        # (info, >3h) registra los 21 casos vistos sin ensuciar el informe;
        # `duracion_sospechosa` (warning, >300min) sigue siendo la anomalía
        # real — un bloque de 310min, por ejemplo, sí dispararía esto.
        if duration <= 0 or duration < 60 or duration > 300:
            warnings.append(
                ParseWarning(
                    code="duracion_sospechosa",
                    message=f"{acronym} {start_time}-{end_time}: duración {duration} min",
                    page_index=raw.page_index,
                    bbox=rect,
                )
            )
        elif duration > 180:
            infos.append(
                ParseInfo(
                    code="duracion_larga",
                    message=f"{acronym} {start_time}-{end_time}: duración {duration} min (> 3h)",
                    page_index=raw.page_index,
                    bbox=rect,
                )
            )

        blocks.append(
            ClassBlock(
                subject_acronym=acronym,
                day_of_week=day,
                start_time=start_time,
                end_time=end_time,
                room=room,
                groups=groups,
                bbox=rect,
            )
        )

    return blocks, unparsed, warnings, infos


def flatten_sessions(header: HeaderInfo, blocks: list[ClassBlock]) -> list[Session]:
    sessions: list[Session] = []
    for block in blocks:
        for group in block.groups:
            sessions.append(
                Session(
                    asignatura=block.subject_acronym,
                    curso=header.curso,
                    semestre=header.semestre,
                    itinerario=header.itinerario,
                    grupo=group.group_code,
                    tipo=group.group_type,
                    dia=DIA_NOMBRE[block.day_of_week],
                    inicio=block.start_time,
                    fin=block.end_time,
                    aula=block.room,
                    semanas=group.weeks_active,
                )
            )
    return sessions


def validate_weeks_active_lectivo(
    blocks: list[ClassBlock],
    day_status: dict[tuple[int, int], DayStatus],
    notes: list[ScheduleNote],
    page_index: int,
) -> tuple[list[ParseWarning], list[ParseInfo]]:
    """[B] Para cada (ClassBlock, semana activa), comprueba contra el
    minicalendario que ese día de la semana es LECTIVO. Verificado en las 24
    páginas: las 138 apariciones observadas correspondían, una a una, al día
    de ORIGEN de alguna nota de traslado de su propia página — el
    comportamiento esperado de la regla de la Fase 3 (semana de origen), no
    una anomalía. Por eso solo sube a `ParseWarning` cuando NO hay ninguna
    nota de la página que explique la combinación (semana, día); si la hay,
    es `ParseInfo` — confirma la regla, no la contradice.
    """
    note_origins = {(n.from_week, n.from_weekday) for n in notes}
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []
    for block in blocks:
        for group in block.groups:
            for week in group.weeks_active:
                entry = day_status.get((week, block.day_of_week))
                if entry is None or entry.status == DayLectivoStatus.LECTIVO:
                    continue
                message = (
                    f"{block.subject_acronym} {group.group_code} "
                    f"({DIA_NOMBRE[block.day_of_week]} {block.start_time}, {entry.date}): "
                    f"semana {week} activa pero el día está marcado {entry.status.value}"
                )
                explained = (week, block.day_of_week) in note_origins
                target_cls = ParseInfo if explained else ParseWarning
                target_list = infos if explained else warnings
                target_list.append(
                    target_cls(
                        code="semana_activa_en_dia_no_lectivo",
                        message=message,
                        page_index=page_index,
                        bbox=block.bbox,
                    )
                )
    return warnings, infos

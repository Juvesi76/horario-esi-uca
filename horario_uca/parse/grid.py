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

"""Columnas de día y rectángulos de clase.

No existen líneas verticales que delimiten los días (verificado: 0
encontradas en toda la rejilla). Las columnas de día se derivan agrupando los
rectángulos de fondo grises en cadenas contiguas por fila y emparejando cada
cadena con la etiqueta de día ("Lunes".."Viernes") cuyo centro coincide, con
tolerancia.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from horario_uca.extract import RawDrawing, RawPage
from horario_uca.model import BBox, DIA_NOMBRE, ParseWarning

BG_FILL = (241, 242, 242)
EXCLUDED_FILLS = {(241, 242, 242), (0, 0, 0), (120, 127, 135), (255, 255, 255)}
DAY_LABELS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
DAY_CENTER_TOLERANCE = 2.0
CHAIN_GAP_TOLERANCE = 0.75
MIN_CLASS_RECT_SIZE = (20.0, 8.0)


def _day_label_centers(raw: RawPage) -> dict[int, float]:
    centers: dict[int, float] = {}
    for span in raw.spans:
        text = span.text.strip()
        if text in DAY_LABELS:
            x0, _, x1, _ = span.bbox
            centers[DAY_LABELS.index(text)] = (x0 + x1) / 2
    return centers


def _chains_for_row(rects: list[RawDrawing]) -> list[tuple[float, float]]:
    rects = sorted(rects, key=lambda d: d.rect[0])
    chains: list[list[RawDrawing]] = [[rects[0]]]
    for d in rects[1:]:
        if d.rect[0] - chains[-1][-1].rect[2] < CHAIN_GAP_TOLERANCE:
            chains[-1].append(d)
        else:
            chains.append([d])
    return [(c[0].rect[0], c[-1].rect[2]) for c in chains]


def find_day_columns(raw: RawPage) -> tuple[dict[int, tuple[float, float]], list[ParseWarning]]:
    warnings: list[ParseWarning] = []
    centers = _day_label_centers(raw)

    rows: dict[tuple[float, float], list[RawDrawing]] = defaultdict(list)
    for d in raw.drawings:
        if d.fill == BG_FILL:
            key = (round(d.rect[1], 1), round(d.rect[3], 1))
            rows[key].append(d)

    observed: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row_rects in rows.values():
        if not row_rects:
            continue
        for x0, x1 in _chains_for_row(row_rects):
            center = (x0 + x1) / 2
            match = min(
                (day for day in centers if abs(centers[day] - center) <= DAY_CENTER_TOLERANCE),
                key=lambda day: abs(centers[day] - center),
                default=None,
            )
            if match is None:
                warnings.append(
                    ParseWarning(
                        code="columna_sin_dia",
                        message=f"cadena de fondo x=({x0:.1f},{x1:.1f}) no coincide con ninguna etiqueta de día",
                        page_index=raw.page_index,
                        bbox=(x0, row_rects[0].rect[1], x1, row_rects[0].rect[3]),
                    )
                )
                continue
            observed[match].append((round(x0, 1), round(x1, 1)))

    day_columns: dict[int, tuple[float, float]] = {}
    for day, ranges in observed.items():
        canonical, _ = Counter(ranges).most_common(1)[0]
        day_columns[day] = canonical
        distinct = {r for r in ranges if abs(r[0] - canonical[0]) > 1.0 or abs(r[1] - canonical[1]) > 1.0}
        if distinct:
            warnings.append(
                ParseWarning(
                    code="columna_dia_ancho_inconsistente",
                    message=f"{DIA_NOMBRE[day]}: rango canónico {canonical}, también visto {sorted(distinct)}",
                    page_index=raw.page_index,
                )
            )

    return day_columns, warnings


def _is_background_or_border(fill: tuple[int, int, int] | None) -> bool:
    return fill is None or fill in EXCLUDED_FILLS


def find_class_rects(raw: RawPage) -> list[RawDrawing]:
    candidates = [
        d
        for d in raw.drawings
        if not _is_background_or_border(d.fill)
        and (d.rect[2] - d.rect[0]) >= MIN_CLASS_RECT_SIZE[0]
        and (d.rect[3] - d.rect[1]) >= MIN_CLASS_RECT_SIZE[1]
    ]

    deduped: list[RawDrawing] = []
    for d in candidates:
        if any(
            d.fill == other.fill and all(abs(a - b) < 1.0 for a, b in zip(d.rect, other.rect))
            for other in deduped
        ):
            continue
        deduped.append(d)
    return deduped


def day_of_week_for_rect(
    rect: BBox, day_columns: dict[int, tuple[float, float]]
) -> int | None:
    center = (rect[0] + rect[2]) / 2
    for day, (x0, x1) in day_columns.items():
        if x0 - DAY_CENTER_TOLERANCE <= center <= x1 + DAY_CENTER_TOLERANCE:
            return day
    return None

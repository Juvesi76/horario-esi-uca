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

"""Lectura geométrica cruda de una página del PDF, sin interpretación
semántica. El parseo por posición (bbox, color) en vez de por orden de
texto plano es deliberado: qué semanas de una tira están activas se
codifica solo en el color de cada span, no en el texto, y a qué
grupo/día pertenece un rectángulo se codifica en su posición, no en el
orden del stream de texto — `pdftotext` o `get_text("text")` pierden esa
información.
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from horario_uca.model import BBox, RGB


def _int_to_rgb(color: int) -> RGB:
    return ((color >> 16) & 255, (color >> 8) & 255, color & 255)


def _float_to_rgb(color: tuple[float, float, float]) -> RGB:
    return tuple(round(c * 255) for c in color)  # type: ignore[return-value]


@dataclass(frozen=True)
class RawSpan:
    bbox: BBox
    text: str
    color: RGB
    size: float
    font: str
    direction: tuple[float, float]


@dataclass(frozen=True)
class RawDrawing:
    rect: BBox
    fill: RGB | None
    stroke: RGB | None
    type: str


@dataclass(frozen=True)
class RawPage:
    page_index: int
    width: float
    height: float
    spans: list[RawSpan]
    drawings: list[RawDrawing]


def read_page(doc: pymupdf.Document, page_index: int) -> RawPage:
    page = doc[page_index]
    spans: list[RawSpan] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            direction = line.get("dir", (1.0, 0.0))
            for span in line["spans"]:
                spans.append(
                    RawSpan(
                        bbox=tuple(span["bbox"]),
                        text=span["text"],
                        color=_int_to_rgb(span["color"]),
                        size=span["size"],
                        font=span["font"],
                        direction=tuple(direction),
                    )
                )

    drawings: list[RawDrawing] = []
    for d in page.get_drawings():
        r = d["rect"]
        fill = _float_to_rgb(d["fill"]) if d.get("fill") is not None else None
        stroke = _float_to_rgb(d["color"]) if d.get("color") is not None else None
        drawings.append(
            RawDrawing(
                rect=(r.x0, r.y0, r.x1, r.y1),
                fill=fill,
                stroke=stroke,
                type=d.get("type", ""),
            )
        )

    return RawPage(
        page_index=page_index,
        width=page.rect.width,
        height=page.rect.height,
        spans=spans,
        drawings=drawings,
    )


def read_document(pdf_path: str) -> pymupdf.Document:
    return pymupdf.open(pdf_path)


def read_document_from_bytes(data: bytes) -> pymupdf.Document:
    """Igual que `read_document`, para cuando el PDF llega como bytes en
    memoria (p.ej. una subida HTTP) en vez de una ruta en disco — ver
    `horario_uca.pipeline.parse_document_from_bytes`."""
    return pymupdf.open(stream=data, filetype="pdf")

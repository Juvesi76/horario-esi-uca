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

"""Comando de depuración visual.

    python -m horario_uca.debug render data/GII_horario2627.pdf --page N

`--page` es el `page_index` 0-based, igual que en todo el resto del proyecto
(`SchedulePage.page_index`, `RawPage.page_index`) — N=0 es la primera página
del PDF. El PNG generado se llama `debug_page_index_{N}.png` para que el
nombre del fichero no pueda confundirse con una numeración 1-based. Genera un
PNG con los ClassBlock detectados en verde, los UnparsedBlock en rojo (con el
motivo como etiqueta) y las columnas de día detectadas como líneas
discontinuas azules.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf

from horario_uca.extract import read_document, read_page
from horario_uca.parse import parse_page
from horario_uca.parse.grid import find_day_columns

GREEN = (0, 0.6, 0)
RED = (0.8, 0, 0)
BLUE = (0, 0, 1)


def render(pdf_path: str, page_index: int, out_path: str | None = None) -> str:
    doc = read_document(pdf_path)
    raw = read_page(doc, page_index)
    schedule_page = parse_page(raw)
    day_columns, _ = find_day_columns(raw)

    page = doc[page_index]
    shape = page.new_shape()

    for day, (x0, x1) in day_columns.items():
        shape.draw_line(pymupdf.Point(x0, 140), pymupdf.Point(x0, page.rect.height - 20))
        shape.draw_line(pymupdf.Point(x1, 140), pymupdf.Point(x1, page.rect.height - 20))
    shape.finish(color=BLUE, width=0.5, dashes="[2 2] 0")

    for block in schedule_page.blocks:
        r = pymupdf.Rect(block.bbox)
        shape.draw_rect(r)
        label = f"{block.subject_acronym} {'/'.join(g.group_code for g in block.groups)}"
        shape.insert_text(r.tl + (1, 6), label, fontsize=4, color=GREEN)
    shape.finish(color=GREEN, width=0.8)

    for u in schedule_page.unparsed:
        r = pymupdf.Rect(u.bbox)
        shape.draw_rect(r)
        shape.insert_text(r.tl + (1, 6), u.reason, fontsize=4, color=RED)
    shape.finish(color=RED, width=0.8)

    shape.commit()

    if out_path is None:
        out_dir = Path("out")
        out_dir.mkdir(exist_ok=True)
        out_path = str(out_dir / f"debug_page_index_{page_index}.png")

    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
    pix.save(out_path)

    if schedule_page.warnings:
        print(f"{len(schedule_page.warnings)} warning(s):")
        for w in schedule_page.warnings:
            print(f"  [{w.code}] {w.message}")
    if schedule_page.infos:
        # Solo el recuento por defecto: los ParseInfo son comprobaciones con
        # resultado esperado, no anomalías — no hace falta listarlas para
        # depurar un fallo.
        print(f"{len(schedule_page.infos)} info(s) (comprobaciones OK, no listadas)")
    if schedule_page.unparsed:
        print(f"{len(schedule_page.unparsed)} bloque(s) sin interpretar:")
        for u in schedule_page.unparsed:
            print(f"  [{u.reason}] bbox={u.bbox}")

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m horario_uca.debug")
    sub = parser.add_subparsers(dest="command", required=True)

    render_p = sub.add_parser("render", help="genera un PNG con los rectángulos detectados")
    render_p.add_argument("pdf_path")
    render_p.add_argument("--page", type=int, required=True, help="page_index, 0-based")
    render_p.add_argument("--out", default=None)

    args = parser.parse_args()
    if args.command == "render":
        out_path = render(args.pdf_path, args.page, args.out)
        print(f"PNG generado: {out_path}")


if __name__ == "__main__":
    main()

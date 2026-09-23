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

"""Mide tiempo y pico de RSS de parsear UN PDF, pensado para correr DENTRO
del contenedor Docker con los límites reales del plan gratuito de Render
(`docker run --cpus=0.1 --memory=512m`, ver README, "Desplegar en Render").
`resource.ru_maxrss` es KB en Linux (el contenedor lo es, aunque se
desarrolle en Windows), no bytes.

Un PDF por invocación (un `docker run` por fichero), no varios en un
bucle: `ru_maxrss` es el pico ACUMULADO del proceso — con varios ficheros
en la misma invocación, el pico del segundo incluiría lo ya reservado por
el primero, mezclando los números.

Uso:
    docker run --rm --cpus=0.1 --memory=512m \
        -v "$(pwd)/data:/data:ro" -v "$(pwd)/scripts:/scripts:ro" \
        horario-uca-test python /scripts/medir_rendimiento.py /data/GII_horario2627.pdf
"""
import resource
import sys
import time

import pymupdf

from horario_uca.extract import read_page
from horario_uca.parse.exams import parse_exam_calendar
from horario_uca.pipeline import parse_document, resolve_document


def main(path: str) -> None:
    start = time.monotonic()
    if "calendarioExamenes" in path:
        doc = pymupdf.open(path)
        parse_exam_calendar(read_page(doc, 0))
        doc.close()
    else:
        pages = parse_document(path)
        resolve_document(pages)
    elapsed = time.monotonic() - start
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"{path}: {elapsed:.2f}s, pico RSS {peak_kb / 1024:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1])

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

"""Leyenda de asignaturas: acrónimo -> (nombre, código, color).

El color de una entrada es el color de texto de todo el span de la línea de
leyenda (verificado: no hay una muestra de color separada; el mismo color se
repite en los acrónimos de los bloques de clase de esa asignatura, aunque el
mapeo fiable de bloque a asignatura sigue siendo el texto del acrónimo, no el
color).
"""
from __future__ import annotations

import re

from horario_uca.extract import RawPage
from horario_uca.model import SubjectLegendEntry


# El acrónimo NO se define por lo que se ha visto (mayúsculas y guion)
# sino por lo que puede ser: cualquier secuencia sin espacios antes del
# primer " - " — encontrado un caso real que rompía la suposición anterior
# ([A-Z\-]+): `ApC` ("Aprendizaje Computacional", 4º, Itinerario de
# Computación) es el único acrónimo del documento con una minúscula, así
# que la fila entera no matcheaba `LEGEND_RE` desde el principio y la
# asignatura desaparecía de `page.legend` en silencio — mismo modo de
# fallo que la fusión GCV, pero sin fragmentar: aquí la fila cabía entera
# en un span, el problema era solo la clase de carácter permitida.
LEGEND_RE = re.compile(r"^(\S+) - (.+) - (\d{8})$")

# Una entrada de leyenda puede llegar partida en dos spans HORIZONTALES de
# la MISMA fila, no verticales — encontrado buscando explícitamente este
# caso a petición: `GCV - Gestión del Ciclo de Vida del Producto.` y
# `PLM-PDM - 21717037` son dos spans distintos, contiguos en X, mismo y0,
# en `GIDIDP` p.7 y `GIM-GIDIDP` p.8 (probablemente un cambio de estilo
# interno del PDF alrededor del paréntesis/sigla que PyMuPDF corta en un
# span nuevo). Ninguno de los dos fragmentos por separado matchea
# `LEGEND_RE` (el primero no termina en código, el segundo no tiene el
# `ACRONIMO - NOMBRE - ` completo), así que la asignatura entera
# desaparecía de la leyenda en silencio — no una entrada rota, una entrada
# AUSENTE. Fusión por proximidad horizontal en la misma fila, ratio sobre
# `span.size` (mismo patrón que el resto del parser): hueco real medido
# ~1.9-2.1pt a tamaño ~4.9-5.1pt (ratio ~0.37-0.41).
_LEGEND_MERGE_Y_RATIO = 0.3
_LEGEND_MERGE_X_GAP_RATIO = 1.0
_LEGEND_MERGE_MAX_FRAGMENTS = 3  # el propio span + hasta 2 continuaciones


def parse_legend(raw: RawPage) -> list[SubjectLegendEntry]:
    entries: list[SubjectLegendEntry] = []
    # Un span vacío/solo-espacio entre dos fragmentos reales (visto entre
    # "GCV - ...Producto." y "PLM-PDM - 21717037": PyMuPDF corta un span de
    # un único carácter " " justo en el hueco) no debe contar como
    # fragmento propio — si se incluye, la fusión añade DOS separadores en
    # vez de uno (uno al "fusionar" con el span vacío, otro al fusionar con
    # el fragmento real siguiente) y el nombre queda con doble espacio.
    # Se descarta de la lista de fusión, no del resto del parseo.
    spans = sorted((s for s in raw.spans if s.text.strip()), key=lambda s: (round(s.bbox[1], 1), s.bbox[0]))
    consumed: set[int] = set()

    for i, span in enumerate(spans):
        if id(span) in consumed:
            continue
        text = span.text.strip()
        m = LEGEND_RE.match(text)
        merged_ids: list[int] = []
        if not m:
            merged_text = text
            last = span
            for nxt in spans[i + 1 : i + _LEGEND_MERGE_MAX_FRAGMENTS]:
                if id(nxt) in consumed:
                    continue
                same_row = abs(nxt.bbox[1] - last.bbox[1]) < _LEGEND_MERGE_Y_RATIO * last.size
                gap = nxt.bbox[0] - last.bbox[2]
                if not (same_row and 0 <= gap < _LEGEND_MERGE_X_GAP_RATIO * last.size):
                    break
                merged_text = f"{merged_text} {nxt.text.strip()}"
                merged_ids.append(id(nxt))
                last = nxt
                m = LEGEND_RE.match(merged_text)
                if m:
                    break
        if not m:
            continue
        consumed.update(merged_ids)
        acronym, name, code = m.groups()
        entries.append(
            SubjectLegendEntry(acronym=acronym, name=name, code=code, color=span.color)
        )
    return entries

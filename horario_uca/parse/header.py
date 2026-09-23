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

"""Cabecera de página: año académico, curso, semestre, itinerario.

Los pares etiqueta/valor se localizan por
posición relativa (etiqueta arriba, valor debajo, mismo rango X), nunca por
una ventana Y fija ni por una lista fija de cursos/itinerarios — el tamaño de
letra de la cabecera escala con la densidad de la página (verificado: de
~4.5pt a ~8.4pt según cuántas subcolumnas tenga la rejilla), así que
cualquier límite en puntos absolutos calibrado contra una sola página se
rompe en otras. `parse_header` nunca lanza excepción: si no encuentra la
cabecera, devuelve los campos que falten a `None` y un `ParseWarning`.
"""
from __future__ import annotations

from dataclasses import dataclass

from horario_uca.extract import RawPage, RawSpan
from horario_uca.model import ParseWarning

_LABELS = ("Año académico", "Curso", "Semestre")
# Un doble grado ("Doble grado en Ingeniería X e Ingeniería Y") NO empieza
# por "Grado en Ingeniería" — descubierto midiendo el offset título→segunda
# línea contra PDFs de otros grados: `title_span` salía `None` en TODAS las
# páginas de los 3 dobles grados diagnosticados (`GIE-GIEI`, `GIM-GIE`,
# `GIM-GIDIDP`), así que la línea de itinerario, cuando existía, nunca se
# leía — no un fallo de la ventana geométrica, un fallo de texto anterior a
# ella.
_TITLE_PREFIXES = ("Grado en Ingeniería", "Doble grado en Ingeniería")

# La segunda línea de itinerario, cuando existe, se busca por posición
# relativa al título — y ese título escala en tamaño con la densidad de la
# página (~4.5pt a ~17.6pt, medido en las 24 páginas de referencia más 110
# de otros grados). La ventana de Y y la tolerancia de X tienen que ser
# ratio sobre `title.size`, no puntos absolutos: ningún umbral geométrico de
# este parser se fija en puntos absolutos, la tipografía real varía
# demasiado entre páginas. Medido
# sobre los casos reales con itinerario en las 134 páginas comprobadas:
# `dy/title.size` entre 0.20 y 0.349, `dx/title.size` ≈ 0.243 siempre (el
# mismo valor en páginas de fuentes muy distintas — la propia plantilla del
# PDF alinea la segunda línea al mismo x0 relativo que el título,
# proporcional al tamaño de letra). Las páginas SIN itinerario no tienen
# ningún candidato dentro de esta ventana más estrecha (el texto más
# cercano en esos casos es "Lunes" o una cabecera de hora, con
# dy/title.size >= 1.38 — muy por encima del límite de abajo, así que nunca
# se cuela como itinerario falso).
_ITINERARIO_Y_RATIO = (0.1, 0.6)
_ITINERARIO_X_RATIO = 0.4
# El título de un doble grado largo ("Doble grado en Ingeniería Mecánica e
# Ingeniería en Diseño Industrial y Desarrollo del Producto") puede envolver
# a una segunda (o tercera) línea DENTRO del propio título, antes de llegar
# a la línea de itinerario real — encontrado buscando exactamente este caso
# a petición explícita, tras notar un nombre de itinerario truncado
# ("del Producto" en vez del nombre real). La primera versión de esta
# búsqueda tomaba sin más "el candidato más cercano por debajo" y se
# quedaba con esa continuación envuelta del título, no con el itinerario —
# mismo síntoma (línea partida) que la fusión de tipo de grupo ya
# documentada, pero la causa aquí no es que el itinerario se parta, es que
# el TÍTULO se parte y su segunda línea se confunde con el itinerario.
# La discriminación es por TAMAÑO DE FUENTE, no por posición — verificado
# en las 134 páginas: una línea de continuación del título envuelto tiene
# EXACTAMENTE el mismo `size` que la primera línea del título (es el mismo
# texto, solo partido por el renderizador al llegar al margen); la línea de
# itinerario real, cuando existe, tiene SIEMPRE un tamaño menor — la razón
# exacta observada en los tres casos medidos (GII, un doble grado corto, un
# doble grado largo) es el mismo ratio 5/6 ≈ 0.833 sobre el tamaño del
# título, consistente entre páginas de tipografías muy distintas.
_TITLE_WRAP_SIZE_TOLERANCE = 0.05


@dataclass(frozen=True)
class HeaderInfo:
    academic_year: str | None
    curso: str | None
    semestre: int | None
    itinerario: str | None


def _x_overlap(a: RawSpan, b: RawSpan) -> bool:
    ax0, _, ax1, _ = a.bbox
    bx0, _, bx1, _ = b.bbox
    return ax0 < bx1 and bx0 < ax1


def _value_below(label: RawSpan, spans: list[RawSpan]) -> RawSpan | None:
    _, ly0, _, ly1 = label.bbox
    candidates = [s for s in spans if s.bbox[1] > ly1 - 0.5 and _x_overlap(label, s)]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.bbox[1])


def parse_header(raw: RawPage) -> tuple[HeaderInfo, list[ParseWarning]]:
    warnings: list[ParseWarning] = []

    # Sin ventana de Y: las etiquetas se buscan por texto exacto en toda la
    # página. Son literales de dominio ("Curso", "Semestre", "Año
    # académico") que no aparecen en ningún otro sitio del documento, así
    # que no hace falta acotar dónde buscar.
    label_spans = {t: s for s in raw.spans for t in _LABELS if s.text.strip() == t}
    missing_labels = [t for t in _LABELS if t not in label_spans]

    values: dict[str, RawSpan | None] = {
        t: _value_below(label_spans[t], raw.spans) for t in _LABELS if t in label_spans
    }
    missing_values = [t for t in _LABELS if t in label_spans and values.get(t) is None]

    if missing_labels or missing_values:
        detail = []
        if missing_labels:
            detail.append(f"etiquetas no encontradas: {missing_labels}")
        if missing_values:
            detail.append(f"valores no encontrados debajo de: {missing_values}")
        warnings.append(
            ParseWarning(
                code="cabecera_no_encontrada",
                message=f"página {raw.page_index}: " + "; ".join(detail),
                page_index=raw.page_index,
            )
        )

    academic_year = values.get("Año académico").text.strip() if values.get("Año académico") else None
    curso = values.get("Curso").text.strip() if values.get("Curso") else None
    semestre_span = values.get("Semestre")
    semestre = None
    if semestre_span is not None:
        semestre = 1 if semestre_span.text.strip().startswith("1") else 2

    title_span = next(
        (s for s in raw.spans if any(s.text.strip().startswith(p) for p in _TITLE_PREFIXES)), None
    )
    itinerario = None
    if title_span is not None:
        tx0 = title_span.bbox[0]
        title_size = title_span.size
        y_lo, y_hi = _ITINERARIO_Y_RATIO[0] * title_size, _ITINERARIO_Y_RATIO[1] * title_size
        x_tol = _ITINERARIO_X_RATIO * title_size
        seen = {id(title_span)}
        current = title_span
        # Encadena hacia abajo mientras la línea siguiente sea del mismo
        # tamaño que el título (continuación envuelta del propio título, no
        # itinerario); se detiene en la primera línea de tamaño MENOR (el
        # itinerario real) o en cuanto no hay ningún candidato en la
        # ventana geométrica (no hay itinerario en esta página).
        while True:
            _, _, _, cy1 = current.bbox
            candidates = [
                s
                for s in raw.spans
                if id(s) not in seen
                and y_lo < (s.bbox[1] - cy1) < y_hi
                and abs(s.bbox[0] - tx0) < x_tol
            ]
            if not candidates:
                break
            nxt = min(candidates, key=lambda s: s.bbox[1])
            if abs(nxt.size - title_size) < _TITLE_WRAP_SIZE_TOLERANCE:
                current, seen = nxt, seen | {id(nxt)}
                continue
            if nxt.size < title_size - _TITLE_WRAP_SIZE_TOLERANCE:
                itinerario = nxt.text.strip()
            break

    return (
        HeaderInfo(academic_year=academic_year, curso=curso, semestre=semestre, itinerario=itinerario),
        warnings,
    )

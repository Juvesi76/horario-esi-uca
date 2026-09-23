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

"""Calendario de exámenes: tabla de convocatoria (código, asignatura,
siglas, curso, semestre, fecha, hora), formato geométrico totalmente
distinto del horario semanal: una tabla, no una rejilla con minicalendario.

Verificado sobre `data/GII.calendarioExamenes.Feb27.pdf` (65 filas de
datos, 1 sola sección, 1 sola página): CADA fila de datos tiene EXACTAMENTE
7 spans de PyMuPDF, en orden X creciente, uno por columna
(código/asignatura/siglas/curso/semestre/fecha/hora) — nunca una línea de
asignatura partida en dos (a diferencia del horario semanal, donde SÍ pasa
con nombres largos). Por eso la asignación de columna es POSICIONAL
(ordenar por `x0`, tomar el span N-ésimo), no por proximidad a la cabecera
de columna: se comprobó que la cabecera "Asignatura" está CENTRADA sobre
una columna mucho más ancha que su propio texto, con su centro real mucho
más cerca del centro de la columna "Código" que del de sus propios datos
(`"Cálculo"` empieza en x=142.7, la cabecera "Asignatura" en x=212.1) — un
emparejamiento por cabecera más cercana habría asignado mal la columna.
Si algún día aparece una fila con un número de spans distinto de 7, se
marca `ParseWarning` con el texto crudo — nunca se asume qué son antes de
comprobarlo.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime

from horario_uca.extract import RawPage, RawSpan
from horario_uca.model import ExamCalendar, ExamEntry, ParseInfo, ParseWarning, SchedulePage

COLUMN_HEADERS = ["Código", "Asignatura", "Siglas", "Curso", "Semestre", "Fecha", "Hora"]
_CODE_RE = re.compile(r"^\d{8}$")
_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_TIME_RE = re.compile(r"^(\d{2}):(\d{2})$")
_CONVOCATORIA_RE = re.compile(r"CONVOCATORIA DE (.+)$", re.IGNORECASE)
_GRADO_PREFIX = "GRADO EN"

# Tolerancia de fila: mismo patrón de agrupar-por-y0-redondeado que el
# resto del proyecto (grid.py, calendar.py) — la tabla no tiene rejilla de
# fondo con la que derivar filas, así que agrupar por texto en la misma
# franja Y es la única señal disponible, y basta: verificado que las 66
# filas (65 datos + 1 cabecera) de la página de referencia agrupan limpio
# sin colisiones.
_ROW_Y_PRECISION = 1


def _iso_date(dd: str, mm: str, yyyy: str) -> str | None:
    try:
        return date(int(yyyy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


def parse_exam_calendar(raw: RawPage) -> ExamCalendar:
    """Nunca lanza excepción — mismo criterio que el resto de `parse/`. Una
    fila que no se pueda validar por completo queda fuera de `entries`,
    con su propio `ParseWarning` (texto crudo incluido), en vez de
    tumbar el análisis de toda la página."""
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []

    rows: dict[float, list[RawSpan]] = defaultdict(list)
    for s in raw.spans:
        rows[round(s.bbox[1], _ROW_Y_PRECISION)].append(s)

    convocatoria = None
    grado = None
    header_row_ys: list[float] = []
    for y, spans_in_row in rows.items():
        if len(spans_in_row) != 1:
            continue
        text = spans_in_row[0].text.strip()
        m = _CONVOCATORIA_RE.search(text)
        if m:
            convocatoria = m.group(1).strip()
            continue
        if text.upper().startswith(_GRADO_PREFIX):
            grado = text.strip()
            continue

    if convocatoria is None:
        warnings.append(
            ParseWarning(code="convocatoria_no_encontrada", message="no se encontró el título de convocatoria", page_index=raw.page_index)
        )
    if grado is None:
        warnings.append(
            ParseWarning(code="grado_no_encontrado", message="no se encontró la línea de grado", page_index=raw.page_index)
        )

    # Cabecera de columna: cualquier fila cuyos 7 textos (en orden X) sean
    # EXACTAMENTE COLUMN_HEADERS — literal, no heurística, porque son
    # literales de dominio fijos del propio formato de tabla, igual que
    # "Curso"/"Semestre" en header.py.
    all_rows_sorted = sorted(rows.items())
    for y, spans_in_row in all_rows_sorted:
        if len(spans_in_row) == len(COLUMN_HEADERS):
            texts = [s.text.strip() for s in sorted(spans_in_row, key=lambda s: s.bbox[0])]
            if texts == COLUMN_HEADERS:
                header_row_ys.append(y)

    if not header_row_ys:
        warnings.append(
            ParseWarning(code="tabla_examenes_vacia", message="no se encontró ninguna cabecera de columna reconocible", page_index=raw.page_index)
        )
        return ExamCalendar(
            convocatoria=convocatoria,
            grado=grado,
            sections=[],
            entries=[],
            warnings=warnings,
            infos=infos,
        )

    # Sección de cada bloque de filas: el span de una sola celda
    # inmediatamente por encima de su cabecera de columna (mayor y1 que
    # siga siendo < y de la cabecera) — nunca el texto fijo de hoy
    # ("Asignaturas propias del título"), para que un PDF futuro con más
    # secciones (optativas, otros títulos) las reconozca igual sin tocar
    # código.
    single_spans = sorted(((y, ss[0]) for y, ss in rows.items() if len(ss) == 1), key=lambda t: t[0])

    def _section_for(header_y: float) -> str | None:
        above = [t for t in single_spans if t[0] < header_y]
        return above[-1][1].text.strip() if above else None

    sections: list[str] = []
    entries: list[ExamEntry] = []

    boundaries = header_row_ys + [float("inf")]
    for i, header_y in enumerate(header_row_ys):
        section = _section_for(header_y)
        if section and section not in sections:
            sections.append(section)
        next_boundary = boundaries[i + 1]
        data_rows = [
            (y, ss) for y, ss in all_rows_sorted if header_y < y < next_boundary and len(ss) == len(COLUMN_HEADERS)
        ]
        for y, spans_in_row in data_rows:
            cells = [s.text.strip() for s in sorted(spans_in_row, key=lambda s: s.bbox[0])]
            raw_text = " | ".join(cells)
            code, name, acronym, curso_txt, semestre_txt, fecha_txt, hora_txt = cells

            problems = []
            if not _CODE_RE.match(code):
                problems.append(f"código {code!r} no son 8 dígitos")
            if not curso_txt.isdigit():
                problems.append(f"curso {curso_txt!r} no es un entero")
            if not semestre_txt.isdigit():
                problems.append(f"semestre {semestre_txt!r} no es un entero")
            date_m = _DATE_RE.match(fecha_txt)
            iso_date = _iso_date(*date_m.groups()) if date_m else None
            if iso_date is None:
                problems.append(f"fecha {fecha_txt!r} no parsea como DD/MM/AAAA válida")
            if not _TIME_RE.match(hora_txt):
                problems.append(f"hora {hora_txt!r} no es HH:MM")
            elif not (0 <= int(hora_txt[:2]) <= 23 and 0 <= int(hora_txt[3:]) <= 59):
                problems.append(f"hora {hora_txt!r} fuera de rango")
            if not name:
                problems.append("nombre de asignatura vacío")
            if not acronym:
                problems.append("siglas vacías")

            if problems:
                warnings.append(
                    ParseWarning(
                        code="fila_examen_invalida",
                        message=f"{'; '.join(problems)} — fila cruda: {raw_text}",
                        page_index=raw.page_index,
                    )
                )
                continue

            entries.append(
                ExamEntry(
                    code=code,
                    name=name,
                    acronym=acronym,
                    curso=int(curso_txt),
                    semestre=int(semestre_txt),
                    date=iso_date,
                    start_time=hora_txt,
                    room=None,
                    section=section or "",
                )
            )

    return ExamCalendar(
        convocatoria=convocatoria,
        grado=grado,
        sections=sections,
        entries=entries,
        warnings=warnings,
        infos=infos,
    )


def cross_check_exam_codes(exam_calendar: ExamCalendar, pages: list[SchedulePage]) -> list[ParseInfo]:
    """Validación cruzada barata: el código de 8 dígitos es la clave
    estable entre el calendario de exámenes y la leyenda del horario (las
    siglas podrían diferir entre documentos, el código no). Un código que
    solo aparece en uno de los dos documentos NO es un error — es
    `ParseInfo`, no `ParseWarning`, en las dos direcciones: una asignatura
    puede tener clases y no examen en esta convocatoria (TFG, verificado
    real en `GII_horario2627.pdf`/`GII.calendarioExamenes.Feb27.pdf`), o
    examen sin clases este curso (`ApC` "Aprendizaje Computacional", curso
    4º, verificado real en el mismo par de PDFs — probablemente una
    optativa no ofertada este año pero con convocatoria de examen para
    quien ya la cursó)."""
    infos: list[ParseInfo] = []

    exam_codes = {e.code: e.acronym for e in exam_calendar.entries}
    legend_codes: dict[str, str] = {}
    for page in pages:
        for entry in page.legend:
            legend_codes.setdefault(entry.code, entry.acronym)

    for code in sorted(set(exam_codes) - set(legend_codes)):
        infos.append(
            ParseInfo(
                code="examen_sin_clases_en_horario",
                message=f"código {code} ({exam_codes[code]}) tiene examen en esta convocatoria pero no aparece en ninguna página del horario",
                page_index=0,
            )
        )
    for code in sorted(set(legend_codes) - set(exam_codes)):
        infos.append(
            ParseInfo(
                code="clases_sin_examen_en_convocatoria",
                message=f"código {code} ({legend_codes[code]}) tiene clases en el horario pero no examen en esta convocatoria",
                page_index=0,
            )
        )
    return infos

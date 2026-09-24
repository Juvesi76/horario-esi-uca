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

"""Modelo de datos intermedio (JSON serializable vía pydantic)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

BBox = tuple[float, float, float, float]
RGB = tuple[int, int, int]

GROUP_TYPES: dict[str, str] = {
    "A": "Clases de teoría",
    "B": "Clases de problemas",
    "C": "Prácticas informáticas",
    "D": "Prácticas de laboratorio",
    "X": "Clases teórico-prácticas",
}

DIA_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes"]


class SubjectLegendEntry(BaseModel):
    acronym: str
    name: str
    code: str
    color: RGB


class WeekGroup(BaseModel):
    group_code: str
    group_type: str
    weeks_active: list[int]


class ClassBlock(BaseModel):
    subject_acronym: str
    day_of_week: int
    start_time: str
    end_time: str
    room: str | None  # solo el identificador ("D01"), sin el prefijo "Aula"
    groups: list[WeekGroup]
    bbox: BBox


class DayLectivoStatus(str, Enum):
    LECTIVO = "lectivo"
    NO_LECTIVO = "no_lectivo"
    FUERA_DE_PERIODO = "fuera_de_periodo"


class DayStatus(BaseModel):
    date: str
    status: DayLectivoStatus


class CalendarWeek(BaseModel):
    week_number: int
    days: list[DayStatus]


class ScheduleNote(BaseModel):
    from_weekday: int
    from_week: int
    from_date: str
    to_weekday: int
    to_week: int
    to_date: str
    raw_text: str


class UnparsedBlock(BaseModel):
    bbox: BBox
    raw_text: str
    reason: str


class ParseWarning(BaseModel):
    """Anomalía que requiere atención — algo salió distinto de lo esperado."""

    code: str
    message: str
    page_index: int
    bbox: BBox | None = None


class ParseInfo(BaseModel):
    """Comprobación ejecutada con resultado esperado — mismo formato que
    ParseWarning pero NO es una anomalía. Confirma que una regla se evaluó y
    dio el resultado benigno previsto (p.ej. una semana activa que coincide
    con el día de origen de una nota de traslado de su propia página). Vive
    aparte de ParseWarning para que el informe de la Fase 4 pueda mostrar
    solo anomalías por defecto sin perder la trazabilidad de lo verificado.
    """

    code: str
    message: str
    page_index: int
    bbox: BBox | None = None


class SchedulePage(BaseModel):
    page_index: int
    academic_year: str | None
    curso: str | None
    semestre: int | None
    itinerario: str | None
    generation_timestamp: str | None = None
    """ISO 8601 (p.ej. "2026-09-15T16:04:00") — de la marca de 14 dígitos
    del pie de página. Es POR PÁGINA, no del documento entero: verificado
    que varía entre páginas de un mismo PDF (ver `parse/header.py`)."""
    approval_date: str | None = None
    """ISO (p.ej. "2026-05-11") — de la frase "Aprobado en Junta de
    Escuela..." de la cabecera. A diferencia de `generation_timestamp`,
    esta sí es la misma en todas las páginas de un documento."""
    legend: list[SubjectLegendEntry] = []
    blocks: list[ClassBlock] = []
    calendar: list[CalendarWeek] = []
    notes: list[ScheduleNote] = []
    unparsed: list[UnparsedBlock] = []
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []


class ScheduleDocument(BaseModel):
    source_pdf: str
    pages: list[SchedulePage]


class CalendarEvent(BaseModel):
    """Una sesión de clase resuelta a fecha real (Fase 3). `date` y
    `moved_from` son `str` ISO, no `datetime.date` — mismo criterio que
    `DayStatus.date`/`ScheduleNote.from_date` en el resto del modelo, para
    usar un único criterio de fecha en todo el proyecto."""

    subject_acronym: str
    subject_name: str
    curso: str | None
    semestre: int | None
    itinerario: str | None
    group_code: str
    group_type: str
    date: str
    weekday: int  # 0=lunes..4=viernes DEL EVENTO YA RESUELTO — puede diferir
    # de ClassBlock.day_of_week si el evento se trasladó (ver moved_from)
    start_time: str
    end_time: str
    room: str | None
    moved_from: str | None  # fecha de origen ISO si esta ocurrencia viene de un traslado de nota


class ScheduleConflict(BaseModel):
    """Dos `CalendarEvent` de grupos DISTINTOS con horas solapadas en la misma
    fecha, dentro de la selección del usuario. No es un fallo del parser ni
    del render: el propio PDF programa esos dos grupos a la vez y un alumno
    no puede asistir a ambos — es una combinación de grupos incompatible. Ver
    `select/conflicts.py` (única función que los calcula, consumida tanto por
    `report.py` como por `render/html.py`)."""

    date: str
    event_a: CalendarEvent
    event_b: CalendarEvent


class SubjectSelection(BaseModel):
    """Entrada del usuario para la Fase 4 (`select/filter.py`)."""

    acronym: str
    curso: str
    itinerario: str | None = None
    groups: list[str]  # ["A1", "B3", "C5"] — vacío de una letra = ese tipo se descarta
    solo_examen: bool = False
    """True: asignatura llevada por libre — no genera ningún evento de
    clase (normalmente con `groups` vacío) pero sí cuenta para el
    calendario de exámenes. La regla automática por semestre
    (`select/exams.py::CONVOCATORIA_AUTO_SEMESTER`) no aplica aquí: quien
    lleva una asignatura por libre no tiene por qué coincidir con la
    convocatoria de quien sí va a clase, así que la convocatoria se elige
    explícitamente en `convocatorias`."""
    convocatorias: list[str] = []
    """Nombres de convocatoria elegidos explícitamente para esta
    asignatura cuando `solo_examen` es True (p.ej. `["FEBRERO DE
    2027"]`, tal cual `ExamCalendar.convocatoria`). Ignorado si
    `solo_examen` es False."""


class ExamEntry(BaseModel):
    """Una fila de la tabla de convocatoria — ver `parse/exams.py`."""

    code: str
    name: str
    acronym: str
    curso: int
    semestre: int
    date: str  # ISO, mismo criterio que el resto del modelo
    start_time: str  # "HH:MM"
    room: str | None = None  # nunca viene en el PDF — lo rellena el alumno a mano en la interfaz
    section: str  # p.ej. "Asignaturas propias del título"
    convocatoria: str | None = None  # "FEBRERO DE 2027" — de qué ExamCalendar viene, para agrupar
    # varias convocatorias de la misma asignatura en el render (una selección
    # puede incluir más de un PDF de convocatoria a la vez).


class ExamCalendar(BaseModel):
    convocatoria: str | None  # p.ej. "FEBRERO DE 2027", tal cual aparece tras "CONVOCATORIA DE"
    grado: str | None
    sections: list[str] = []
    entries: list[ExamEntry] = []
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []


class ExamConflict(BaseModel):
    """Dos `ExamEntry` distintos el mismo día — ver `select/exams.py`.
    `certain=True` cuando comparten hora de inicio exacta (choque seguro,
    el único caso posible cuando todos los exámenes de la convocatoria son
    a la misma hora, como en `GII.calendarioExamenes.Feb27.pdf`);
    `certain=False` cuando el día coincide pero la hora de inicio no — sin
    hora de fin en el PDF no se puede confirmar el solape, así que se
    avisa como POSIBLE choque, nunca como seguro."""

    date: str
    exam_a: ExamEntry
    exam_b: ExamEntry
    certain: bool


class ExamClassConflict(BaseModel):
    """Un examen que coincide con una clase real de la selección del
    alumno — la hora de fin del examen no está en el PDF, así que se
    estima con una duración nominal configurable (ver
    `select/exams.py::EXAM_NOMINAL_DURATION_MINUTES`)."""

    exam: ExamEntry
    event: CalendarEvent


class ExamSelection(BaseModel):
    """Entrada del usuario para la Fase 4 de exámenes: qué exámenes debe
    incluir el calendario final, por código (clave estable, ver
    `parse/exams.py`) — nunca por siglas."""

    codes: list[str]
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []


class Session(BaseModel):
    """Vista aplanada de un (ClassBlock, WeekGroup) para inspección/depuración."""

    asignatura: str
    curso: str | None
    semestre: int | None
    itinerario: str | None
    grupo: str
    tipo: str
    dia: str
    inicio: str
    fin: str
    aula: str | None
    semanas: list[int]

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

"""Orquestación de fases compartida por la CLI (`cli.py`) y por la API web
(`web/app.py`) — ninguna de las dos debe reimplementar esta lógica por su
cuenta. No añade ninguna regla
nueva de dominio: encadena `parse/`, `resolve/` y `select/`, que son las
fuentes de verdad.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf
from pydantic import BaseModel

from horario_uca.extract import read_document, read_document_from_bytes, read_page
from horario_uca.model import (
    CalendarEvent,
    ParseWarning,
    ScheduleConflict,
    SchedulePage,
    SubjectSelection,
)
from horario_uca.parse import parse_page
from horario_uca.render.html import build_html
from horario_uca.render.ics import build_ics
from horario_uca.report import build_confirmation_report
from horario_uca.resolve import resolve_events, validate_events_lectivo
from horario_uca.select import filter_events, find_conflicts, validate_selection

# Códigos de validate_selection que impiden generar nada: la selección no
# tiene un significado único o pide algo que no existe. El resto
# (`letra_con_docencia_sin_grupo_elegido`) es informativo.
HARD_VALIDATION_CODES = {
    "asignatura_no_existe_en_curso",
    "asignatura_no_existe_en_itinerario",
    "itinerario_ambiguo",
    "grupo_no_existe",
}


def _parse_pages(doc: pymupdf.Document) -> list[SchedulePage]:
    return [parse_page(read_page(doc, i)) for i in range(doc.page_count)]


def parse_document(pdf_path: str) -> list[SchedulePage]:
    return _parse_pages(read_document(pdf_path))


def parse_document_from_bytes(data: bytes) -> list[SchedulePage]:
    """Igual que `parse_document`, para un PDF ya en memoria (p.ej. una
    subida HTTP) — nunca se escribe a disco. Puede lanzar
    `pymupdf.FileDataError` si `data` no es un PDF válido; el llamador decide
    cómo traducir eso a un error accionable (ver `web/app.py`)."""
    return _parse_pages(read_document_from_bytes(data))


def resolve_document(pages: list[SchedulePage]) -> tuple[list[CalendarEvent], list[ParseWarning]]:
    """Resuelve fechas reales para TODAS las páginas y concatena el
    resultado. `validate_events_lectivo` se ejecuta aquí también, por
    página, para que ningún consumidor de este helper se olvide de esa
    comprobación."""
    events: list[CalendarEvent] = []
    warnings: list[ParseWarning] = []
    for page in pages:
        page_events, page_warnings = resolve_events(page)
        events.extend(page_events)
        warnings.extend(page_warnings)
        warnings.extend(validate_events_lectivo(page, page_events))
    return events, warnings


# --- Catálogo: qué cursos/asignaturas/grupos hay en un documento ya parseado.
# Usado por `horario listar` y por el paso 2-3 del flujo web (elegir curso,
# elegir grupos) — una sola función, no una por interfaz.


class CatalogGroupType(BaseModel):
    tipo: str
    codigos: list[str]
    sesiones: dict[str, list[str]] = {}
    """código -> lista de sesiones ("día|inicio|fin|aula|semanas"), una por
    cada `ClassBlock` distinto de ese código (puede haber más de una: dos
    bloques del mismo código nunca se deduplican, cada rectángulo es una
    fuente independiente con su propio conjunto de semanas). Sirve solo
    para que un consumidor (hoy,
    `web/static/index.html::mergeCombo` al fundir 1ºA/1ºB) pueda comprobar
    si un código que aparece igual en dos páginas es de verdad la misma
    clase o una colisión de texto — no se usa en ningún otro sitio del
    pipeline."""


class CatalogSubject(BaseModel):
    acronimo: str
    nombre: str
    grupos: dict[str, CatalogGroupType]  # letra ("A".."D","X") -> tipo + códigos


class CatalogCombo(BaseModel):
    page_index: int
    curso: str
    semestre: int
    itinerario: str | None
    academic_year: str | None
    asignaturas: list[CatalogSubject]


def build_catalog(pages: list[SchedulePage]) -> list[CatalogCombo]:
    """Una entrada por página con curso/semestre reconocidos y al menos un
    bloque de clase — omite páginas sin cabecera legible o vacías en vez de
    lanzar, mismo criterio de tolerancia que el resto de `parse/`."""
    combos: list[CatalogCombo] = []
    for page in pages:
        if page.curso is None or page.semestre is None or not page.blocks:
            continue

        by_subject: dict[str, dict[str, set[str]]] = {}
        type_by_letter: dict[str, dict[str, str]] = {}
        sessions_by_code: dict[str, dict[str, set[str]]] = {}
        for block in page.blocks:
            for group in block.groups:
                letter = group.group_code[0]
                by_subject.setdefault(block.subject_acronym, {}).setdefault(letter, set()).add(group.group_code)
                type_by_letter.setdefault(block.subject_acronym, {})[letter] = group.group_type
                weeks = ",".join(str(w) for w in sorted(group.weeks_active))
                signature = f"{block.day_of_week}|{block.start_time}|{block.end_time}|{block.room or ''}|{weeks}"
                sessions_by_code.setdefault(block.subject_acronym, {}).setdefault(group.group_code, set()).add(signature)

        legend_by_acronym = {entry.acronym: entry for entry in page.legend}
        asignaturas = [
            CatalogSubject(
                acronimo=acronym,
                nombre=legend_by_acronym[acronym].name if acronym in legend_by_acronym else acronym,
                grupos={
                    letter: CatalogGroupType(
                        tipo=type_by_letter[acronym][letter],
                        codigos=sorted(codes),
                        sesiones={
                            code: sorted(sessions_by_code[acronym][code]) for code in sorted(codes)
                        },
                    )
                    for letter, codes in by_subject[acronym].items()
                },
            )
            for acronym in sorted(by_subject)
        ]

        combos.append(
            CatalogCombo(
                page_index=page.page_index,
                curso=page.curso,
                semestre=page.semestre,
                itinerario=page.itinerario,
                academic_year=page.academic_year,
                asignaturas=asignaturas,
            )
        )
    return combos


# --- Generación completa: de una lista de SubjectSelection a HTML + .ics,
# pasando por validación, resolución de fechas, filtrado y conflictos. Un
# solo punto de entrada para `horario generar` y para POST /api/generar.


@dataclass
class GenerateOutcome:
    validation_warnings: list[ParseWarning]
    hard_errors: list[ParseWarning]
    resolve_warnings: list[ParseWarning] = field(default_factory=list)
    conflicts: list[ScheduleConflict] = field(default_factory=list)
    events: list[CalendarEvent] = field(default_factory=list)
    report_text: str = ""
    html: str | None = None
    ics: bytes | None = None

    @property
    def ok(self) -> bool:
        return not self.hard_errors


def generate_calendar(
    pages: list[SchedulePage],
    selections: list[SubjectSelection],
    titulo: str = "Horario ESI (UCA)",
    verbose: bool = False,
) -> GenerateOutcome:
    """Nunca lanza excepción por una selección inválida — los errores
    "duros" (asignatura/grupo inexistente, itinerario ambiguo) se devuelven
    en `hard_errors`, y el llamador decide cómo comunicarlos (código de
    salida en la CLI, HTTP 400 en la web). No escribe nada a disco."""
    validation_warnings: list[ParseWarning] = []
    for selection in selections:
        validation_warnings.extend(validate_selection(selection, pages))
    hard_errors = [w for w in validation_warnings if w.code in HARD_VALIDATION_CODES]

    if hard_errors:
        return GenerateOutcome(validation_warnings=validation_warnings, hard_errors=hard_errors)

    events, resolve_warnings = resolve_document(pages)
    filtered = filter_events(events, selections)
    conflicts = find_conflicts(filtered)
    report_text = build_confirmation_report(selections, pages, filtered, verbose=verbose)
    legend = [entry for page in pages for entry in page.legend]
    html = build_html(filtered, legend=legend, title=titulo)
    ics = build_ics(filtered, calendar_name=titulo)

    return GenerateOutcome(
        validation_warnings=validation_warnings,
        hard_errors=[],
        resolve_warnings=resolve_warnings,
        conflicts=conflicts,
        events=filtered,
        report_text=report_text,
        html=html,
        ics=ics,
    )

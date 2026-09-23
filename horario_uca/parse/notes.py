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

"""Notas al pie: traslados de clase (día/semana origen → día/semana destino).

Contra lo que se documentó en la Fase 1 sobre concatenación sin separador en
páginas rotadas: verificado en las 24 páginas que cada nota es SIEMPRE su
propio span independiente, esté la página rotada o no — `re.search` directo
por span basta, no hace falta `re.finditer` sobre texto concatenado. La
rotación solo afecta a `span.direction`, nunca a cómo se segmenta el texto en
spans.
"""
from __future__ import annotations

import re

from horario_uca.extract import RawPage
from horario_uca.model import DayLectivoStatus, DayStatus, DIA_NOMBRE, ParseInfo, ParseWarning, ScheduleNote

DIA_TO_WEEKDAY = {nombre: i for i, nombre in enumerate(DIA_NOMBRE)}

NOTE_RE = re.compile(
    r"Las clases del (?P<from_day>\S+) de la semana (?P<from_week>\d+) "
    r"\((?P<from_date>\d{2}/\d{2}/\d{4})\) se impartir[áa]n el (?P<to_day>\S+) "
    r"de la semana (?P<to_week>\d+) \((?P<to_date>\d{2}/\d{2}/\d{4})\)\.?"
)


def _to_iso(dmy: str) -> str:
    d, m, y = dmy.split("/")
    return f"{y}-{m}-{d}"


def parse_notes(raw: RawPage) -> tuple[list[ScheduleNote], list[ParseWarning]]:
    notes: list[ScheduleNote] = []
    warnings: list[ParseWarning] = []

    for s in raw.spans:
        if "impartir" not in s.text:
            continue
        text = s.text.strip()
        m = NOTE_RE.search(text)
        if not m:
            warnings.append(
                ParseWarning(
                    code="nota_no_reconocida",
                    message=f"texto no coincide con el patrón esperado: {text!r}",
                    page_index=raw.page_index,
                    bbox=s.bbox,
                )
            )
            continue

        from_day = m.group("from_day").lower()
        to_day = m.group("to_day").lower()
        if from_day not in DIA_TO_WEEKDAY or to_day not in DIA_TO_WEEKDAY:
            warnings.append(
                ParseWarning(
                    code="nota_dia_desconocido",
                    message=f"día no reconocido en {text!r} (from={from_day!r}, to={to_day!r})",
                    page_index=raw.page_index,
                    bbox=s.bbox,
                )
            )
            continue

        notes.append(
            ScheduleNote(
                from_weekday=DIA_TO_WEEKDAY[from_day],
                from_week=int(m.group("from_week")),
                from_date=_to_iso(m.group("from_date")),
                to_weekday=DIA_TO_WEEKDAY[to_day],
                to_week=int(m.group("to_week")),
                to_date=_to_iso(m.group("to_date")),
                raw_text=text,
            )
        )

    return notes, warnings


def validate_notes_against_calendar(
    notes: list[ScheduleNote],
    day_status: dict[tuple[int, int], DayStatus],
    page_index: int,
) -> tuple[list[ParseWarning], list[ParseInfo]]:
    """Validación cruzada notas ↔ minicalendario.

    1. Coherencia interna: from_weekday+from_week resuelto contra el
       calendario debe dar la misma fecha que el texto literal de la nota
       (igual para to_*). Siempre ParseWarning si falla.
    2. La fecha de destino debe ser LECTIVO — invariante dura, siempre
       ParseWarning si falla (no se puede dar clase en un día no lectivo).
    3. La fecha de origen NO tiene por qué ser no lectiva: verificado en las
       24 páginas que la nota de fin de semestre ("jueves semana 15 → martes
       semana 15") traslada entre dos días LECTIVOS por compresión de
       calendario, no por festivo — 14/14 páginas de semestre 2, consistente,
       no una anomalía. Solo es un ParseWarning real cuando origen Y destino
       son AMBOS no lectivos (mover una clase entre dos días sin clase no
       tiene sentido y sí sería incoherente). Cuando el origen es lectivo
       pero el traslado es coherente en lo demás, se registra como
       `ParseInfo` — confirma un traslado no motivado por festivo, no lo
       contradice.
    """
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []
    for note in notes:
        from_entry = day_status.get((note.from_week, note.from_weekday))
        to_entry = day_status.get((note.to_week, note.to_weekday))

        if from_entry is not None and from_entry.date != note.from_date:
            warnings.append(
                ParseWarning(
                    code="nota_fecha_incoherente",
                    message=(
                        f"origen: calendario da {from_entry.date} para semana "
                        f"{note.from_week}/weekday {note.from_weekday}, la nota dice {note.from_date}"
                    ),
                    page_index=page_index,
                )
            )
        if to_entry is not None and to_entry.date != note.to_date:
            warnings.append(
                ParseWarning(
                    code="nota_fecha_incoherente",
                    message=(
                        f"destino: calendario da {to_entry.date} para semana "
                        f"{note.to_week}/weekday {note.to_weekday}, la nota dice {note.to_date}"
                    ),
                    page_index=page_index,
                )
            )

        if to_entry is not None and to_entry.status != DayLectivoStatus.LECTIVO:
            warnings.append(
                ParseWarning(
                    code="nota_destino_no_es_lectivo",
                    message=f"{note.raw_text!r}: {note.to_date} está marcado {to_entry.status.value}, se esperaba lectivo",
                    page_index=page_index,
                )
            )

        if from_entry is not None:
            if (
                to_entry is not None
                and from_entry.status != DayLectivoStatus.LECTIVO
                and to_entry.status != DayLectivoStatus.LECTIVO
            ):
                warnings.append(
                    ParseWarning(
                        code="nota_traslado_entre_dias_no_lectivos",
                        message=(
                            f"{note.raw_text!r}: origen {note.from_date} ({from_entry.status.value}) "
                            f"y destino {note.to_date} ({to_entry.status.value}) ambos no lectivos — "
                            "traslado sin sentido"
                        ),
                        page_index=page_index,
                    )
                )
            elif from_entry.status == DayLectivoStatus.LECTIVO:
                infos.append(
                    ParseInfo(
                        code="nota_traslado_no_festivo",
                        message=(
                            f"{note.raw_text!r}: origen {note.from_date} marcado lectivo — "
                            "traslado no motivado por festivo (p.ej. compresión de fin de semestre)"
                        ),
                        page_index=page_index,
                    )
                )

    return warnings, infos

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

"""Contrato de la API web — deliberadamente distinto del modelo interno
(`horario_uca.model`): aquí el vocabulario es el que ve un alumno (sin
"ParseWarning", sin "itinerario" sin explicar, sin jerga de parseo), no el
que usa el pipeline internamente.
"""
from __future__ import annotations

from pydantic import BaseModel


class SeleccionEntrada(BaseModel):
    """Una asignatura elegida por el alumno. `itinerario` solo hace falta
    cuando la asignatura existe en más de un itinerario del mismo curso —
    el front lo pide explícitamente en ese caso, nunca por defecto."""

    acronimo: str
    curso: str
    itinerario: str | None = None
    grupos: list[str]


class GenerarPeticion(BaseModel):
    selecciones: list[SeleccionEntrada]
    titulo: str | None = None


class EventoSalida(BaseModel):
    fecha: str
    asignatura: str
    nombre_asignatura: str
    grupo: str
    tipo: str
    inicio: str
    fin: str
    aula: str | None
    trasladado_desde: str | None
    descripcion_traslado: str | None


class ConflictoLadoSalida(BaseModel):
    asignatura: str
    grupo: str
    curso: str | None
    inicio: str
    fin: str
    aula: str | None


class ConflictoSalida(BaseModel):
    fecha: str
    a: ConflictoLadoSalida
    b: ConflictoLadoSalida
    mensaje: str
    mismo_curso: bool
    """False cuando `a`/`b` vienen de cursos distintos (alumno con
    asignaturas de más de un curso a la vez, repita o no) — el front separa
    visualmente estos conflictos de los del mismo curso, ya que estos últimos se
    resuelven eligiendo otro grupo y los primeros puede que no tengan
    ninguna combinación sin choques."""


class GenerarRespuesta(BaseModel):
    eventos: list[EventoSalida]
    conflictos: list[ConflictoSalida]
    avisos: bool
    avisos_detalle: list[str]
    html: str
    ics: str

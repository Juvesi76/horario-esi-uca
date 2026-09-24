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

"""API web: capa fina sobre `pipeline.py` para que un alumno sin conocer la
CLI pueda subir el PDF, elegir curso/asignaturas/grupos de listas (nunca
tecleando un acrónimo o un código de grupo) y descargar su calendario.

Sin cuentas, sin base de datos, sin sesiones: el PDF llega en el cuerpo de
la petición y no se escribe a disco en ningún punto de este módulo, no
queda en logs. **El horario ya PARSEADO sí se cachea en memoria, indexado
por el hash SHA-256 del PDF — ver `_CACHE`/`_obtener_paginas` más abajo.**
Todos los alumnos de un mismo grado suben exactamente el mismo PDF oficial;
sin caché, cada uno pagaba el mismo coste de parseo (varios segundos) que
el primero, sin ninguna razón — es un documento público de la ESI, no un
dato del alumno. La selección de asignaturas del alumno, en cambio, NUNCA
se guarda: cada petición a `/api/generar` la manda de nuevo y se descarta
al responder, igual que antes.

**Por qué el parseo corre en un hilo, no en el bucle de eventos**: `fitz`/
`pymupdf` es C puro sin ningún punto de `await` — una llamada directa desde
un `async def` bloquea el bucle de eventos ENTERO mientras dura, así que
`GET /`/`GET /api/salud` (y cualquier otra petición concurrente) se quedan
sin responder hasta que termina, aunque no tengan nada que ver con el PDF
que se está parseando. Verificado con un caso real: `GET /` lanzada 0.3s
después de un `POST /api/catalogo` con el PDF de referencia no respondía
hasta que el `POST` terminaba del todo (ambas peticiones acababan casi al
mismo tiempo), en vez de responder casi al instante como cabría esperar de
un endpoint que no toca el PDF. `run_in_threadpool` (el mismo mecanismo que
usa FastAPI internamente para un endpoint `def` normal) mueve el trabajo de
CPU a un hilo aparte, dejando el bucle de eventos libre para atender otras
peticiones mientras tanto.

**Por qué el `MultiPartParser.spool_max_size` de Starlette se sube aquí**:
por defecto (1 MB) cualquier fichero subido más grande que eso deja de
mantenerse en memoria (`SpooledTemporaryFile`) y pasa a un fichero temporal
real en disco — se borra solo al cerrarse, pero mientras dura la petición sí
toca el disco. Verificado con el primitivo de la librería estándar
(`tempfile.SpooledTemporaryFile`): con 2 MB de contenido, `_rolled` es
`True` y aparece un fichero nuevo en el directorio temporal del sistema
mientras el objeto está abierto; desaparece al llamar a `.close()`. El PDF
de referencia (886 KB) se queda en memoria sin más, pero cualquier PDF algo
más grande —muy plausible para otro grado o un horario con más páginas— ya
lo tocaría. Se sube el umbral a `MAX_PDF_BYTES` para que ningún fichero
dentro del límite que este servicio acepta pase nunca por disco, ni
siquiera transitoriamente.

Toda la lógica de negocio (validar la selección, resolver fechas, detectar
conflictos, construir el HTML/.ics) vive en `pipeline.py`/`select/`/
`render/` y es exactamente la misma que usa `horario generar` — este
módulo solo traduce HTTP <-> esas funciones y adapta el vocabulario interno
(`ParseWarning`, "itinerario" a secas) al vocabulario de un alumno.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from datetime import date
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartParser

from horario_uca.model import CalendarEvent, ExamCalendar, SubjectSelection
from horario_uca.pipeline import (
    CatalogCombo,
    build_catalog,
    generate_calendar,
    parse_document_from_bytes,
    parse_exam_calendar_from_bytes,
)
from horario_uca.web.schemas import (
    ConflictoLadoSalida,
    ConflictoSalida,
    EventoSalida,
    GenerarRespuesta,
    SeleccionEntrada,
)

MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB — un horario de la ESI ronda 1 MB

# Starlette solo mantiene en memoria los ficheros subidos hasta 1 MB
# (`MultiPartParser.spool_max_size`); por encima de eso los escribe en un
# fichero temporal real (autoborrado, pero real mientras dura la petición —
# ver docstring del módulo). Se sube aquí, antes de que se procese ninguna
# petición, para que ningún PDF dentro de `MAX_PDF_BYTES` toque el disco.
MultiPartParser.spool_max_size = MAX_PDF_BYTES

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Horario ESI (UCA)", description="Genera tu horario de la ESI a partir del PDF oficial.")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Sirve el front (HTML/CSS/JS autocontenido, sin dependencias externas
    salvo las llamadas a /api/*) — ver `web/static/index.html`."""
    return FileResponse(_STATIC_DIR / "index.html")


# Sin autenticación ni datos sensibles de por medio (el PDF es un documento
# público de la ESI) — CORS permisivo para que el front pueda servirse desde
# otro origen durante el desarrollo. Revisar antes de un despliegue con
# dominio propio si se quiere restringir.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_DIA_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
_SELECCIONES_ADAPTER = TypeAdapter(list[SeleccionEntrada])


def _fecha_larga(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} de {_MES_ES[d.month - 1]}"


def _dia_de(iso: str) -> str:
    return _DIA_ES[date.fromisoformat(iso).weekday()]


def _descripcion_traslado(event: CalendarEvent) -> str | None:
    if not event.moved_from:
        return None
    return (
        f"Esta clase se traslada del {_dia_de(event.moved_from)} "
        f"{_fecha_larga(event.moved_from)} al {_dia_de(event.date)} {_fecha_larga(event.date)}."
    )


# Ningún horario/calendario real de este proyecto pasa de 24 páginas
# (`GII_horario2627.pdf`, el más grande de los 10 grados) — 40 da margen
# de sobra (~67%) sin dejar de acotar el caso "alguien sube por error/a
# propósito un PDF de cientos de páginas que no es un horario", cuyo
# tiempo de parseo escala aproximadamente con el número de páginas.
MAX_PDF_PAGES = 40

# Techo al tiempo de procesado de una petición completa (parseo O
# resolver+renderizar) — con el PDF de referencia (24 páginas) tardando
# ~8s en frío a los límites reales de un plan gratuito de hosting con CPU
# limitada (medido con Docker, 0.1 CPU/512 MB), 30s deja margen de sobra
# para un PDF cerca del límite de páginas sin dejar una petición de un PDF "raro" esperando
# indefinidamente. **Límite honesto, no absoluto**: Python no puede matar
# un hilo ya lanzado — `asyncio.wait_for` deja de ESPERAR la respuesta y
# libera al cliente con un error, pero el hilo de `run_in_threadpool`
# sigue corriendo en segundo plano hasta que termine por su cuenta. Lo que
# sí garantiza es que ninguna petición individual cuelga para siempre, y
# que el resto del servidor sigue respondiendo mientras tanto (ver
# `run_in_threadpool` más abajo — el bucle de eventos nunca se bloquea).
PROCESSING_TIMEOUT_SECONDS = 30


async def _leer_pdf_subido(pdf: UploadFile) -> bytes:
    data = await pdf.read()
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "pdf_demasiado_grande",
                "mensaje": f"El fichero supera el límite de {MAX_PDF_BYTES // (1024 * 1024)} MB.",
            },
        )
    if not data.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400,
            detail={"error": "archivo_no_es_pdf", "mensaje": "El fichero subido no es un PDF."},
        )
    return data


def _contar_paginas_o_error(data: bytes) -> None:
    """Rechazo barato ANTES de parsear nada — abrir un PDF para contar sus
    páginas es mucho más rápido que `parse_document_from_bytes` (que lee y
    procesa cada página entera), así que esta comprobación no consume el
    presupuesto de `PROCESSING_TIMEOUT_SECONDS`."""
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            page_count = doc.page_count
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "archivo_no_es_pdf", "mensaje": "No se ha podido leer el fichero como PDF."},
        ) from exc
    if page_count > MAX_PDF_PAGES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "pdf_demasiadas_paginas",
                "mensaje": f"Este PDF tiene {page_count} páginas; el límite es {MAX_PDF_PAGES}. Comprueba que es el horario correcto.",
            },
        )


def _parsear_pdf(data: bytes) -> list:
    try:
        return parse_document_from_bytes(data)
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "archivo_no_es_pdf", "mensaje": "No se ha podido leer el fichero como PDF."},
        ) from exc


async def _parsear_examenes_subidos(examenes: list[UploadFile] | None) -> list[ExamCalendar]:
    """Sin caché (a diferencia del horario): el PDF de convocatoria es
    mucho más ligero de parsear (una sola página, tabla) — no compensa la
    complejidad de indexarlo por hash para un coste ya bajo."""
    calendars: list[ExamCalendar] = []
    for exam_pdf in examenes or []:
        data = await _leer_pdf_subido(exam_pdf)
        try:
            calendars.append(parse_exam_calendar_from_bytes(data))
        except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "examen_pdf_no_es_pdf",
                    "mensaje": f"No se ha podido leer «{exam_pdf.filename}» como PDF de calendario de exámenes.",
                },
            ) from exc
    return calendars


# Caché del PARSEO por hash SHA-256 del PDF — nunca de la selección del
# alumno, ver docstring del módulo. Solo en memoria (un dict de proceso, se
# pierde al reiniciar/dormir el servicio, y eso es lo esperado — no hay
# ninguna intención de persistir nada entre reinicios). `OrderedDict` con
# `move_to_end` en cada acierto da un LRU barato sin dependencia nueva
# (`popitem(last=False)` para descartar el menos reciente al superar el
# tamaño máximo).
#
# **Por qué en NÚMERO de entradas, no en bytes acumulados**: se probaron
# dos proxies baratos para medir el tamaño de una entrada antes de cada
# inserción — ninguno sirve. `len(pickle.dumps(pages))` da 386 KB para el
# PDF de referencia, pero `tracemalloc` (memoria Python REALMENTE retenida,
# medida antes/después de parsear con `gc.collect()` de por medio) da
# 3145 KB — pickle serializa mucho más compacto de lo que el grafo de
# objetos vivos ocupa en memoria (cada `SchedulePage`/`ClassBlock`/etc. es
# un objeto pydantic con su propio overhead), así que un presupuesto en
# bytes basado en pickle dejaría entrar ~8x más memoria real de la que
# parece. Medir con `tracemalloc` en cada inserción sería exacto pero caro
# (activa el rastreo de asignaciones del proceso entero) para algo que ya
# tiene una cota simple y segura: el PDF de horario más grande de los 10
# grados (`GII_horario2627.pdf`, 24 páginas) retiene 3145 KB — medido una
# vez, no en cada petición — así que un tope en NÚMERO de entradas con
# margen real es más simple y más seguro que intentar acertar un
# presupuesto en bytes con un proxy que ya se demostró engañoso.
#
# En la práctica hay como mucho ~40 PDFs oficiales distintos en
# circulación (10 grados × su horario + sus 3 convocatorias de exámenes,
# y los de exámenes son muchísimo más pequeños que un horario). 48 entradas
# del tamaño MÁXIMO real (3145 KB) son ~149 MB — deja de sobra margen para
# cubrir esos ~40 documentos sin desalojo prematuro y sigue muy por debajo
# de los 512 MB del plan gratuito (con margen para la app + PyMuPDF +
# trabajo concurrente en curso, que en las mediciones de esta sesión pica
# en torno a 80 MB durante un parseo activo).
_CACHE_MAX_ENTRIES = 48
_CACHE: OrderedDict[str, list] = OrderedDict()


async def _obtener_paginas(data: bytes) -> list:
    _contar_paginas_o_error(data)
    key = hashlib.sha256(data).hexdigest()
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    try:
        pages = await asyncio.wait_for(run_in_threadpool(_parsear_pdf, data), timeout=PROCESSING_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "procesado_demasiado_lento",
                "mensaje": "Leer este PDF está tardando demasiado. Comprueba que es el horario correcto e inténtalo de nuevo.",
            },
        ) from exc
    _CACHE[key] = pages
    _CACHE.move_to_end(key)
    if len(_CACHE) > _CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)
    return pages


def _catalogo_o_error(pages: list) -> list[CatalogCombo]:
    catalog = build_catalog(pages)
    if not catalog:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "pdf_no_reconocido",
                "mensaje": "Este PDF no parece un horario de la ESI: no se ha encontrado ninguna asignatura.",
            },
        )
    return catalog


@app.get("/api/salud")
def salud() -> dict:
    return {"estado": "ok"}


@app.post("/api/catalogo", response_model=list[CatalogCombo])
async def api_catalogo(pdf: UploadFile = File(...)) -> list[CatalogCombo]:
    """Paso 1-2 del flujo: sube el PDF, recibe los combos curso/semestre/
    itinerario y, dentro de cada uno, las asignaturas con sus grupos — todo
    lo que el front necesita para pintar listas, sin que el alumno teclee
    nada."""
    data = await _leer_pdf_subido(pdf)
    pages = await _obtener_paginas(data)
    return _catalogo_o_error(pages)


@app.post("/api/generar", response_model=GenerarRespuesta)
async def api_generar(
    pdf: UploadFile = File(...),
    selecciones: str = Form(..., description="JSON de SeleccionEntrada[], ver web/schemas.py"),
    titulo: str | None = Form(None),
    examenes: list[UploadFile] | None = File(
        default=None, description="PDFs opcionales de convocatoria de exámenes (uno por convocatoria)"
    ),
    examenes_todas_convocatorias: bool = Form(
        default=False,
        description="El alumno pidió expresamente incluir TODAS las convocatorias subidas, no solo la automática",
    ),
) -> GenerarRespuesta:
    """Paso 3 del flujo: con el mismo PDF y la selección de grupos ya
    elegida en listas, valida, resuelve fechas, detecta conflictos y
    devuelve los eventos, el HTML autocontenido (para la vista previa) y el
    `.ics` — todo en una única respuesta, nada queda guardado en el
    servidor. `examenes` es opcional: 0, 1 o varios PDFs de convocatoria a
    la vez (febrero/junio/septiembre son documentos distintos)."""
    data = await _leer_pdf_subido(pdf)
    pages = await _obtener_paginas(data)
    _catalogo_o_error(pages)
    exam_calendars = await _parsear_examenes_subidos(examenes)

    try:
        entradas = _SELECCIONES_ADAPTER.validate_json(selecciones)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "seleccion_invalida", "mensaje": "La selección enviada no tiene un formato válido."},
        ) from exc

    if not entradas:
        raise HTTPException(
            status_code=400,
            detail={"error": "seleccion_vacia", "mensaje": "No has seleccionado ninguna asignatura."},
        )
    if any(not entrada.grupos and not entrada.solo_examen for entrada in entradas):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "seleccion_vacia",
                "mensaje": "Cada asignatura elegida necesita al menos un grupo marcado, o el modo 'solo examen'.",
            },
        )

    selections = [
        SubjectSelection(
            acronym=e.acronimo,
            curso=e.curso,
            itinerario=e.itinerario,
            groups=e.grupos,
            solo_examen=e.solo_examen,
            convocatorias=e.convocatorias,
        )
        for e in entradas
    ]

    # Resolver fechas + render HTML/.ics es más ligero que parsear el PDF
    # entero, pero sigue siendo trabajo de CPU sin ningún `await` dentro —
    # el mismo motivo que el parseo, mismo mecanismo y mismo techo de
    # tiempo (ver `PROCESSING_TIMEOUT_SECONDS`).
    try:
        outcome = await asyncio.wait_for(
            run_in_threadpool(
                generate_calendar,
                pages,
                selections,
                titulo=titulo or "Horario ESI (UCA)",
                verbose=False,
                exam_calendars=exam_calendars,
                include_all_exam_convocatorias=examenes_todas_convocatorias,
            ),
            timeout=PROCESSING_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "procesado_demasiado_lento",
                "mensaje": "Generar este calendario está tardando demasiado. Inténtalo de nuevo en un momento.",
            },
        ) from exc

    if not outcome.ok:
        primero = outcome.hard_errors[0]
        raise HTTPException(
            status_code=400,
            detail={"error": primero.code, "mensaje": "; ".join(w.message for w in outcome.hard_errors)},
        )

    selected_acronyms = {s.acronym for s in selections}
    relevant_pages = [p for p in pages if any(b.subject_acronym in selected_acronyms for b in p.blocks)]
    avisos_detalle = [w.message for p in relevant_pages for w in p.warnings]

    eventos = [
        EventoSalida(
            fecha=e.date,
            asignatura=e.subject_acronym,
            nombre_asignatura=e.subject_name,
            grupo=e.group_code,
            tipo=e.group_type,
            inicio=e.start_time,
            fin=e.end_time,
            aula=e.room,
            trasladado_desde=e.moved_from,
            descripcion_traslado=_descripcion_traslado(e),
        )
        for e in outcome.events
    ]

    def _conflicto_salida(c) -> ConflictoSalida:
        a, b = c.event_a, c.event_b
        mismo_curso = a.curso == b.curso
        a_curso = f" ({a.curso})" if not mismo_curso and a.curso else ""
        b_curso = f" ({b.curso})" if not mismo_curso and b.curso else ""
        if mismo_curso:
            mensaje = (
                f"El {_fecha_larga(c.date)} {a.subject_acronym} {a.group_code} y "
                f"{b.subject_acronym} {b.group_code} coinciden en horario — no se puede "
                "cursar esa combinación de grupos."
            )
        else:
            mensaje = (
                f"El {_fecha_larga(c.date)} {a.subject_acronym} {a.group_code}{a_curso} choca con "
                f"{b.subject_acronym} {b.group_code}{b_curso} — al cursar asignaturas de más de un curso a la "
                "vez puede que no exista ninguna combinación de grupos sin choques."
            )
        return ConflictoSalida(
            fecha=c.date,
            a=ConflictoLadoSalida(
                asignatura=a.subject_acronym, grupo=a.group_code, curso=a.curso,
                inicio=a.start_time, fin=a.end_time, aula=a.room,
            ),
            b=ConflictoLadoSalida(
                asignatura=b.subject_acronym, grupo=b.group_code, curso=b.curso,
                inicio=b.start_time, fin=b.end_time, aula=b.room,
            ),
            mensaje=mensaje,
            mismo_curso=mismo_curso,
        )

    conflictos = [_conflicto_salida(c) for c in outcome.conflicts]

    return GenerarRespuesta(
        eventos=eventos,
        conflictos=conflictos,
        avisos=bool(avisos_detalle),
        avisos_detalle=avisos_detalle,
        html=outcome.html,
        ics=outcome.ics.decode("utf-8"),
        examenes_incluidos=len(outcome.exams),
        examenes_convocatorias_incluidas=outcome.exam_convocatorias_incluidas,
        examenes_convocatorias_disponibles=outcome.exam_convocatorias_disponibles,
        examenes_solo_examen_disponibles=outcome.solo_examen_convocatorias,
        generado_el=outcome.generated_at,
        aprobado_el=outcome.approved_at,
    )

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

"""`horario diagnostico PDF...` — comprueba en dos minutos, sin recordar
nada del funcionamiento interno del proyecto, si un PDF nuevo (típicamente
el horario republicado para el curso siguiente) sigue teniendo el mismo
formato geométrico que los PDFs contra los que se calibró el parser.

No introduce ninguna validación nueva de dominio: empaqueta en un informe
legible lo que `parse/` y `resolve/` ya comprueban por su cuenta en cada
página (`page.warnings`/`page.infos`, `evento_en_dia_no_lectivo`), más un
puñado de comprobaciones que antes solo vivían como documentación interna
y no como código (formato de `curso` conocido, letra de grupo dentro de
`GROUP_TYPES`, layout de minicalendario reconocido).

La cifra de referencia para "esto debería dar 0" no es un fichero de
snapshot aparte: en los 10 PDFs de referencia comprobados contra el
parser actual, `page.warnings` agregado es `{}` (vacío) en todos.
Cualquier `ParseWarning`, letra de grupo fuera de `GROUP_TYPES`, formato
de `curso` desconocido o layout de minicalendario no reconocido es, por
definición, algo que NO pasaba antes — la señal es la propia aparición,
no una comparación contra un fichero guardado que además habría que
mantener al día.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from horario_uca.extract import RawPage, read_page
from horario_uca.model import GROUP_TYPES, ParseWarning, SchedulePage
from horario_uca.parse import parse_page
from horario_uca.parse.calendar import MONTH_NUM
from horario_uca.pipeline import resolve_document

# Formatos de `curso` vistos en los 10 PDFs de referencia (GII + otros 9
# grados). `curso` es `str | None` libre en el modelo a propósito, para
# que un formato nuevo no rompa el PARSEO; esta lista es solo para que el
# diagnóstico avise de que hay uno nuevo, no para bloquear nada.
KNOWN_CURSOS = {"1ºA", "1ºB", "2ºA", "2ºB", "3º", "4º", "5º", "4º/5º", None}

EXPECTED_WEEKS = {1: 16, 2: 15}

# Códigos de cruce que el documento pide destacar explícitamente, aparte
# del recuento general por código — todos deben dar 0 en un PDF sano.
CROSS_CHECK_CODES = [
    "bloque_sin_leyenda",
    "nota_fecha_incoherente",
    "nota_destino_no_es_lectivo",
    "nota_traslado_entre_dias_no_lectivos",
    "evento_en_dia_no_lectivo",
]


def _minicalendar_layout(raw: RawPage) -> str:
    """"horizontal" / "apilado" / "sin_minicalendario" — misma
    clasificación que `parse/calendar.py::parse_calendar_day_status`
    (comparar el rango de X de las cabeceras de mes contra su rango de Y),
    recalculada aquí de forma independiente: es solo para informar, así
    que no merece la pena tocar la firma de una función ya calibrada por
    otra cosa solo para exponer este dato."""
    headers = [(s.bbox[0], s.bbox[3]) for s in raw.spans if s.text.strip() in MONTH_NUM]
    if not headers:
        return "sin_minicalendario"
    x_spread = max(h[0] for h in headers) - min(h[0] for h in headers)
    y_spread = max(h[1] for h in headers) - min(h[1] for h in headers)
    return "horizontal" if x_spread >= y_spread else "apilado"


def _page_rotated(raw: RawPage) -> bool:
    """Un span con dirección distinta de la horizontal normal (1.0, 0.0)
    es buena señal de página con contenido rotado 90° — el bloque de
    notas puede estarlo según dónde cae el minicalendario, aunque el
    minicalendario en sí nunca lo está; el resto del contenido (rejilla,
    leyenda) siempre va horizontal."""
    return any(abs(s.direction[1]) > 0.5 for s in raw.spans)


def _weeks_complete(page: SchedulePage) -> bool | None:
    """`None` si el semestre no se pudo leer (no hay nada que comprobar);
    si no, todas las semanas 1..(N-1) deben tener 7 días exactos y la
    última (N) puede tener <= 7 (fin de periodo)."""
    expected = EXPECTED_WEEKS.get(page.semestre)
    if expected is None:
        return None
    by_week = {w.week_number: w for w in page.calendar}
    for week_number in range(1, expected):
        week = by_week.get(week_number)
        if week is None or len(week.days) != 7:
            return False
    last = by_week.get(expected)
    if last is not None and len(last.days) > 7:
        return False
    return True


@dataclass
class PageDiagnostico:
    page_index: int
    curso: str | None
    semestre: int | None
    itinerario: str | None
    n_blocks: int
    n_unparsed: int
    n_legend: int
    n_calendar_weeks: int
    n_notes: int
    rotated: bool
    layout: str
    weeks_complete: bool | None
    group_letters: set[str] = field(default_factory=set)


@dataclass
class DocumentDiagnostico:
    pdf_path: str
    pages: list[SchedulePage]
    page_diags: list[PageDiagnostico]
    resolve_warnings: list[ParseWarning]

    @property
    def all_warnings(self) -> list[ParseWarning]:
        return [w for p in self.pages for w in p.warnings] + self.resolve_warnings


def diagnosticar_pdf(pdf_path: str) -> DocumentDiagnostico:
    doc = pymupdf.open(pdf_path)
    pages: list[SchedulePage] = []
    page_diags: list[PageDiagnostico] = []
    for i in range(doc.page_count):
        raw = read_page(doc, i)
        page = parse_page(raw)
        pages.append(page)
        group_letters = {g.group_code[0] for b in page.blocks for g in b.groups if g.group_code}
        page_diags.append(
            PageDiagnostico(
                page_index=i,
                curso=page.curso,
                semestre=page.semestre,
                itinerario=page.itinerario,
                n_blocks=len(page.blocks),
                n_unparsed=len(page.unparsed),
                n_legend=len(page.legend),
                n_calendar_weeks=len(page.calendar),
                n_notes=len(page.notes),
                rotated=_page_rotated(raw),
                layout=_minicalendar_layout(raw),
                weeks_complete=_weeks_complete(page),
                group_letters=group_letters,
            )
        )
    _, resolve_warnings = resolve_document(pages)
    return DocumentDiagnostico(pdf_path=pdf_path, pages=pages, page_diags=page_diags, resolve_warnings=resolve_warnings)


def _fmt_page(pd: PageDiagnostico) -> str:
    itin = f" · {pd.itinerario}" if pd.itinerario else ""
    return f"p.{pd.page_index} ({pd.curso}, semestre {pd.semestre}{itin})"


def find_generation_mismatches(
    docs: list[DocumentDiagnostico],
) -> dict[tuple[str | None, int | None, str | None], dict[str, str]]:
    """{(curso, semestre, itinerario): {pdf_path: generation_timestamp}}
    solo para las combinaciones que aparecen en más de un `doc` de `docs`
    CON fechas de generación distintas — no es ruido (la fecha varía POR
    PÁGINA incluso dentro de un único PDF, ver `parse/header.py`) porque
    aquí se compara la MISMA combinación (curso, semestre, itinerario)
    entre DOCUMENTOS distintos: es la señal real de "estos dos ficheros no
    son la misma versión", útil para comparar un PDF viejo contra uno
    recién descargado."""
    combo_versions: dict[tuple[str | None, int | None, str | None], dict[str, str]] = {}
    for doc in docs:
        for page in doc.pages:
            if not page.generation_timestamp:
                continue
            key = (page.curso, page.semestre, page.itinerario)
            combo_versions.setdefault(key, {})[doc.pdf_path] = page.generation_timestamp
    return {k: v for k, v in combo_versions.items() if len(set(v.values())) > 1}


def build_diagnostico_report(pdf_paths: list[str]) -> tuple[str, bool]:
    """Devuelve (informe, sin_anomalias) — `sin_anomalias` es lo que decide
    el código de salida de la CLI (0 si todo limpio, 1 si hay algo que
    mirar), para que un script de comprobación anual pueda usarlo sin
    tener que parsear el texto del informe."""
    lines: list[str] = []
    overall_clean = True
    docs: list[DocumentDiagnostico] = []

    for pdf_path in pdf_paths:
        doc = diagnosticar_pdf(pdf_path)
        docs.append(doc)
        lines.append(f"{'=' * 70}")
        lines.append(f"  {pdf_path}")
        lines.append(f"{'=' * 70}")

        # Limpio para ESTE pdf — `overall_clean` (el veredicto agregado que
        # decide el código de salida) se actualiza al final de la
        # iteración; una anomalía en un PDF no debe "manchar" el veredicto
        # impreso de los demás PDFs de la misma tirada.
        pdf_clean = True

        n_pages = len(doc.page_diags)
        n_empty_unparsed = sum(1 for pd in doc.page_diags if pd.n_unparsed == 0)
        n_sin_bloques = sum(1 for pd in doc.page_diags if pd.n_blocks == 0)
        lines.append("")
        lines.append("=== Páginas ===")
        lines.append(f"  {n_pages} páginas totales")
        lines.append(f"  {n_empty_unparsed}/{n_pages} con unparsed vacío (0 rectángulos sin explicar)")
        lines.append(f"  {n_sin_bloques}/{n_pages} sin ningún ClassBlock" + ("" if n_sin_bloques == 0 else " — revisar cuáles (ya señaladas como 'pagina_sin_bloques' más abajo):"))
        for pd in doc.page_diags:
            if pd.n_blocks == 0:
                lines.append(f"    {_fmt_page(pd)}")

        lines.append("")
        lines.append("=== ParseWarning por código ===")
        lines.append("  (en los PDFs de referencia ya comprobados, TODOS estos códigos dan 0 —")
        lines.append("   cualquiera que aparezca aquí es una señal a mirar, no ruido esperado)")
        by_code = Counter(w.code for w in doc.all_warnings)
        if not by_code:
            lines.append("  ninguno — 0 ParseWarning en todo el documento")
        else:
            pdf_clean = False
            for code, count in by_code.most_common():
                lines.append(f"  [{code}] × {count}")

        lines.append("")
        lines.append("=== Semanas por página (15/16 según semestre, 7 días cada una) ===")
        semanas_mal = [pd for pd in doc.page_diags if pd.weeks_complete is False]
        semanas_no_verificables = [pd for pd in doc.page_diags if pd.weeks_complete is None]
        if not semanas_mal:
            lines.append("  todas correctas")
        else:
            pdf_clean = False
            for pd in semanas_mal:
                lines.append(f"  MAL: {_fmt_page(pd)} — {pd.n_calendar_weeks} semanas, alguna no tiene el número de días esperado")
        if semanas_no_verificables:
            lines.append(
                f"  sin verificar (semestre no reconocido): "
                + ", ".join(_fmt_page(pd) for pd in semanas_no_verificables)
            )

        lines.append("")
        lines.append("=== Notas al pie por página ===")
        total_notas = sum(pd.n_notes for pd in doc.page_diags)
        n_rotadas = sum(1 for pd in doc.page_diags if pd.rotated)
        lines.append(f"  {total_notas} notas en total, {n_rotadas}/{n_pages} páginas con contenido rotado 90°")
        sin_notas = [pd for pd in doc.page_diags if pd.n_notes == 0]
        if sin_notas:
            lines.append(f"  {len(sin_notas)} páginas sin ninguna nota (puede ser legítimo, ver ParseWarning 'notas_vacias' arriba)")

        lines.append("")
        lines.append("=== Layouts de minicalendario ===")
        layout_counts = Counter(pd.layout for pd in doc.page_diags)
        for layout, count in layout_counts.most_common():
            lines.append(f"  {layout}: {count} páginas")
        desconocidos = [
            pd for pd in doc.page_diags
            if pd.layout == "sin_minicalendario" and doc.pages[pd.page_index].academic_year is not None
        ]
        if desconocidos:
            pdf_clean = False
            lines.append("  AVISO: página con año académico leído pero sin cabeceras de mes reconocibles:")
            for pd in desconocidos:
                lines.append(f"    {_fmt_page(pd)}")

        lines.append("")
        lines.append("=== Letras de grupo fuera de GROUP_TYPES ===")
        lines.append(f"  letras conocidas: {sorted(GROUP_TYPES)}")
        letras_desconocidas: dict[str, list[int]] = {}
        for pd in doc.page_diags:
            for letra in pd.group_letters - set(GROUP_TYPES):
                letras_desconocidas.setdefault(letra, []).append(pd.page_index)
        if not letras_desconocidas:
            lines.append("  ninguna — todas las letras de grupo vistas están en GROUP_TYPES")
        else:
            pdf_clean = False
            for letra, paginas in sorted(letras_desconocidas.items()):
                lines.append(f"  {letra!r}: páginas {paginas}")

        lines.append("")
        lines.append("=== Formatos de curso distintos de los conocidos ===")
        lines.append(f"  conocidos: {sorted(c for c in KNOWN_CURSOS if c)}")
        cursos_desconocidos = {
            pd.curso: pd.page_index for pd in doc.page_diags if pd.curso not in KNOWN_CURSOS
        }
        if not cursos_desconocidos:
            lines.append("  ninguno")
        else:
            pdf_clean = False
            for curso, page_index in sorted(cursos_desconocidos.items(), key=lambda kv: str(kv[0])):
                lines.append(f"  {curso!r} (p.{page_index})")

        lines.append("")
        lines.append("=== Validaciones cruzadas (deben dar 0 en todas) ===")
        for code in CROSS_CHECK_CODES:
            count = by_code.get(code, 0)
            marca = "OK" if count == 0 else "MAL"
            lines.append(f"  [{marca}] {code}: {count}")

        lines.append("")
        if pdf_clean:
            lines.append("VEREDICTO: sin anomalías.")
        else:
            lines.append("VEREDICTO: hay cosas que mirar — ver las secciones marcadas arriba.")
        lines.append("")

        overall_clean = overall_clean and pdf_clean

    if len(pdf_paths) > 1:
        lines.append(f"{'=' * 70}")
        lines.append("=== Comparación entre los PDFs dados ===")
        mismatches = find_generation_mismatches(docs)
        if not mismatches:
            lines.append("  ninguna combinación (curso, semestre, itinerario) compartida tiene fechas distintas")
        else:
            overall_clean = False
            for (curso, semestre, itinerario), versions in sorted(mismatches.items(), key=lambda kv: str(kv[0])):
                itin = f" · {itinerario}" if itinerario else ""
                lines.append(f"  AVISO: {curso}, semestre {semestre}{itin} tiene fechas de generación distintas entre PDFs:")
                for pdf_path, ts in versions.items():
                    lines.append(f"    {pdf_path}: {ts}")
        lines.append("")
        lines.append(
            "VEREDICTO GENERAL: sin anomalías en ningún PDF."
            if overall_clean
            else "VEREDICTO GENERAL: al menos un PDF tiene algo que mirar — ver arriba."
        )

    return "\n".join(lines), overall_clean


__all__ = ["DocumentDiagnostico", "PageDiagnostico", "build_diagnostico_report", "diagnosticar_pdf"]

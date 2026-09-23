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

"""Cruce interno DENTRO de una página: todo `ClassBlock.subject_acronym`
debe existir en `page.legend` de esa misma página, y viceversa (con menos
severidad) — ver `parse/__init__.py::_check_legend_block_consistency`.

Esta comprobación habría cazado el bug de `LEGEND_RE` (acrónimos con
minúscula o dígito perdidos de la leyenda — `ApC`, `MV(a)`, `MV(e)`,
`FIS1`, `FIS2`, `MF1`, `MF2`, `ERM1`, `ERM2`...) desde el primer día, sin
necesitar el PDF de exámenes para contradecirlo por casualidad. Revertir
`LEGEND_RE` a propósito al `[A-Z\\-]+` antiguo y ejecutar el barrido real
de los 10 PDFs (no solo GII) confirmó que el fallo real era MUCHO más
amplio que los 3 casos documentados al arreglar `LEGEND_RE` la primera
vez: 36 avisos `bloque_sin_leyenda` en 9 de los 10 PDFs (todo acrónimo con
un dígito, como `FIS1`/`MF1`/`ERM1`, fallaba igual que uno con minúscula,
porque `[A-Z\\-]+` tampoco admite dígitos) — la primera verificación
("barrido de 194 acrónimos, solo 3 con minúscula") solo miraba
`str.isupper()` sobre el resultado YA arreglado, y `"FIS1".isupper()` es
`True` en Python (los dígitos no cuentan como minúscula), así que ese
barrido nunca podría haber visto estos casos aunque hubiera corrido
contra el regex antiguo. Con el `\\S+` actual, los 36 avisos desaparecen
por completo en los 10 PDFs.
"""
import re

import pymupdf
import pytest

from horario_uca.extract import read_page
from horario_uca.model import ClassBlock, SubjectLegendEntry
from horario_uca.parse import _check_legend_block_consistency, parse_page
from horario_uca.pipeline import parse_document

OTHER_PDFS = [
    "GIA.horario2627.pdf", "GIDIDP.horario2627.pdf", "GIE.horario2627.pdf",
    "GIEI.horario2627.pdf", "GIE-GIEI.horario2627.pdf", "GIM.horario2627.pdf",
    "GIM-GIDIDP.horario2627.pdf", "GIM-GIE.horario2627.pdf", "GITI.horario2627.pdf",
]


def _minimal_block(acronym: str) -> ClassBlock:
    return ClassBlock(
        subject_acronym=acronym, day_of_week=0, start_time="08:30", end_time="10:00",
        room=None, groups=[], bbox=(0.0, 0.0, 1.0, 1.0),
    )


def test_la_comprobacion_reacciona_a_un_bloque_sin_leyenda():
    """Unidad, sin PDF real: un bloque de una asignatura ausente de la
    leyenda dispara `ParseWarning`; una entrada de leyenda sin bloques
    (TFG-like) dispara solo `ParseInfo`, nunca warning."""
    legend = [SubjectLegendEntry(acronym="CAL", name="Cálculo", code="21714009", color=(228, 26, 28))]
    blocks = [_minimal_block("CAL"), _minimal_block("ApC")]

    warnings, infos = _check_legend_block_consistency(0, legend, blocks)

    assert [w.code for w in warnings] == ["bloque_sin_leyenda"]
    assert "ApC" in warnings[0].message
    assert warnings[0].page_index == 0


def test_leyenda_sin_bloque_es_info_no_warning():
    legend = [
        SubjectLegendEntry(acronym="CAL", name="Cálculo", code="21714009", color=(228, 26, 28)),
        SubjectLegendEntry(acronym="TFG", name="Trabajo Fin de Grado", code="21714064", color=(0, 0, 0)),
    ]
    blocks = [_minimal_block("CAL")]

    warnings, infos = _check_legend_block_consistency(0, legend, blocks)

    assert warnings == []
    assert [i.code for i in infos] == ["leyenda_sin_bloque"]
    assert "TFG" in infos[0].message


def test_ninguna_pagina_de_gii_tiene_bloque_sin_leyenda(sample_pdf_path):
    doc = pymupdf.open(sample_pdf_path)
    failures = []
    for page_index in range(doc.page_count):
        page = parse_page(read_page(doc, page_index))
        fired = [w.message for w in page.warnings if w.code == "bloque_sin_leyenda"]
        failures.extend(f"p{page_index}: {m}" for m in fired)
    assert not failures, "\n" + "\n".join(failures)


@pytest.mark.parametrize("pdf_name", OTHER_PDFS)
def test_ningun_bloque_sin_leyenda_en_otros_grados(pdf_name):
    from pathlib import Path

    pdf_path = Path(__file__).parent.parent / "data" / pdf_name
    if not pdf_path.exists():
        pytest.skip(f"fixture exploratoria no presente: {pdf_name}")
    pages = parse_document(str(pdf_path))
    failures = [
        f"p{page.page_index}: {w.message}"
        for page in pages
        for w in page.warnings
        if w.code == "bloque_sin_leyenda"
    ]
    assert not failures, "\n" + "\n".join(failures)


def test_con_el_legend_re_antiguo_la_comprobacion_habria_disparado_36_veces():
    """Reproduce el regex antiguo (`[A-Z\\-]+`, sin dígitos ni minúsculas)
    dentro del propio test, vía monkeypatch temporal de
    `horario_uca.parse.legend.LEGEND_RE` — no toca el fichero fuente. Deja
    constancia permanente de que la comprobación no es decorativa: con el
    regex roto reacciona en 9 de los 10 PDFs; restaurado, no reacciona en
    ninguno (ver los tests de arriba)."""
    import horario_uca.parse.legend as legend_mod

    from pathlib import Path

    pdf_path = Path(__file__).parent.parent / "data" / "GII_horario2627.pdf"
    if not pdf_path.exists():
        pytest.skip(f"fixture no presente: {pdf_path.name} (ver README, 'De dónde descargar los PDFs')")

    original = legend_mod.LEGEND_RE
    legend_mod.LEGEND_RE = re.compile(r"^([A-Z\-]+) - (.+) - (\d{8})$")
    try:
        pages = parse_document(str(pdf_path))
        total = sum(1 for page in pages for w in page.warnings if w.code == "bloque_sin_leyenda")
        assert total >= 1  # ApC en GII, como mínimo
    finally:
        legend_mod.LEGEND_RE = original

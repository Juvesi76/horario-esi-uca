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

"""`horario diagnostico` — verificado contra el PDF de referencia (debe dar
"sin anomalías") y contra un PDF de otro grado con anomalías reales
conocidas (debe señalarlas, no darlas por buenas en silencio)."""
from pathlib import Path

import pytest

from horario_uca.diagnostico import (
    DocumentDiagnostico,
    build_diagnostico_report,
    diagnosticar_pdf,
    find_generation_mismatches,
)
from horario_uca.model import SchedulePage

DATA_DIR = Path(__file__).parent.parent / "data"
GIA_PDF = DATA_DIR / "GIA.horario2627.pdf"


def test_pdf_referencia_sin_anomalias(sample_pdf_path):
    report, sin_anomalias = build_diagnostico_report([str(sample_pdf_path)])
    assert sin_anomalias is True
    assert "VEREDICTO: sin anomalías." in report
    assert "ninguno — 0 ParseWarning en todo el documento" in report
    # 54 notas / 14 páginas rotadas, cifras ya fijadas como constantes de
    # test del proyecto — verificado aquí de nuevo, no solo copiado.
    assert "54 notas en total, 14/24 páginas con contenido rotado" in report


def test_pdf_referencia_semanas_completas(sample_pdf_path):
    doc = diagnosticar_pdf(str(sample_pdf_path))
    assert all(pd.weeks_complete is not False for pd in doc.page_diags)


def test_gia_senala_anomalias_reales_conocidas():
    """GIA (Ingeniería Aeroespacial) no tiene cohortes A/B en 1º/2º (curso
    llega como "1º"/"2º", no "1ºA"/"1ºB") y usa una letra de grupo ("E")
    fuera de GROUP_TYPES — dos diferencias de formato reales frente al PDF
    de referencia, exactamente el tipo de cosa que este comando debe
    señalar en vez de dar por buena en silencio."""
    if not GIA_PDF.exists():
        pytest.skip(f"fixture no presente: {GIA_PDF.name}")
    report, sin_anomalias = build_diagnostico_report([str(GIA_PDF)])
    assert sin_anomalias is False
    assert "'E': páginas" in report
    assert "'1º' (p." in report or "'2º' (p." in report


def test_varios_pdfs_veredicto_general_no_se_contamina_entre_ellos(sample_pdf_path):
    """El veredicto de un PDF limpio no debe salir "sucio" solo porque
    otro PDF de la misma tirada tenga anomalías — cada uno lleva su propio
    veredicto, y el general es la combinación de los dos, no un contador
    compartido que se pisa entre iteraciones."""
    if not GIA_PDF.exists():
        pytest.skip(f"fixture no presente: {GIA_PDF.name}")
    report, sin_anomalias = build_diagnostico_report([str(GIA_PDF), str(sample_pdf_path)])
    assert sin_anomalias is False
    assert "VEREDICTO GENERAL: al menos un PDF tiene algo que mirar" in report
    # El PDF de referencia, aunque vaya SEGUNDO en la lista, debe seguir
    # imprimiendo su propio "sin anomalías" — no heredar el estado sucio
    # del primero.
    assert "VEREDICTO: sin anomalías." in report


def test_pdf_referencia_generation_timestamp_y_approval_date(sample_pdf_path):
    """Verificado con datos reales de las 13 páginas/documentos de
    referencia (10 horarios + 3 calendarios de exámenes, no repetido aquí
    en texto): la marca de 14 dígitos del pie SIEMPRE parsea como fecha
    válida y VARÍA por página dentro de un mismo documento (12 valores
    distintos en las 24 páginas de este PDF); "Aprobado en Junta de
    Escuela" es la MISMA fecha en todas las páginas de un documento."""
    doc = diagnosticar_pdf(str(sample_pdf_path))
    timestamps = {p.generation_timestamp for p in doc.pages if p.generation_timestamp}
    assert len(timestamps) > 1, "la marca de generación debe variar entre páginas del mismo PDF"
    approvals = {p.approval_date for p in doc.pages if p.approval_date}
    assert approvals == {"2026-05-11"}


def _synthetic_page(curso, semestre, generation_timestamp):
    return SchedulePage(
        page_index=0,
        academic_year="2026-2027",
        curso=curso,
        semestre=semestre,
        itinerario=None,
        generation_timestamp=generation_timestamp,
    )


def test_find_generation_mismatches_detecta_version_distinta_del_mismo_curso():
    doc_a = DocumentDiagnostico(
        pdf_path="viejo.pdf",
        pages=[_synthetic_page("1ºA", 1, "2026-01-01T00:00:00")],
        page_diags=[],
        resolve_warnings=[],
    )
    doc_b = DocumentDiagnostico(
        pdf_path="nuevo.pdf",
        pages=[_synthetic_page("1ºA", 1, "2026-09-15T16:04:00")],
        page_diags=[],
        resolve_warnings=[],
    )
    mismatches = find_generation_mismatches([doc_a, doc_b])
    assert mismatches == {
        ("1ºA", 1, None): {"viejo.pdf": "2026-01-01T00:00:00", "nuevo.pdf": "2026-09-15T16:04:00"}
    }


def test_find_generation_mismatches_no_avisa_por_variacion_normal_dentro_de_un_pdf():
    """La fecha varía POR PÁGINA dentro de un único PDF (comprobado
    arriba) — comparar solo DOS páginas del MISMO documento con distinto
    (curso, semestre) nunca debe producir un aviso: `find_generation_
    mismatches` compara la MISMA combinación entre documentos DISTINTOS,
    nunca (curso, semestre) distintos dentro del mismo."""
    doc = DocumentDiagnostico(
        pdf_path="horario.pdf",
        pages=[
            _synthetic_page("1ºA", 1, "2026-09-15T16:04:00"),
            _synthetic_page("1ºB", 1, "2026-09-18T10:13:00"),
        ],
        page_diags=[],
        resolve_warnings=[],
    )
    assert find_generation_mismatches([doc]) == {}


def test_dos_copias_del_mismo_pdf_no_dan_falso_positivo(sample_pdf_path):
    report, sin_anomalias = build_diagnostico_report([str(sample_pdf_path), str(sample_pdf_path)])
    assert sin_anomalias is True
    assert "ninguna combinación (curso, semestre, itinerario) compartida tiene fechas distintas" in report


def test_exit_code_cli(sample_pdf_path):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "horario_uca.cli", "diagnostico", str(sample_pdf_path)],
        capture_output=True,
    )
    assert result.returncode == 0

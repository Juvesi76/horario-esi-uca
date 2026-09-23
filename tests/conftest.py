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

from pathlib import Path

import pytest

# Los PDFs de la UCA no viven en el repositorio (son documentos con
# derechos de la universidad, no del proyecto — ver README, "De dónde
# descargar los PDFs"). Cualquier test que dependa de uno de ellos debe
# saltarse con un mensaje claro si no está en `data/`, nunca fallar con un
# error de fichero no encontrado — así la suite sigue siendo útil (para
# quien clona el repo sin los PDFs) y sigue siendo completa (para quien sí
# los tiene, tras `scripts/descargar_pdfs.py` o descargándolos a mano).
FIXTURE_PDF = Path(__file__).parent.parent / "data" / "GII_horario2627.pdf"


@pytest.fixture
def sample_pdf_path() -> Path:
    if not FIXTURE_PDF.exists():
        pytest.skip(f"fixture no presente: {FIXTURE_PDF.name} (ver README, 'De dónde descargar los PDFs')")
    return FIXTURE_PDF

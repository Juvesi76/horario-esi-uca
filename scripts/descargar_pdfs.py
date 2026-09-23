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

"""Descarga a `data/` los PDFs oficiales de horarios/exámenes de la ESI —
opcional, solo hace falta si quieres correr la suite completa (sin él, los
tests que dependen de un PDF se saltan solos, ver `tests/conftest.py`).

**PENDIENTE DE RELLENAR**: este proyecto nunca inventa URLs —
`URLS` está vacío a propósito. Añade aquí cada PDF
como lo publica la web de horarios de la ESI, con su nombre exacto en
`data/` (los mismos que usan los tests, p.ej. "GII_horario2627.pdf"), y
confirma que la URL es estable (no cambia de curso a curso ni de sesión a
sesión) antes de depender de ella.

Uso: python scripts/descargar_pdfs.py
"""
from pathlib import Path
from urllib.request import urlretrieve

DATA_DIR = Path(__file__).parent.parent / "data"

# "nombre_en_data.pdf": "https://url-estable-de-la-esi/..."
URLS: dict[str, str] = {}


def main() -> None:
    if not URLS:
        print("URLS está vacío — rellénalo con las direcciones reales de la web de horarios de la ESI (ver el docstring de este fichero).")
        return
    DATA_DIR.mkdir(exist_ok=True)
    for name, url in URLS.items():
        dest = DATA_DIR / name
        if dest.exists():
            print(f"ya existe, se salta: {name}")
            continue
        print(f"descargando {name}...")
        urlretrieve(url, dest)
    print("hecho.")


if __name__ == "__main__":
    main()

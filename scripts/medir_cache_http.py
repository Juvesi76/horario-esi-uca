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

"""Mide el efecto real de la caché por hash sobre HTTP real, con conexión
PERSISTENTE (una sola, reutilizada) — con una conexión nueva por petición
la medición se contamina con el coste de abrir esa conexión, que no tiene
nada que ver con la caché (una primera medición con conexión nueva por
petición dio ~0.5s, de los que ~15-30ms eran trabajo real del servidor y
el resto, coste de conexión repetido).

Uso: con el servidor ya corriendo en otra terminal/contenedor,
    python scripts/medir_cache_http.py http://localhost:8000 data/GII_horario2627.pdf
"""
import sys
import time

import httpx


def main(base_url: str, pdf_path: str) -> None:
    data = open(pdf_path, "rb").read()
    with httpx.Client(base_url=base_url, timeout=60) as client:
        t0 = time.perf_counter()
        r = client.post("/api/catalogo", files={"pdf": ("horario.pdf", data, "application/pdf")})
        print(f"en frio: {(time.perf_counter() - t0) * 1000:.0f} ms, status {r.status_code}")
        for i in range(3):
            t0 = time.perf_counter()
            r = client.post("/api/catalogo", files={"pdf": ("horario.pdf", data, "application/pdf")})
            print(f"con cache #{i}, misma conexion: {(time.perf_counter() - t0) * 1000:.0f} ms, status {r.status_code}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

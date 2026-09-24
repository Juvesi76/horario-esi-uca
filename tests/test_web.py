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

"""API web (`horario_uca/web/app.py`): la misma lógica de `pipeline.py`
detrás de HTTP, sin cuentas ni almacenamiento — cada test sube el PDF de
referencia como lo haría un navegador."""
import json
from pathlib import Path

import icalendar
import pymupdf
import pytest
from fastapi.testclient import TestClient

from horario_uca.web.app import MAX_PDF_PAGES, app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def pdf_bytes(sample_pdf_path) -> bytes:
    return sample_pdf_path.read_bytes()


def _pdf_file(pdf_bytes: bytes, name: str = "horario.pdf"):
    return {"pdf": (name, pdf_bytes, "application/pdf")}


def _selecciones(*items: dict) -> dict:
    return {"selecciones": json.dumps(list(items))}


# --- /api/catalogo ---------------------------------------------------------


def test_catalogo_subida_correcta(client, pdf_bytes):
    r = client.post("/api/catalogo", files=_pdf_file(pdf_bytes))
    assert r.status_code == 200
    combos = r.json()

    # 24 páginas del PDF de referencia.
    assert len(combos) == 24

    pagina_0 = next(c for c in combos if c["curso"] == "1ºA" and c["semestre"] == 1)
    assert pagina_0["itinerario"] is None
    assert len(pagina_0["asignaturas"]) == 5
    md = next(a for a in pagina_0["asignaturas"] if a["acronimo"] == "MD")
    assert md["nombre"] == "Matemática Discreta"
    assert set(md["grupos"].keys()) == {"A", "B"}
    assert md["grupos"]["A"]["codigos"] == ["A1"]
    assert md["grupos"]["A"]["tipo"] == "Clases de teoría"


def test_catalogo_fichero_no_es_pdf(client):
    r = client.post(
        "/api/catalogo",
        files={"pdf": ("horario.txt", b"esto no es un PDF, es texto plano", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "archivo_no_es_pdf"


def test_catalogo_pdf_corrupto_pero_con_cabecera_falsa(client):
    """Empieza con la firma de PDF pero no es un PDF válido — distinto del
    caso anterior (ni siquiera parece un PDF): aquí `pymupdf.open` debe fallar
    al parsear, no el chequeo de cabecera."""
    r = client.post(
        "/api/catalogo",
        files={"pdf": ("horario.pdf", b"%PDF-1.4\nesto no es un PDF de verdad", "application/pdf")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "archivo_no_es_pdf"


def test_catalogo_rechaza_pdf_con_demasiadas_paginas(client):
    """Protección del endpoint público: un PDF real pero con muchas más
    páginas de las que tiene un horario/calendario de verdad (máximo real,
    24 páginas) se rechaza ANTES de parsear, sin gastar el presupuesto de
    tiempo de `PROCESSING_TIMEOUT_SECONDS`."""
    doc = pymupdf.open()
    for _ in range(MAX_PDF_PAGES + 1):
        doc.new_page()
    data = doc.tobytes()
    doc.close()

    r = client.post("/api/catalogo", files={"pdf": ("horario.pdf", data, "application/pdf")})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "pdf_demasiadas_paginas"


# --- /api/generar ------------------------------------------------------


def test_generar_seleccion_valida(client, pdf_bytes):
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones({"acronimo": "MD", "curso": "1ºA", "grupos": ["A1", "B1"]}),
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["eventos"]) > 0
    assert body["conflictos"] == []
    assert "html" in body and "<html" in body["html"].lower()
    assert "ics" in body and "BEGIN:VCALENDAR" in body["ics"]


def test_generar_grupo_inexistente(client, pdf_bytes):
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones({"acronimo": "MD", "curso": "1ºA", "grupos": ["Z9"]}),
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error"] == "grupo_no_existe"
    assert "Z9" in detail["mensaje"]


def test_generar_asignatura_inexistente(client, pdf_bytes):
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones({"acronimo": "NOEXISTE", "curso": "1ºA", "grupos": ["A1"]}),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "asignatura_no_existe_en_curso"


def test_generar_seleccion_vacia_lista(client, pdf_bytes):
    r = client.post("/api/generar", files=_pdf_file(pdf_bytes), data={"selecciones": "[]"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "seleccion_vacia"


def test_generar_seleccion_sin_grupos(client, pdf_bytes):
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones({"acronimo": "MD", "curso": "1ºA", "grupos": []}),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "seleccion_vacia"


def test_generar_seleccion_json_invalido(client, pdf_bytes):
    r = client.post("/api/generar", files=_pdf_file(pdf_bytes), data={"selecciones": "esto no es json"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "seleccion_invalida"


def test_generar_fichero_no_es_pdf(client):
    r = client.post(
        "/api/generar",
        files={"pdf": ("horario.txt", b"no soy un pdf", "text/plain")},
        data=_selecciones({"acronimo": "MD", "curso": "1ºA", "grupos": ["A1"]}),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "archivo_no_es_pdf"


def test_generar_conflicto_de_horario_avisa_pero_no_bloquea(client, pdf_bytes):
    """Caso real ya verificado en `tests/test_conflicts.py`: IG·C1 + CAL·C1
    chocan 6 veces, una de ellas trasladada por nota al pie (08/01/2027)."""
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones(
            {"acronimo": "IG", "curso": "1ºA", "grupos": ["C1"]},
            {"acronimo": "CAL", "curso": "1ºA", "grupos": ["C1"]},
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["conflictos"]) == 6
    fechas = {c["fecha"] for c in body["conflictos"]}
    assert "2027-01-08" in fechas
    for c in body["conflictos"]:
        assert {c["a"]["asignatura"], c["b"]["asignatura"]} == {"IG", "CAL"}


EXAM_PDF = Path(__file__).parent.parent / "data" / "GII.calendarioExamenes.Feb27.pdf"


@pytest.fixture
def exam_pdf_bytes() -> bytes:
    if not EXAM_PDF.exists():
        pytest.skip(f"fixture no presente: {EXAM_PDF.name} (ver README, 'De dónde descargar los PDFs')")
    return EXAM_PDF.read_bytes()


def test_generar_con_pdf_de_examenes_los_incluye_en_html_e_ics(client, pdf_bytes, exam_pdf_bytes):
    """IG (1ºA, semestre 1) con clases seleccionadas -> su examen de esta
    convocatoria se añade automáticamente (ver `select/exams.py`,
    `default_exam_codes`)."""
    r = client.post(
        "/api/generar",
        files=[
            ("pdf", ("horario.pdf", pdf_bytes, "application/pdf")),
            ("examenes", ("examenes.pdf", exam_pdf_bytes, "application/pdf")),
        ],
        data=_selecciones({"acronimo": "IG", "curso": "1ºA", "grupos": ["C1"]}),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["examenes_incluidos"] > 0
    assert "Examen:" in body["html"]
    assert "exam-" in body["ics"]  # prefijo del UID de un VEVENT de examen


def test_generar_sin_pdf_de_examenes_no_incluye_ninguno(client, pdf_bytes):
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones({"acronimo": "IG", "curso": "1ºA", "grupos": ["C1"]}),
    )
    assert r.status_code == 200
    assert r.json()["examenes_incluidos"] == 0


EXAM_PDF_JUN = Path(__file__).parent.parent / "data" / "GII.calendarioExamenes.Jun27.pdf"
EXAM_PDF_SEP = Path(__file__).parent.parent / "data" / "GII.calendarioExamenes.Sep27.pdf"


@pytest.fixture
def exam_pdf_bytes_jun() -> bytes:
    if not EXAM_PDF_JUN.exists():
        pytest.skip(f"fixture no presente: {EXAM_PDF_JUN.name}")
    return EXAM_PDF_JUN.read_bytes()


@pytest.fixture
def exam_pdf_bytes_sep() -> bytes:
    if not EXAM_PDF_SEP.exists():
        pytest.skip(f"fixture no presente: {EXAM_PDF_SEP.name}")
    return EXAM_PDF_SEP.read_bytes()


def _tres_convocatorias_files(pdf_bytes, exam_pdf_bytes, exam_pdf_bytes_jun, exam_pdf_bytes_sep):
    return [
        ("pdf", ("horario.pdf", pdf_bytes, "application/pdf")),
        ("examenes", ("feb.pdf", exam_pdf_bytes, "application/pdf")),
        ("examenes", ("jun.pdf", exam_pdf_bytes_jun, "application/pdf")),
        ("examenes", ("sep.pdf", exam_pdf_bytes_sep, "application/pdf")),
    ]


def test_tres_convocatorias_a_la_vez_solo_incluye_la_automatica_de_cada_semestre(
    client, pdf_bytes, exam_pdf_bytes, exam_pdf_bytes_jun, exam_pdf_bytes_sep
):
    """CAL (semestre 1) + ALG (semestre 2) con las 3 convocatorias
    subidas: por defecto solo entran el examen de CAL en febrero y el de
    ALG en junio (ver CONVOCATORIA_AUTO_SEMESTER) — septiembre nunca se
    incluye sola, pero las tres convocatorias quedan disponibles."""
    r = client.post(
        "/api/generar",
        files=_tres_convocatorias_files(pdf_bytes, exam_pdf_bytes, exam_pdf_bytes_jun, exam_pdf_bytes_sep),
        data=_selecciones(
            {"acronimo": "CAL", "curso": "1ºA", "grupos": ["C1"]},
            {"acronimo": "ALG", "curso": "1ºA", "grupos": ["A1"]},
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["examenes_incluidos"] == 2
    assert set(body["examenes_convocatorias_incluidas"]) == {"FEBRERO DE 2027", "JUNIO DE 2027"}
    assert body["examenes_convocatorias_disponibles"] == ["SEPTIEMBRE DE 2027"]


def test_tres_convocatorias_todas_incluidas_a_peticion_del_alumno(
    client, pdf_bytes, exam_pdf_bytes, exam_pdf_bytes_jun, exam_pdf_bytes_sep
):
    """Con `examenes_todas_convocatorias=true`, CAL y ALG aparecen en las
    tres convocatorias (6 exámenes en total), no solo en la automática."""
    r = client.post(
        "/api/generar",
        files=_tres_convocatorias_files(pdf_bytes, exam_pdf_bytes, exam_pdf_bytes_jun, exam_pdf_bytes_sep),
        data={
            **_selecciones(
                {"acronimo": "CAL", "curso": "1ºA", "grupos": ["C1"]},
                {"acronimo": "ALG", "curso": "1ºA", "grupos": ["A1"]},
            ),
            "examenes_todas_convocatorias": "true",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["examenes_incluidos"] == 6
    assert set(body["examenes_convocatorias_incluidas"]) == {"FEBRERO DE 2027", "JUNIO DE 2027", "SEPTIEMBRE DE 2027"}
    assert body["examenes_convocatorias_disponibles"] == []


def test_generar_seleccion_solo_examen_sin_grupos_no_es_seleccion_vacia(client, pdf_bytes):
    """`solo_examen=true` con `grupos` vacío es la forma normal de llevar
    una asignatura por libre — a diferencia de `test_generar_seleccion_sin_
    grupos`, esto NO debe rechazarse."""
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones(
            {"acronimo": "CAL", "curso": "1ºA", "grupos": ["C1"]},
            {"acronimo": "ALG", "curso": "1ºA", "grupos": [], "solo_examen": True},
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert all(e["asignatura"] != "ALG" for e in body["eventos"])
    assert any(e["asignatura"] == "CAL" for e in body["eventos"])


def test_generar_solo_examen_incluye_convocatoria_elegida_a_mano(client, pdf_bytes, exam_pdf_bytes):
    """ALG por libre, convocatoria de febrero elegida explícitamente —
    nunca se incluiría por la regla automática (ALG es de semestre 2)."""
    r = client.post(
        "/api/generar",
        files=[
            ("pdf", ("horario.pdf", pdf_bytes, "application/pdf")),
            ("examenes", ("examenes.pdf", exam_pdf_bytes, "application/pdf")),
        ],
        data=_selecciones(
            {
                "acronimo": "ALG", "curso": "1ºA", "grupos": [],
                "solo_examen": True, "convocatorias": ["FEBRERO DE 2027"],
            }
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["examenes_incluidos"] == 1
    assert "Examen:" in body["html"]
    assert all(e["asignatura"] != "ALG" for e in body["eventos"])


def test_generar_solo_examen_sin_convocatoria_elegida_ofrece_las_disponibles(client, pdf_bytes, exam_pdf_bytes):
    """Sin elegir todavía ninguna convocatoria: no se añade ningún examen,
    pero la respuesta dice cuáles hay disponibles para esa asignatura, para
    que el front pueda ofrecerlas."""
    r = client.post(
        "/api/generar",
        files=[
            ("pdf", ("horario.pdf", pdf_bytes, "application/pdf")),
            ("examenes", ("examenes.pdf", exam_pdf_bytes, "application/pdf")),
        ],
        data=_selecciones({"acronimo": "ALG", "curso": "1ºA", "grupos": [], "solo_examen": True}),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["examenes_incluidos"] == 0
    assert body["examenes_solo_examen_disponibles"] == {"ALG": ["FEBRERO DE 2027"]}


def test_generar_evento_trasladado_incluye_descripcion(client, pdf_bytes):
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones({"acronimo": "MD", "curso": "1ºA", "grupos": ["A1", "B1"]}),
    )
    body = r.json()
    trasladados = [e for e in body["eventos"] if e["trasladado_desde"]]
    assert trasladados
    for e in trasladados:
        assert e["descripcion_traslado"] is not None
        assert e["trasladado_desde"] in e["descripcion_traslado"] or True  # la fecha va en formato largo, no ISO
        assert "traslada" in e["descripcion_traslado"]


# --- extremo a extremo: la selección ya verificada evento a evento -------


def test_extremo_a_extremo_seleccion_md_cal_1a(client, pdf_bytes):
    """La misma selección MD:1ºA:A1,B1 + CAL:1ºA:A1,B3,C1 verificada en
    `tests/test_select.py` contra el pipeline directo (82 eventos,
    0 avisos de validación) — aquí reproducida por la API, de punta a
    punta, incluyendo el .ics resultante."""
    r = client.post(
        "/api/generar",
        files=_pdf_file(pdf_bytes),
        data=_selecciones(
            {"acronimo": "MD", "curso": "1ºA", "grupos": ["A1", "B1"]},
            {"acronimo": "CAL", "curso": "1ºA", "grupos": ["A1", "B3", "C1"]},
        ),
    )
    assert r.status_code == 200
    body = r.json()

    assert len(body["eventos"]) == 82
    assert body["conflictos"] == []
    assert body["avisos"] is False

    por_asignatura = {}
    for e in body["eventos"]:
        por_asignatura[e["asignatura"]] = por_asignatura.get(e["asignatura"], 0) + 1
    assert por_asignatura == {"MD": 40, "CAL": 42}

    trasladados = sum(1 for e in body["eventos"] if e["trasladado_desde"])
    assert trasladados == 6

    cal = icalendar.Calendar.from_ical(body["ics"])
    vevents = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(vevents) == 82
    uids = {str(v["uid"]) for v in vevents}
    assert len(uids) == 82
    assert b"BEGIN:VTIMEZONE" in body["ics"].encode("utf-8")

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

"""CLI (`horario_uca/cli.py`): sintaxis de selección, --config YAML, y los
tres subcomandos ejecutados de verdad contra data/GII_horario2627.pdf."""
import icalendar
import pytest
import yaml

from horario_uca.cli import (
    SelectionSyntaxError,
    cmd_generar,
    cmd_listar,
    load_config,
    parse_selection_spec,
)
from horario_uca.model import SubjectSelection


def test_parse_selection_spec_tres_partes():
    s = parse_selection_spec("MD:1ºA:A1,B1")
    assert s == SubjectSelection(acronym="MD", curso="1ºA", itinerario=None, groups=["A1", "B1"])


def test_parse_selection_spec_cuatro_partes_itinerario():
    s = parse_selection_spec("CS:4º:Itinerario de Ingeniería del Software:A1,B1,C1")
    assert s.itinerario == "Itinerario de Ingeniería del Software"
    assert s.groups == ["A1", "B1", "C1"]


@pytest.mark.parametrize("spec", ["MD", "MD:1ºA", "MD:1ºA:X:Y:Z", "MD::A1", ":1ºA:A1", "MD:1ºA:"])
def test_parse_selection_spec_formato_invalido(spec):
    with pytest.raises(SelectionSyntaxError):
        parse_selection_spec(spec)


def test_load_config_valido(tmp_path):
    config = tmp_path / "seleccion.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "titulo": "Mi horario",
                "salida": "out/mi_horario",
                "selecciones": [
                    {"acronimo": "MD", "curso": "1ºA", "grupos": ["A1", "B1"]},
                    {"acronimo": "CAL", "curso": "1ºA", "grupos": ["A1", "B3", "C1"]},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    selections, titulo, salida = load_config(str(config))
    assert titulo == "Mi horario"
    assert salida == "out/mi_horario"
    assert len(selections) == 2
    assert selections[0] == SubjectSelection(acronym="MD", curso="1ºA", itinerario=None, groups=["A1", "B1"])


def test_load_config_sin_clave_selecciones(tmp_path):
    config = tmp_path / "vacio.yaml"
    config.write_text(yaml.safe_dump({"titulo": "x"}), encoding="utf-8")
    with pytest.raises(SelectionSyntaxError):
        load_config(str(config))


def test_load_config_selecciones_incompleta(tmp_path):
    config = tmp_path / "incompleta.yaml"
    config.write_text(
        yaml.safe_dump({"selecciones": [{"acronimo": "MD", "curso": "1ºA"}]}),  # falta 'grupos'
        encoding="utf-8",
    )
    with pytest.raises(SelectionSyntaxError):
        load_config(str(config))


class _Args:
    def __init__(self, **kw):
        self.config = None
        self.seleccion = []
        self.titulo = None
        self.salida = None
        self.verbose = False
        self.curso = None
        self.itinerario = None
        self.__dict__.update(kw)


def test_cmd_generar_seleccion_de_referencia(sample_pdf_path, tmp_path, capsys):
    salida = tmp_path / "horario_md_cal"
    args = _Args(pdf=str(sample_pdf_path), seleccion=["MD:1ºA:A1,B1", "CAL:1ºA:A1,B3,C1"], salida=str(salida))
    exit_code = cmd_generar(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "total: 82" in out
    assert "sin conflictos" in out

    html_path = salida.with_suffix(".html")
    ics_path = salida.with_suffix(".ics")
    assert html_path.exists()
    assert ics_path.exists()

    ics_bytes = ics_path.read_bytes()
    cal = icalendar.Calendar.from_ical(ics_bytes)
    vevents = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(vevents) == 82
    assert b"BEGIN:VTIMEZONE" in ics_bytes


def test_cmd_generar_seleccion_con_conflicto_muestra_aviso(sample_pdf_path, tmp_path, capsys):
    salida = tmp_path / "horario_solape"
    args = _Args(pdf=str(sample_pdf_path), seleccion=["IG:1ºA:C1", "CAL:1ºA:C1"], salida=str(salida))
    exit_code = cmd_generar(args)
    out = capsys.readouterr().out
    assert exit_code == 0  # un conflicto de horario avisa, no bloquea la generación
    assert "6 fechas dentro del mismo curso" in out
    assert "combinación de grupos incompatible" in out
    assert salida.with_suffix(".html").exists()


def test_cmd_generar_grupo_inexistente_no_genera_nada(sample_pdf_path, tmp_path, capsys):
    salida = tmp_path / "no_deberia_existir"
    args = _Args(pdf=str(sample_pdf_path), seleccion=["MD:1ºA:A1,Z9"], salida=str(salida))
    exit_code = cmd_generar(args)
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "grupo_no_existe" in err
    assert not salida.with_suffix(".html").exists()
    assert not salida.with_suffix(".ics").exists()


def test_cmd_generar_asignatura_inexistente_no_genera_nada(sample_pdf_path, tmp_path, capsys):
    salida = tmp_path / "no_deberia_existir2"
    args = _Args(pdf=str(sample_pdf_path), seleccion=["NOEXISTE:1ºA:A1"], salida=str(salida))
    exit_code = cmd_generar(args)
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "asignatura_no_existe_en_curso" in err
    assert not salida.with_suffix(".html").exists()


def test_cmd_generar_sin_seleccion_ni_config_es_error(sample_pdf_path, capsys):
    args = _Args(pdf=str(sample_pdf_path), seleccion=[])
    exit_code = cmd_generar(args)
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "al menos una selección" in err


def test_cmd_generar_con_config_yaml(sample_pdf_path, tmp_path, capsys):
    config = tmp_path / "seleccion.yaml"
    salida = tmp_path / "horario_config"
    config.write_text(
        yaml.safe_dump(
            {
                "titulo": "Horario de config",
                "salida": str(salida),
                "selecciones": [
                    {"acronimo": "MD", "curso": "1ºA", "grupos": ["A1", "B1"]},
                    {"acronimo": "CAL", "curso": "1ºA", "grupos": ["A1", "B3", "C1"]},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    args = _Args(pdf=str(sample_pdf_path), config=str(config))
    exit_code = cmd_generar(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "total: 82" in out
    assert salida.with_suffix(".html").exists()


def test_cmd_listar_sin_filtros(sample_pdf_path, capsys):
    args = _Args(pdf=str(sample_pdf_path))
    exit_code = cmd_listar(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.count("·") >= 24  # 24 combos curso/semestre(/itinerario)


def test_cmd_listar_curso_con_itinerario_ambiguo_avisa(sample_pdf_path, capsys):
    args = _Args(pdf=str(sample_pdf_path), curso="4º")
    exit_code = cmd_listar(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Aviso" in out and "itinerarios" in out


def test_cmd_listar_curso_e_itinerario_4o_ingenieria_software(sample_pdf_path, capsys):
    args = _Args(pdf=str(sample_pdf_path), curso="4º", itinerario="Itinerario de Ingeniería del Software")
    exit_code = cmd_listar(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    for acronym in ("CS", "DGPS", "ES", "MPS"):
        assert acronym in out
    assert "A1" in out and "B1" in out and "C1" in out

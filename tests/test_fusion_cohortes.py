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

"""Fusión en el front de las páginas de cohorte A/B de 1º y 2º en una sola
tarjeta "1º · Semestre N" / "2º · Semestre N". La fusión en sí vive en
JS (`web/static/index.html::mergeCombo`/`buildDisplayCombos`) y no se
reimplementa aquí — lo que se fija como test de regresión permanente es la
invariante de DATOS de la que depende para ser segura: que unir por texto
de código de grupo (un `Set` en JS, que deduplica solo) nunca pierde ni
mezcla dos clases distintas bajo la misma etiqueta.
"""
from horario_uca.model import SubjectSelection
from horario_uca.pipeline import parse_document, resolve_document
from horario_uca.select import filter_events

COHORT_PAIRS = [("1ºA", "1ºB"), ("2ºA", "2ºB")]


def _session_key(block, group):
    return (block.day_of_week, block.start_time, block.end_time, block.room, frozenset(group.weeks_active))


def _by_page(pages):
    return {p.curso: p for p in pages if p.curso is not None}


def test_fusion_1o_2o_no_pierde_ni_duplica_grupos(sample_pdf_path):
    """Para cada (curso, semestre) con cohortes A/B: mismo conjunto de
    asignaturas en las dos páginas, mismo tipo de grupo por letra, y
    cualquier código de grupo que aparezca en AMBAS páginas es literalmente
    la misma sesión (mismo día/hora/aula/semanas activas) — nunca dos
    clases distintas compartiendo etiqueta. Con eso garantizado, unir los
    códigos de las dos páginas con un `Set` (lo que hace el front) no
    pierde ninguno (ningún código real desaparece) ni produce dos chips
    para la misma clase (el `Set` deduplica el texto igual, y esa
    duplicación es inofensiva solo porque ya se sabe que es la misma
    sesión)."""
    pages = parse_document(str(sample_pdf_path))
    by_page = _by_page(pages)

    for curso_a, curso_b in COHORT_PAIRS:
        pages_a = {p.semestre: p for p in pages if p.curso == curso_a}
        pages_b = {p.semestre: p for p in pages if p.curso == curso_b}
        assert set(pages_a) == set(pages_b) == {1, 2}, (curso_a, curso_b, pages_a.keys(), pages_b.keys())

        for semestre in (1, 2):
            page_a, page_b = pages_a[semestre], pages_b[semestre]

            subj_a = {b.subject_acronym for b in page_a.blocks}
            subj_b = {b.subject_acronym for b in page_b.blocks}
            assert subj_a == subj_b, (
                f"{curso_a[:-1]} sem{semestre}: conjunto de asignaturas distinto entre "
                f"{curso_a} ({subj_a}) y {curso_b} ({subj_b})"
            )
            assert len(subj_a) > 0

            # Mismo conjunto de LETRAS (tipos de grupo) por asignatura, no
            # solo mismo tipo en las letras que ya coinciden — comparar
            # solo pares (asignatura, letra) presentes en ambas (lo que
            # hacía la primera versión de este test, vía `common_letters`
            # más abajo) no detecta el caso de una letra que exista SOLO en
            # una de las dos páginas: p.ej. si una asignatura tuviera
            # laboratorio (D) en A pero ningún D en B, la fila fusionada de
            # "Laboratorio" mostraría solo los códigos de A sin decir que
            # B no lo ofrece. Aquí se compara el conjunto de letras
            # asignatura por asignatura, no globalmente.
            def letters_by_subject(page):
                m: dict[str, set[str]] = {}
                for b in page.blocks:
                    for g in b.groups:
                        m.setdefault(b.subject_acronym, set()).add(g.group_code[0])
                return m

            letras_a, letras_b = letters_by_subject(page_a), letters_by_subject(page_b)
            for acronym in subj_a:
                assert letras_a.get(acronym, set()) == letras_b.get(acronym, set()), (
                    f"{curso_a}/{curso_b} sem{semestre} {acronym}: conjunto de letras distinto — "
                    f"A={sorted(letras_a.get(acronym, set()))} B={sorted(letras_b.get(acronym, set()))} "
                    "(la fila fusionada de ese tipo mostraría grupos de una sola cohorte sin avisar)"
                )

            def sessions_by_code(page):
                m: dict[tuple[str, str], set] = {}
                type_by = {}
                for b in page.blocks:
                    for g in b.groups:
                        key = (b.subject_acronym, g.group_code)
                        m.setdefault(key, set()).add(_session_key(b, g))
                        type_by[(b.subject_acronym, g.group_code[0])] = g.group_type
                return m, type_by

            sa, ta = sessions_by_code(page_a)
            sb, tb = sessions_by_code(page_b)

            # Mismo tipo de grupo por (asignatura, letra) en ambas páginas
            # — si no, la fila fusionada mostraría una etiqueta ("Teoría")
            # distinta según de qué página viniera cada chip de esa fila.
            common_letters = set(ta) & set(tb)
            for key in common_letters:
                assert ta[key] == tb[key], f"{curso_a}/{curso_b} sem{semestre} {key}: tipo distinto {ta[key]!r} vs {tb[key]!r}"

            # Ningún código de grupo compartido por texto puede ser una
            # clase distinta en cada página.
            for code_key in set(sa) & set(sb):
                assert sa[code_key] == sb[code_key], (
                    f"{curso_a}/{curso_b} sem{semestre} {code_key}: mismo código de grupo, "
                    f"sesiones distintas — A={sa[code_key]} B={sb[code_key]} (fusionar los "
                    "escondería como si fueran la misma clase)"
                )

            # La unión de códigos por (asignatura, letra) no debe perder
            # ningún código real de ninguna de las dos páginas.
            codes_a: dict[tuple[str, str], set[str]] = {}
            codes_b: dict[tuple[str, str], set[str]] = {}
            for (acr, code), _ in sa.items():
                codes_a.setdefault((acr, code[0]), set()).add(code)
            for (acr, code), _ in sb.items():
                codes_b.setdefault((acr, code[0]), set()).add(code)
            for key in set(codes_a) | set(codes_b):
                union = codes_a.get(key, set()) | codes_b.get(key, set())
                assert codes_a.get(key, set()) <= union and codes_b.get(key, set()) <= union


def test_seleccion_con_grupos_de_las_dos_cohortes_en_la_misma_asignatura(sample_pdf_path):
    """Extremo a extremo: la misma asignatura (CAL, curso fusionado "1º"
    semestre 1) con un tipo elegido de la cohorte A (Teoría A1, página
    1ºA) y otro tipo elegido de la cohorte B (Problemas B2, página 1ºB) —
    combinación real que el front produce al fusionar (ver
    `buildPayload`/`resolveGroupCurso` en index.html, verificado en vivo
    contra un servidor real esta sesión: el payload que construye el
    asistente para esta selección exacta son dos `SubjectSelection`, una
    por cohorte). Aquí se fija contra el pipeline real que ambas resuelven
    a fechas correctas de su propia página de origen."""
    pages = parse_document(str(sample_pdf_path))
    events, warnings = resolve_document(pages)
    assert warnings == []

    selections = [
        SubjectSelection(acronym="CAL", curso="1ºA", groups=["A1"]),
        SubjectSelection(acronym="CAL", curso="1ºB", groups=["B2"]),
    ]
    filtered = filter_events(events, selections)

    a1_events = [e for e in filtered if e.curso == "1ºA" and e.group_code == "A1"]
    b2_events = [e for e in filtered if e.curso == "1ºB" and e.group_code == "B2"]

    assert len(a1_events) == 24
    assert len(b2_events) == 12
    assert {e.subject_acronym for e in a1_events} == {"CAL"}
    assert {e.subject_acronym for e in b2_events} == {"CAL"}
    assert min(e.date for e in a1_events) == "2026-09-21"
    assert min(e.date for e in b2_events) == "2026-09-28"

    # No hay eventos de un tercer curso ni se ha perdido ninguna de las dos cohortes.
    assert {e.curso for e in filtered} == {"1ºA", "1ºB"}

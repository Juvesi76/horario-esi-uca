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

"""CLI: `horario generar`, `horario listar`, `horario debug`.

    horario generar data/GII_horario2627.pdf "MD:1ºA:A1,B1" "CAL:1ºA:A1,B3,C1"
    horario generar data/GII_horario2627.pdf --config seleccion.yaml
    horario listar data/GII_horario2627.pdf --curso 4º
    horario debug data/GII_horario2627.pdf --page 0

Sintaxis de selección por línea de comandos: `ACRONIMO:CURSO:GRUPOS` (curso
sin itinerario o con itinerario no ambiguo) o `ACRONIMO:CURSO:ITINERARIO:GRUPOS`
(3º semestre 2º y 4º, donde el mismo acrónimo puede repetirse entre
itinerarios — ver `validate_selection`, código `itinerario_ambiguo`).
`GRUPOS` es una lista separada por comas: `A1,B1`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from horario_uca import debug as debug_module
from horario_uca.model import ParseWarning, SubjectSelection
from horario_uca.pipeline import build_catalog, generate_calendar, parse_document


class SelectionSyntaxError(ValueError):
    pass


def parse_selection_spec(spec: str) -> SubjectSelection:
    """`ACRONIMO:CURSO:GRUPOS` o `ACRONIMO:CURSO:ITINERARIO:GRUPOS`."""
    parts = spec.split(":")
    if len(parts) == 3:
        acronym, curso, groups_str = parts
        itinerario = None
    elif len(parts) == 4:
        acronym, curso, itinerario, groups_str = parts
        itinerario = itinerario.strip() or None
    else:
        raise SelectionSyntaxError(
            f"selección {spec!r} no tiene el formato esperado — usa "
            "'ACRONIMO:CURSO:GRUPOS' (p.ej. 'MD:1ºA:A1,B1') o, si el curso tiene "
            "itinerarios, 'ACRONIMO:CURSO:ITINERARIO:GRUPOS'"
        )
    groups = [g.strip() for g in groups_str.split(",") if g.strip()]
    if not acronym.strip() or not curso.strip() or not groups:
        raise SelectionSyntaxError(
            f"selección {spec!r} incompleta — acrónimo, curso y al menos un grupo son obligatorios"
        )
    return SubjectSelection(acronym=acronym.strip(), curso=curso.strip(), itinerario=itinerario, groups=groups)


def load_config(path: str) -> tuple[list[SubjectSelection], str | None, str | None]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_selections = data.get("selecciones")
    if not raw_selections:
        raise SelectionSyntaxError(f"{path!r} no define ninguna selección bajo la clave 'selecciones'")

    selections = []
    for i, item in enumerate(raw_selections):
        try:
            selections.append(
                SubjectSelection(
                    acronym=str(item["acronimo"]),
                    curso=str(item["curso"]),
                    itinerario=(str(item["itinerario"]) if item.get("itinerario") else None),
                    groups=[str(g) for g in item["grupos"]],
                )
            )
        except KeyError as exc:
            raise SelectionSyntaxError(
                f"{path!r}: selección #{i} no tiene la clave obligatoria {exc}"
            ) from exc

    return selections, data.get("titulo"), data.get("salida")


def _print_warnings(warnings: list[ParseWarning]) -> None:
    # Sin valor por defecto para el stream: `file=sys.stderr` como default de
    # parámetro se evaluaría UNA vez, al definir la función, y capturaría el
    # `sys.stderr` de ese momento — si algo (p.ej. `capsys` en los tests)
    # sustituye `sys.stderr` después, este código seguiría escribiendo al
    # stream antiguo. Se lee `sys.stderr` en cada llamada.
    for w in warnings:
        print(f"  [{w.code}] {w.message}", file=sys.stderr)


def cmd_generar(args: argparse.Namespace) -> int:
    if args.config:
        try:
            selections, titulo_cfg, salida_cfg = load_config(args.config)
        except (SelectionSyntaxError, OSError, yaml.YAMLError) as exc:
            print(f"error leyendo --config {args.config!r}: {exc}", file=sys.stderr)
            return 2
        titulo = args.titulo or titulo_cfg or "Horario ESI (UCA)"
        salida = args.salida or salida_cfg or "out/horario"
    else:
        if not args.seleccion:
            print(
                "error: hay que indicar al menos una selección (ACRONIMO:CURSO:GRUPOS) o --config",
                file=sys.stderr,
            )
            return 2
        try:
            selections = [parse_selection_spec(s) for s in args.seleccion]
        except SelectionSyntaxError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        titulo = args.titulo or "Horario ESI (UCA)"
        salida = args.salida or "out/horario"

    pages = parse_document(args.pdf)
    outcome = generate_calendar(pages, selections, titulo=titulo, verbose=args.verbose)

    if not outcome.ok:
        print("No se puede generar: la selección tiene errores.", file=sys.stderr)
        _print_warnings(outcome.hard_errors)
        return 1

    if outcome.resolve_warnings:
        print(
            f"aviso: {len(outcome.resolve_warnings)} ParseWarning al resolver fechas (ver detalle):",
            file=sys.stderr,
        )
        _print_warnings(outcome.resolve_warnings)

    print(outcome.report_text)

    if not outcome.events:
        print(
            "\naviso: la selección no produce ningún evento — revisa acrónimos/grupos/curso/itinerario",
            file=sys.stderr,
        )

    salida_path = Path(salida)
    salida_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = salida_path.with_suffix(".html")
    ics_path = salida_path.with_suffix(".ics")
    html_path.write_text(outcome.html, encoding="utf-8")
    ics_path.write_bytes(outcome.ics)

    print(f"\nGenerado: {html_path}")
    print(f"Generado: {ics_path}")
    return 0


def cmd_listar(args: argparse.Namespace) -> int:
    pages = parse_document(args.pdf)
    catalog = build_catalog(pages)

    if not args.curso:
        combos = sorted(
            {(c.curso, c.semestre, c.itinerario) for c in catalog},
            key=lambda t: (t[0] or "", t[1] or 0, t[2] or ""),
        )
        print("Cursos/semestres/itinerarios disponibles (usa --curso para ver asignaturas y grupos):")
        for curso, semestre, itinerario in combos:
            itin = f" · {itinerario}" if itinerario else ""
            print(f"  {curso} · semestre {semestre}{itin}")
        return 0

    matched = [c for c in catalog if c.curso == args.curso]
    if args.itinerario:
        matched = [c for c in matched if c.itinerario == args.itinerario]
    if not matched:
        print(f"No hay páginas para curso={args.curso!r}" + (f" itinerario={args.itinerario!r}" if args.itinerario else ""))
        return 1

    itinerarios_presentes = sorted({c.itinerario for c in matched if c.itinerario})
    if not args.itinerario and itinerarios_presentes:
        print(
            f"Aviso: {args.curso} tiene itinerarios ({', '.join(itinerarios_presentes)}) — "
            "usa --itinerario para ver uno en concreto; si no, se muestran todos mezclados."
        )

    for combo in sorted(matched, key=lambda c: (c.semestre, c.itinerario or "")):
        itin = f" · {combo.itinerario}" if combo.itinerario else ""
        print(f"\n{combo.curso} · semestre {combo.semestre}{itin} (página {combo.page_index})")
        for subject in combo.asignaturas:
            print(f"  {subject.acronimo} — {subject.nombre}")
            for letter in sorted(subject.grupos):
                grupo = subject.grupos[letter]
                print(f"    {letter} ({grupo.tipo}): {', '.join(grupo.codigos)}")
    return 0


def cmd_debug(args: argparse.Namespace) -> int:
    out_path = debug_module.render(args.pdf, args.page, args.out)
    print(f"PNG generado: {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="horario")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generar", help="genera el .html y el .ics de una selección de asignaturas/grupos")
    gen.add_argument("pdf", help="ruta al PDF de horarios de la ESI")
    gen.add_argument(
        "seleccion",
        nargs="*",
        help="ACRONIMO:CURSO:GRUPOS o ACRONIMO:CURSO:ITINERARIO:GRUPOS (repetible; alternativa a --config)",
    )
    gen.add_argument("--config", help="fichero YAML con la selección, en vez de pasarla por línea de comandos")
    gen.add_argument("--titulo", default=None, help="título del calendario (por defecto 'Horario ESI (UCA)')")
    gen.add_argument("--salida", default=None, help="ruta base de salida sin extensión (por defecto out/horario)")
    gen.add_argument("--verbose", action="store_true", help="incluye ParseInfo en el informe de confirmación")

    lst = sub.add_parser("listar", help="lista cursos/itinerarios/asignaturas/grupos disponibles en el PDF")
    lst.add_argument("pdf", help="ruta al PDF de horarios de la ESI")
    lst.add_argument("--curso", default=None, help='p.ej. "1ºA", "3º", "4º"')
    lst.add_argument("--itinerario", default=None, help="nombre del itinerario, solo relevante en 3º(2º sem.) y 4º")

    dbg = sub.add_parser("debug", help="genera un PNG de depuración de una página (rectángulos detectados)")
    dbg.add_argument("pdf", help="ruta al PDF de horarios de la ESI")
    dbg.add_argument("--page", type=int, required=True, help="page_index, 0-based")
    dbg.add_argument("--out", default=None)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "generar":
        sys.exit(cmd_generar(args))
    elif args.command == "listar":
        sys.exit(cmd_listar(args))
    elif args.command == "debug":
        sys.exit(cmd_debug(args))


if __name__ == "__main__":
    main()

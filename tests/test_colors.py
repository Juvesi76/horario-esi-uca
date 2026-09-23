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

"""Paleta de colores de `render/html.py`.

Dos reglas independientes:
1. El rojo (y granate/naranja muy cálido) está reservado en exclusiva para
   los avisos de choque de horario (`--overlap-ring`/`--conflict-*`);
   ningún color de asignatura puede caer ahí, ni siquiera cuando es el
   color real de la leyenda del PDF.
2. **El color NUNCA es el único canal de identidad de una asignatura** —
   cada chip lleva además un patrón de fondo (`_PATTERNS`/
   `_assign_patterns`), independiente del color, precisamente porque una
   paleta puede "parecer perfecta" (matiz separado, contraste AA, incluso
   una simulación de daltonismo sobre la paleta AISLADA) y aun así fallar
   en la práctica: verificado en esta sesión con una selección real de 6
   asignaturas de dos cursos a la vez (colores de leyenda REALES del PDF
   mezclados con sustitutos, nunca antes probado así) — 5 de las 6
   convergían al mismo amarillo/gris bajo deuteranopia.

El rojo está reservado en exclusiva para los avisos de choque de horario;
esta suite cubre también los dos intentos previos de paleta que parecían
correctos y no lo eran.
"""
import itertools

from horario_uca.pipeline import parse_document, resolve_document
from horario_uca.render.html import (
    _assign_colors,
    _assign_patterns,
    _contrast_ratio,
    _DARK_TEXT_RGB,
    _ensure_contrast_margin,
    _FALLBACK_PALETTE,
    _FALLBACK_PALETTE_VIBRANT,
    _hex_to_rgb,
    _hue_degrees,
    _hue_distance,
    _is_reddish,
    _MARKER_RING_DARK_HEX,
    _MARKER_RING_LIGHT_HEX,
    _MIN_CONTRAST_TARGET,
    _OVERLAP_RING_HEX,
    _PALETTE_TIERS,
    _PATTERNS,
    _relative_luminance,
    _rgb_to_hex,
    _text_color_for,
    _WHITE_RGB,
)

# `--card-bg` tal cual está en la hoja de estilos de `render/html.py`
# (`:root`/dark bajo `@media (prefers-color-scheme: dark)`) — duplicado
# aquí a propósito (no vale la pena promoverlo a constante Python solo para
# esta comprobación, es un color de tema estático que casi nunca cambia),
# pero si alguna vez se retoca en la plantilla, este test debe actualizarse
# a la vez o deja de medir lo que dice medir.
_CARD_BG_LIGHT_HEX = "#ffffff"
_CARD_BG_DARK_HEX = "#1f2229"

# Matrices de Machado, Oliveira y Fialho (2009) para protanopia,
# deuteranopia y tritanopia (severidad 1.0, en RGB lineal) — la misma
# familia de matrices que usan simuladores conocidos (Coblis, las "Vision
# deficiencies" de Chrome DevTools). Un contraste de luminosidad correcto
# (WCAG) no dice nada sobre si dos colores SATURADOS se confunden entre sí
# bajo daltonismo — hace falta simular la propia percepción.
_CVD_MATRICES = {
    "protanopia": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
    "deuteranopia": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ],
    "tritanopia": [
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809, 0.147602],
        [0.004733, 0.691367, 0.303900],
    ],
}

# Umbral de "confundible": distancia euclídea RGB por debajo de esto, en el
# espacio YA simulado (o diferencia de luminancia relativa en escala de
# grises), se trata como "un espectador con ese tipo de visión puede
# verlos iguales" — no es un umbral perceptual certificado, es el mismo
# orden de magnitud que separaba los pares realmente confundidos
# encontrados en esta sesión (8-24 de distancia simulada, 0.03-0.08 de
# diferencia de luminancia) de los claramente distintos (>100 / >0.15).
_CONFUSABLE_CVD_DISTANCE = 40
_CONFUSABLE_GRAY_DIFF = 0.08


def _srgb_to_linear(c: int) -> float:
    c_norm = c / 255
    return c_norm / 12.92 if c_norm <= 0.04045 else ((c_norm + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> int:
    c = max(0.0, min(1.0, c))
    v = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return max(0, min(255, round(v * 255)))


def _simulate(matrix: list[list[float]], rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    lin = [_srgb_to_linear(c) for c in rgb]
    out = [sum(matrix[i][j] * lin[j] for j in range(3)) for i in range(3)]
    return tuple(_linear_to_srgb(c) for c in out)


def _euclidean(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _confusable_reasons(hex_a: str, hex_b: str) -> list[str]:
    """Motivos concretos por los que dos colores podrían confundirse (una
    entrada por cada simulación de daltonismo + escala de grises donde
    quedan cerca) — lista vacía si no hay ninguno. Es la MISMA
    comprobación, con el mismo umbral, en las tres formas de daltonismo
    comunes Y en escala de grises pura (la prueba más exigente y más
    barata: si dos colores no se distinguen en gris, no se distinguen bajo
    ningún daltonismo tampoco)."""
    rgb_a, rgb_b = _hex_to_rgb(hex_a), _hex_to_rgb(hex_b)
    reasons = []
    for label, matrix in _CVD_MATRICES.items():
        dist = _euclidean(_simulate(matrix, rgb_a), _simulate(matrix, rgb_b))
        if dist < _CONFUSABLE_CVD_DISTANCE:
            reasons.append(f"{label}={dist:.1f}")
    gray_diff = abs(_relative_luminance(rgb_a) - _relative_luminance(rgb_b))
    if gray_diff < _CONFUSABLE_GRAY_DIFF:
        reasons.append(f"escala_de_grises={gray_diff:.3f}")
    return reasons


def _assert_patterns_cover_confusable_colors(colors: dict[str, str]) -> None:
    """La comprobación central de todo el archivo: para cualquier par de
    asignaturas cuyos colores sean confundibles (razones no vacías, ver
    `_confusable_reasons`), sus patrones de fondo (`_assign_patterns`)
    tienen que ser DISTINTOS — si no, esas dos asignaturas serían
    indistinguibles de verdad para un espectador con ese tipo de visión,
    porque ni el color ni el patrón las separarían."""
    patterns = _assign_patterns(sorted(colors))
    for a, b in itertools.combinations(colors, 2):
        reasons = _confusable_reasons(colors[a], colors[b])
        if reasons and patterns[a] == patterns[b]:
            raise AssertionError(
                f"{a} ({colors[a]}, patrón {patterns[a]}) y {b} ({colors[b]}, patrón {patterns[b]}) "
                f"son confundibles ({', '.join(reasons)}) Y comparten patrón"
            )


def test_paleta_de_respaldo_no_tiene_ningun_color_rojizo():
    for hexcolor in _FALLBACK_PALETTE:
        assert not _is_reddish(_hex_to_rgb(hexcolor)), f"{hexcolor} es rojo/granate/naranja muy cálido"


def test_paleta_de_respaldo_cumple_el_umbral_de_margen_no_solo_aa():
    """Cada color de la paleta de respaldo debe alcanzar
    `_MIN_CONTRAST_TARGET` (5.0, no el mínimo AA de 4.5) con blanco o con
    el negro de texto de los chips — el criterio no depende del tema
    claro/oscuro de la página porque el fondo del chip es el mismo color
    en los dos temas, solo cambia si el texto es blanco o negro. Antes de
    subir el umbral de 4.5 a 5.0, tres colores de esta paleta pasaban por
    0.00-0.07 de margen (4.50, 4.57, 4.87) — "pasan" en sentido estricto,
    pero un margen así de pequeño es indistinguible de un fallo esperando
    a que alguien retoque el color por otro motivo."""
    for hexcolor in _FALLBACK_PALETTE:
        rgb = _hex_to_rgb(hexcolor)
        contrast_white = _contrast_ratio(rgb, _WHITE_RGB)
        contrast_dark = _contrast_ratio(rgb, _DARK_TEXT_RGB)
        assert max(contrast_white, contrast_dark) >= _MIN_CONTRAST_TARGET, (
            f"{hexcolor}: contraste insuficiente (blanco={contrast_white:.2f}, negro={contrast_dark:.2f})"
        )


def test_paleta_vibrante_no_tiene_ningun_color_rojizo():
    """Regla que NO cambia entre modos (ver el pedido original: "el rojo
    sigue reservado exclusivamente a los avisos, en las dos paletas")."""
    for hexcolor in _FALLBACK_PALETTE_VIBRANT:
        assert not _is_reddish(_hex_to_rgb(hexcolor)), f"{hexcolor} es rojo/granate/naranja muy cálido"


def test_paleta_vibrante_cumple_el_umbral_de_margen():
    """Mismo umbral que la paleta accesible (5.0, no el mínimo AA de 4.5) —
    los dos modos comparten el mismo criterio de contraste de texto sobre
    el color, ver el pedido original ("LO QUE NO CAMBIA"). Tabla medida
    (no solo calculada): los 8 colores dan contraste ≥5.03 contra el mejor
    de blanco/`_DARK_TEXT_RGB` (`#1a1a1a`) — dos quedan con margen más
    ajustado (5.03/5.04, los matices 205° y 300°), el resto entre 7.02 y
    10.01."""
    contrastes = {}
    for hexcolor in _FALLBACK_PALETTE_VIBRANT:
        rgb = _hex_to_rgb(hexcolor)
        contrast_white = _contrast_ratio(rgb, _WHITE_RGB)
        contrast_dark = _contrast_ratio(rgb, _DARK_TEXT_RGB)
        contrastes[hexcolor] = max(contrast_white, contrast_dark)
        assert contrastes[hexcolor] >= _MIN_CONTRAST_TARGET, (
            f"{hexcolor}: contraste insuficiente (blanco={contrast_white:.2f}, negro={contrast_dark:.2f})"
        )
    assert all(c >= 5.0 for c in contrastes.values()), contrastes


def test_paleta_vibrante_evita_colisiones_de_matiz_en_caso_real_del_repetidor(sample_pdf_path):
    """Caso real que encontró el motivo de `avoid_hue_collisions` (ver
    `_next_fallback_color_by_hue_distance`): sin patrón de fondo por
    defecto en modo vibrante, el color vuelve a ser el único canal — y sin
    esta selección por distancia, el mismo caso del "repetidor" (5
    asignaturas de 1ºA + 4 de 2ºA, dos páginas que reparten el mismo
    azul/verde/púrpura "de fábrica" del generador de PDFs de la ESI)
    dejaba a `RC` (sustituto) a solo 2° de matiz del azul REAL de `AC` —
    prácticamente el mismo color. Con la selección por distancia, la
    mínima distancia de cualquier color al resto en este caso real sube a
    ≥12° — sigue habiendo un par ajustado (`IP` real y el sustituto de
    `SDIG`, ambos verdes), un límite conocido y aceptado, no un fallo: con
    una asignatura real fija en la rueda de color y solo 8 sustitutos
    posibles, no siempre hay sitio para separar los 9 con margen amplio."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    page_1a = next(p for p in pages if p.curso == "1ºA" and p.semestre == 1)
    page_2a = next(p for p in pages if p.curso == "2ºA" and p.semestre == 1)

    acronyms_2a = ["AAED", "AC", "OGE", "RC"]  # 4 de las 5 de 2ºA, mismo criterio que el fixture ya usado del "repetidor"
    sel_events = [
        e for e in events
        if (e.curso == "1ºA" and e.semestre == 1)
        or (e.curso == "2ºA" and e.semestre == 1 and e.subject_acronym in acronyms_2a)
    ]
    colors_vibrant = _assign_colors(
        sel_events, page_1a.legend + page_2a.legend,
        palette=_FALLBACK_PALETTE_VIBRANT, avoid_hue_collisions=True,
    )
    assert set(colors_vibrant) == {"CAL", "IG", "IP", "MD", "SDIG", "AAED", "AC", "OGE", "RC"}
    assert len(set(colors_vibrant.values())) == 9, "9 asignaturas deben quedar con 9 hex distintos, sin duplicados"

    hues = {acr: _hue_degrees(_hex_to_rgb(hexcolor)) for acr, hexcolor in colors_vibrant.items()}
    min_dists = {
        a: min(_hue_distance(hues[a], hues[b]) for b in hues if b != a)
        for a in hues
    }
    assert min_dists["AC"] >= 20 and min_dists["RC"] >= 20, (
        f"AC/RC deberían quedar bien separados tras evitar la colisión: {min_dists}"
    )
    assert min(min_dists.values()) >= 10, f"límite conocido: ningún par debería caer por debajo de ~10° ({min_dists})"


def test_ensure_contrast_margin_sube_un_color_flojo_sin_tocar_uno_bueno():
    """`_ensure_contrast_margin` es un no-op sobre un color que ya alcanza
    el objetivo (verificado con `MD`, `#984ea3`, 5.31:1 de partida) y
    ajusta luminosidad (conservando matiz/saturación) sobre uno que no
    (verificado con `IG`, `#377eb8`, 4.34:1 de partida — el caso real que
    motivó esta función: un color de leyenda del PDF por debajo del
    mínimo AA, ya no protegido por "fidelidad al PDF" porque esa fidelidad
    se abandonó a propósito en otros dos puntos de este mismo módulo)."""
    md_rgb = _hex_to_rgb("#984ea3")
    assert _ensure_contrast_margin(md_rgb) == md_rgb

    ig_rgb = _hex_to_rgb("#377eb8")
    assert max(_contrast_ratio(ig_rgb, _WHITE_RGB), _contrast_ratio(ig_rgb, _DARK_TEXT_RGB)) < 4.5
    ig_adjusted = _ensure_contrast_margin(ig_rgb)
    assert ig_adjusted != ig_rgb
    contrast = max(_contrast_ratio(ig_adjusted, _WHITE_RGB), _contrast_ratio(ig_adjusted, _DARK_TEXT_RGB))
    assert contrast >= _MIN_CONTRAST_TARGET, f"IG ajustado: {contrast:.2f}"


def test_halo_del_anillo_de_choque_se_distingue_sobre_los_8_colores():
    """La marca de choque (ver `overlapBoxShadow` en el JS embebido) es un
    halo + un anillo rojo, no un contorno rojo solo — un contorno rojo solo
    (probado primero) daba un contraste de 1.0-1.6:1 contra los colores de
    la paleta, muy por debajo del mínimo de 3:1 de WCAG para elementos no
    textuales (SC 1.4.11). El halo de cada color reutiliza `_text_color_for`
    (blanco o negro, el que dé más contraste) en vez de ser un único color
    fijo — con colores claros y oscuros en la misma paleta, ni blanco ni
    negro solos bastan contra todos a la vez."""
    for hexcolor in _FALLBACK_PALETTE:
        rgb = _hex_to_rgb(hexcolor)
        halo_rgb = _hex_to_rgb(_text_color_for(hexcolor))
        contrast = _contrast_ratio(halo_rgb, rgb)
        assert contrast >= 3.0, f"halo vs {hexcolor}: contraste {contrast:.2f} < 3.0"


def test_anillo_rojo_se_distingue_del_halo_sea_cual_sea():
    """El anillo rojo debe distinguirse tanto del halo blanco como del halo
    negro (los dos únicos valores posibles de `_text_color_for`) — si no,
    la marca se vería como un único borde plano en vez de la doble banda
    que la hace leerse como 'aviso'. Umbral en 4.0, no el mínimo WCAG de
    3.0: el primer rojo (`#d00000`) pasaba el mínimo pero solo por 0.05 de
    margen contra el halo negro (3.05) — encontrado revisando una captura
    real, se veía flojo sobre los tonos claros de la paleta (los que
    llevan halo negro). `#ed2121` es el rojo con mayor contraste MÍNIMO
    posible contra blanco y negro a la vez (maximizado por barrido de
    lightness) — el umbral de 4.0 deja margen real, no roza el mínimo."""
    ring_rgb = _hex_to_rgb(_OVERLAP_RING_HEX)
    contrast_white = _contrast_ratio(ring_rgb, _WHITE_RGB)
    contrast_black = _contrast_ratio(ring_rgb, _DARK_TEXT_RGB)
    assert contrast_white >= 4.0, f"anillo vs halo blanco: {contrast_white:.2f}"
    assert contrast_black >= 4.0, f"anillo vs halo negro: {contrast_black:.2f}"


def test_paleta_de_respaldo_tiene_3_escalones_de_luminosidad_separados():
    """La garantía real de la paleta ya no es de matiz, es de luminosidad
    (escala de grises): 3 escalones (`_PALETTE_TIERS`: oscuro/medio/claro),
    cualquier par de colores en escalones DISTINTOS debe tener una
    diferencia de luminancia relativa clara — eso sobrevive a cualquier
    daltonismo común y a una impresión en blanco y negro, a diferencia del
    matiz (ver docstring del módulo: una paleta con matiz bien separado y
    contraste AA se demostró insuficiente en esta sesión). Dos colores del
    MISMO escalón pueden ser parecidos — esperado, ver `_PATTERNS` más
    abajo, el patrón de fondo es quien los distingue en ese caso."""
    assert set(_PALETTE_TIERS) == set(_FALLBACK_PALETTE)
    min_cross_tier_diff = min(
        abs(_relative_luminance(_hex_to_rgb(a)) - _relative_luminance(_hex_to_rgb(b)))
        for a, b in itertools.combinations(_FALLBACK_PALETTE, 2)
        if _PALETTE_TIERS[a] != _PALETTE_TIERS[b]
    )
    assert min_cross_tier_diff >= 0.12, f"diferencia mínima entre escalones: {min_cross_tier_diff:.3f}"


def test_escalones_de_la_paleta_sobreviven_protanopia_deuteranopia_tritanopia():
    """La separación entre escalones de luminosidad (verificada arriba en
    visión normal) tiene que seguir siendo clara bajo los tres tipos de
    daltonismo comunes — es precisamente la propiedad que el matiz NO
    tiene y la luminosidad sí."""
    for label, matrix in _CVD_MATRICES.items():
        simulated = {h: _simulate(matrix, _hex_to_rgb(h)) for h in _FALLBACK_PALETTE}
        min_dist = min(
            _euclidean(simulated[a], simulated[b])
            for a, b in itertools.combinations(_FALLBACK_PALETTE, 2)
            if _PALETTE_TIERS[a] != _PALETTE_TIERS[b]
        )
        assert min_dist >= 40, f"{label}: escalones distintos casi indistinguibles (distancia {min_dist:.1f})"


def test_patrones_cubren_los_pares_confundibles_de_la_paleta_de_respaldo():
    """Aplicada a la propia paleta de respaldo: cualquier par de sus 8
    colores que resulte confundible bajo algún daltonismo o en escala de
    grises debe tener patrones distintos en el reparto cíclico real."""
    colors = {f"acr{i}": hexcolor for i, hexcolor in enumerate(_FALLBACK_PALETTE)}
    _assert_patterns_cover_confusable_colors(colors)


def test_asignacion_de_patrones_es_ciclica_y_determinista():
    acronyms = ["CAL", "IG", "IP", "MD", "RC", "SDIG"]
    patterns = _assign_patterns(acronyms)
    assert patterns == _assign_patterns(list(acronyms))  # determinista
    assert [patterns[a] for a in acronyms] == _PATTERNS[:4] + _PATTERNS[:2]


def test_colores_reales_del_pdf_rojos_se_sustituyen(sample_pdf_path):
    """CAL (rojo, `(228,26,28)`) y SDIG (naranja muy cálido saturado,
    `(255,127,0)`) son los dos colores de leyenda reales del PDF de
    referencia que caen en la banda reservada — verificado que
    `_assign_colors` nunca les asigna su color de leyenda tal cual."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    page0 = pages[0]
    colors = _assign_colors([e for e in events if e.curso == "1ºA" and e.semestre == 1], page0.legend)

    legend_by_acr = {entry.acronym: entry.color for entry in page0.legend}
    assert _is_reddish(legend_by_acr["CAL"])
    assert _is_reddish(legend_by_acr["SDIG"])
    assert not _is_reddish(_hex_to_rgb(colors["CAL"]))
    assert not _is_reddish(_hex_to_rgb(colors["SDIG"]))
    assert colors["CAL"] != _rgb_to_hex(legend_by_acr["CAL"])


def test_asignacion_de_color_es_determinista(sample_pdf_path):
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    legend = [entry for page in pages for entry in page.legend]
    assert _assign_colors(events, legend) == _assign_colors(events, legend)


def test_ninguna_asignatura_real_del_documento_completo_recibe_color_rojizo(sample_pdf_path):
    """Barrido sobre las 24 páginas a la vez (65 asignaturas distintas,
    caso extremo que agota varias veces la paleta de respaldo) — ninguna
    puede terminar en un color rojizo, ni por leyenda ni por sustituto."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    legend = [entry for page in pages for entry in page.legend]
    colors = _assign_colors(events, legend)
    assert len(colors) > 20
    reddish = {acr: hexcolor for acr, hexcolor in colors.items() if _is_reddish(_hex_to_rgb(hexcolor))}
    assert reddish == {}


def test_ninguna_asignatura_real_del_documento_completo_queda_por_debajo_del_margen(sample_pdf_path):
    """Mismo barrido de las 24 páginas a la vez, esta vez sobre el
    contraste de texto: ninguna de las 65 asignaturas puede quedar por
    debajo de `_MIN_CONTRAST_TARGET`, sea su color real de la leyenda o
    sustituto — `_ensure_contrast_margin` se aplica a los dos casos dentro
    de `_assign_colors`, no solo a uno."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    legend = [entry for page in pages for entry in page.legend]
    colors = _assign_colors(events, legend)
    assert len(colors) > 20
    flojos = {}
    for acr, hexcolor in colors.items():
        rgb = _hex_to_rgb(hexcolor)
        contrast = max(_contrast_ratio(rgb, _WHITE_RGB), _contrast_ratio(rgb, _DARK_TEXT_RGB))
        if contrast < _MIN_CONTRAST_TARGET:
            flojos[acr] = (hexcolor, round(contrast, 2))
    assert flojos == {}


def test_seleccion_realista_de_5_asignaturas_1a(sample_pdf_path):
    """Caso real (1ºA, semestre 1, las 5 asignaturas de la fixture de
    p.0): tras sustituir CAL y SDIG (los dos colores rojizos reales),
    cualquier par confundible entre sí debe tener patrones distintos — ya
    no se exige separación de matiz (ver docstring del módulo: eso se
    demostró insuficiente), el patrón es la garantía real."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    page0 = pages[0]
    colors = _assign_colors([e for e in events if e.curso == "1ºA" and e.semestre == 1], page0.legend)
    assert set(colors) == {"CAL", "IG", "IP", "MD", "SDIG"}
    _assert_patterns_cover_confusable_colors(colors)


def test_dos_colores_de_leyenda_reales_e_iguales_no_se_duplican(sample_pdf_path):
    """Caso real de dos páginas distintas: `MD` (1ºA) y `RC` (2ºA) tienen
    EXACTAMENTE el mismo color de leyenda real (`#984ea3`, ninguno de los
    dos rojizo) — invisible mientras solo se mire una página a la vez, pero
    visible en cuanto una selección junta asignaturas de los dos cursos
    (un repetidor arrastrando una asignatura de otro curso). El primero en
    orden alfabético (`MD`) se queda con el color real; `RC` pasa a la
    paleta de respaldo en vez de duplicarlo."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    page0 = pages[0]
    page_2a_sem1 = next(p for p in pages if p.curso == "2ºA" and p.semestre == 1)

    md_legend = next(e for e in page0.legend if e.acronym == "MD")
    rc_legend = next(e for e in page_2a_sem1.legend if e.acronym == "RC")
    assert md_legend.color == rc_legend.color, "la fixture asume que MD y RC comparten color real en el PDF"

    sel_events = [
        e for e in events
        if (e.curso == "1ºA" and e.semestre == 1) or (e.curso == "2ºA" and e.semestre == 1 and e.subject_acronym == "RC")
    ]
    colors = _assign_colors(sel_events, page0.legend + page_2a_sem1.legend)
    assert set(colors) == {"CAL", "IG", "IP", "MD", "RC", "SDIG"}
    assert colors["MD"] == _rgb_to_hex(md_legend.color)
    assert colors["RC"] != colors["MD"]


def test_escena_real_de_6_asignaturas_de_dos_cursos_se_distingue_bajo_daltonismo(sample_pdf_path):
    """EL test que encontró el fallo de esta ronda — reproduce el caso
    exacto reportado: 5 asignaturas de 1ºA (`CAL`, `IG`, `IP`, `MD`,
    `SDIG`, mezcla de colores reales de leyenda y sustitutos) + `RC` de
    2ºA a la vez, con `MD`↔`RC` chocando en 20 fechas reales. Antes del
    patrón, `IG`/`MD` e `IG`/`RC` eran confundibles bajo varios tipos de
    daltonismo Y en escala de grises (comprobado con `_confusable_reasons`
    contra los colores reales de esta selección, no una paleta aislada) —
    con el patrón como segundo canal, la comprobación central del archivo
    exige que esos pares tengan patrones distintos."""
    pages = parse_document(str(sample_pdf_path))
    events, _ = resolve_document(pages)
    page0 = pages[0]
    page_2a_sem1 = next(p for p in pages if p.curso == "2ºA" and p.semestre == 1)

    sel_events = [
        e for e in events
        if (e.curso == "1ºA" and e.semestre == 1) or (e.curso == "2ºA" and e.semestre == 1 and e.subject_acronym == "RC")
    ]
    colors = _assign_colors(sel_events, page0.legend + page_2a_sem1.legend)
    assert set(colors) == {"CAL", "IG", "IP", "MD", "RC", "SDIG"}

    # Confirma que este caso SIGUE teniendo pares realmente confundibles
    # por color — si algún día dejara de tenerlos (p.ej. porque cambiaron
    # los colores reales del PDF de referencia), esta aserción avisaría de
    # que el test ya no ejercita lo que dice ejercitar.
    confusable_pairs = [
        (a, b) for a, b in itertools.combinations(colors, 2) if _confusable_reasons(colors[a], colors[b])
    ]
    assert confusable_pairs, "se esperaba al menos un par confundible por color en este caso real"

    _assert_patterns_cover_confusable_colors(colors)


def test_anillo_de_marcador_contra_fondo_de_celda():
    """El marcador compacto de Mes (`.day-marker`, ver render/html.py) se
    rellena con el color de la asignatura — un color validado para
    CONTRASTE DE TEXTO encima (≥5:1, `_MIN_CONTRAST_TARGET`), una relación
    distinta de la que necesita un marcador sin texto: aquí lo que tiene
    que distinguirse del fondo de la celda (`--card-bg`) es el propio
    color, como elemento gráfico (WCAG 1.4.11, mínimo 3:1). Un color
    oscurecido a propósito para que el texto blanco brille encima (p.ej.
    `CAL`, `#1c456e`) es agua y aceite con "destacar sobre una celda ya
    oscura en modo oscuro" — encontrado con captura real, casi invisible.

    En vez de medir esa segunda relación color de paleta por color de
    paleta (cambiaría con cada asignatura nueva), el marcador lleva un
    anillo FIJO por tema, independiente del color de relleno — el límite
    pasa a ser "¿el anillo contrasta con la celda?", constante. Objetivo
    de margen real (no el mínimo de 3:1 rozando), mismo criterio que el
    resto de la paleta de este documento."""
    ring_light = _contrast_ratio(_hex_to_rgb(_MARKER_RING_LIGHT_HEX), _hex_to_rgb(_CARD_BG_LIGHT_HEX))
    ring_dark = _contrast_ratio(_hex_to_rgb(_MARKER_RING_DARK_HEX), _hex_to_rgb(_CARD_BG_DARK_HEX))
    assert ring_light >= 3.0, f"anillo claro: {ring_light:.2f}:1 contra la celda de modo claro"
    assert ring_dark >= 3.0, f"anillo oscuro: {ring_dark:.2f}:1 contra la celda de modo oscuro"
    # Margen real, no solo el mínimo — mismo criterio que el resto de la
    # paleta de este documento (ver `_MIN_CONTRAST_TARGET`, objetivo 5:1
    # para texto; aquí el mínimo normativo es 3:1, pero tampoco se roza).
    assert ring_light >= 4.0
    assert ring_dark >= 4.0

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

"""HTML autocontenido (sin dependencias externas en tiempo de ejecución):
vista mensual y semanal, color estable por asignatura, solapes resaltados.
Los solapes se calculan sobre la SELECCIÓN ya filtrada, no sobre la página
completa — dos grupos elegidos pueden solaparse aunque ninguno de los dos
solape consigo mismo dentro de su propia página.

Los conflictos (dos grupos DISTINTOS de la selección con horas solapadas la
misma fecha) se calculan una sola vez, en Python, con
`select.conflicts.find_conflicts` — la misma función que usa `report.py` —
y se embeben ya resueltos en el HTML. El JS no vuelve a calcular solapes por
su cuenta: solo pinta lo que Python ya decidió, para que el aviso del
informe de confirmación y el aviso del propio calendario nunca puedan
divergir entre sí.
"""
from __future__ import annotations

import colorsys
import json

from horario_uca.model import (
    CalendarEvent,
    DIA_NOMBRE,
    ExamClassConflict,
    ExamConflict,
    ExamEntry,
    ScheduleConflict,
    SubjectLegendEntry,
)
from horario_uca.select import find_conflicts
from horario_uca.select.exams import EXAM_NOMINAL_DURATION_MINUTES, find_exam_class_conflicts, find_exam_conflicts

# Paleta cualitativa de respaldo — usada tanto para un acrónimo que no
# aparezca en ninguna leyenda pasada a build_html, como para sustituir un
# color de leyenda rojizo o repetido (ver `_is_reddish` — el rango rojo
# está reservado en exclusiva para los avisos de choque de horario). Por
# lo demás sigue siendo cierto que el color de leyenda del PDF es la
# fuente preferida — build_html no inventa un color propio salvo en esos
# casos.
#
# **Segunda versión, por LUMINOSIDAD (escala de grises), no por matiz.**
# La primera (derivada de Okabe-Ito, verificada solo con simulación de
# deuteranopia sobre la paleta aislada) fallaba en la práctica: probada con
# una selección real de 6 asignaturas (colores de leyenda REALES del PDF
# mezclados con sustitutos de esta paleta, nunca antes ejercitado así), 5
# de las 6 convergían al mismo amarillo/gris bajo deuteranopia — la paleta
# se repartía por el eje rojo-verde, exactamente el que ese daltonismo
# colapsa; matiz HSL separado y contraste con blanco/negro no dicen nada
# sobre si dos colores SATURADOS se confunden entre sí bajo daltonismo, ni
# siquiera simulándolo, si no se prueba TODA la combinación real (leyenda +
# respaldo) y no solo la paleta de respaldo aislada.
#
# 3 escalones de luminosidad relativa (WCAG), bien separados entre sí
# (≥0.16 de diferencia mínima verificada, `tests/test_colors.py`):
# oscuro (~0.055), medio (~0.22-0.24), claro (~0.50). DOS colores del MISMO
# escalón pueden tener matices que se confundan bajo algún tipo de
# daltonismo — deliberado: la luminosidad (que sobrevive a cualquier forma
# común de daltonismo y a una impresión en blanco y negro) es la garantía
# real; el matiz solo ayuda a la vista con color normal. La distinción
# entre dos colores del mismo escalón la lleva el PATRÓN de fondo
# (`_PATTERNS`/`_assign_patterns` más abajo), no el color — el color nunca
# es el único canal de identidad de una asignatura.
#
# Orden intercalado por escalón (oscuro/medio/claro/oscuro/medio/claro/…),
# no agrupado por escalón — con solo 3-4 sustitutos en un documento típico,
# agrupar habría dado 3 oscuros seguidos antes de llegar a ningún claro;
# intercalado, hasta las primeras 3 sustituciones ya cubren los 3 escalones.
#
# Los tres "medio" están retocados (`#3b85ce`→`#498ed1` etc., mismo matiz y
# saturación, solo un poco más claros) para alcanzar el umbral de MARGEN de
# `_MIN_CONTRAST_TARGET` (5.0), no solo el mínimo AA (4.5) — `#3b85ce` daba
# exactamente 4.50, margen cero: cualquier retoque futuro lo habría dejado
# por debajo sin que ningún test lo notara hasta que alguien lo viera en
# una captura. Ver `_ensure_contrast_margin`, que hizo este ajuste.
_PALETTE_TIERS: dict[str, str] = {
    "#1c456e": "oscuro", "#552697": "oscuro", "#12493c": "oscuro",
    "#498ed1": "medio", "#9e78d2": "medio", "#279b7e": "medio",
    "#99bfe6": "claro", "#cab1ec": "claro",
}
_FALLBACK_PALETTE = [
    "#1c456e",  # azul oscuro
    "#498ed1",  # azul medio
    "#99bfe6",  # azul claro
    "#552697",  # púrpura oscuro
    "#9e78d2",  # púrpura medio
    "#cab1ec",  # púrpura claro
    "#12493c",  # teal oscuro
    "#279b7e",  # teal medio
]

# Paleta alternativa "vibrante" — modo NO accesible, para quien no necesita
# la paleta de arriba (activable/desactivable, ver `render/html.py`'s
# PALETTE_ACCESSIBLE/PALETTE_VIBRANT en la plantilla JS y el interruptor
# "Colores accesibles"). Deliberadamente por MATIZ, no por luminosidad — el
# modo vibrante no lleva patrón de fondo por defecto (ver `_palette_table`,
# `use_patterns=False`), así que aquí el color SÍ vuelve a ser el único
# canal de identidad, al revés que en `_FALLBACK_PALETTE` — no hace falta
# que sobreviva a escala de grises ni a daltonismo (para eso está el modo
# accesible), pero si dos colores de esta paleta quedan demasiado cerca en
# matiz entre sí, el propio modo vibrante deja de servir para lo que es:
# distinguir asignaturas a simple vista. Por eso `_assign_colors` con esta
# paleta usa `avoid_hue_collisions=True` (selección por distancia de matiz
# mínima a TODO lo ya reclamado, real o sustituto — no solo el ciclo fijo
# de `_FALLBACK_PALETTE`).
#
# 8 matices repartidos por la rueda de color FUERA de la zona reservada al
# rojo/aviso (`_is_reddish`: ≤35° o ≥340° con saturación≥0.25), saturación
# alta (0.80) para el efecto "vibrante" pedido, con `_ensure_contrast_margin`
# ya aplicado (mismo umbral de 5.0 que la paleta accesible — ver
# `_MIN_CONTRAST_TARGET`, los umbrales de contraste NO cambian entre modos).
# Verificado con las funciones reales del proyecto, no a ojo: los 8 dan
# `_is_reddish() == False` y contraste ≥5.0 contra blanco o `_DARK_TEXT_RGB`
# (máximo de los dos) tras el ajuste — dos de los ocho quedan con margen
# ajustado (5.03/5.04, matices 205°/300°), el resto entre 8.19 y 10.01.
_FALLBACK_PALETTE_VIBRANT = [
    "#dcab18",  # ámbar,      hue≈45°,  contraste 8.19
    "#7adc18",  # lima,       hue≈90°,  contraste 10.01
    "#18dc39",  # verde,      hue≈130°, contraste 9.39
    "#18dcbc",  # turquesa,   hue≈170°, contraste 9.95
    "#198fe2",  # azul cielo, hue≈205°, contraste 5.03
    "#1829dc",  # azul,       hue≈235°, contraste 8.79
    "#7a18dc",  # violeta,    hue≈270°, contraste 7.02
    "#e931e9",  # magenta,    hue≈300°, contraste 5.04
]

_WHITE_RGB = (255, 255, 255)
_DARK_TEXT_RGB = (26, 26, 26)

# Umbral de contraste que debe alcanzar TODO color de asignatura contra su
# mejor texto (blanco o `_DARK_TEXT_RGB`) — 5.0, no el mínimo AA de 4.5:1.
# Encontrado en esta sesión: varios colores medían exactamente 4.50-4.57,
# "pasando" el mínimo por un margen tan pequeño que un retoque posterior
# (o redondeo de otra herramienta) los dejaría por debajo sin que ningún
# test genérico de "¿pasa 4.5?" lo detectara. 5.0 deja margen real.
_MIN_CONTRAST_TARGET = 5.0

# Patrón de fondo por asignatura — el SEGUNDO canal, independiente del
# color: el acrónimo ya identifica el evento sin ambigüedad (está escrito
# en cada chip), así que el color es redundante para IDENTIFICAR una
# asignatura — el problema real es AGRUPAR de un vistazo sin tener que leer
# letra a letra, y ahí el color solo no basta bajo daltonismo (ver arriba).
# Se asigna por posición en la lista de acrónimos ordenada, cíclico cada 4,
# **independiente de si el color de ese acrónimo es real (de la leyenda) o
# sustituto** — el patrón no depende en absoluto del matiz, así que sigue
# distinguiendo aunque dos colores (reales o no) se confundan entre sí bajo
# cualquier tipo de daltonismo o en escala de grises.
_PATTERNS = ["solid", "diagonal", "dots", "horizontal"]

# Anillo del chip en choque (ver `.event-chip.overlap` en la plantilla más
# abajo): un halo + un anillo rojo, no un contorno rojo solo — un contorno
# rojo solo daba 1.0-1.6:1 de contraste contra los colores de la paleta
# (invisible en la práctica pese a "verse bien" en una captura con solo dos
# colores de prueba). El anillo rojo es fijo (mismo valor en claro y
# oscuro: el fondo relevante es el color de la asignatura, no cambia con el
# tema), pero **el halo NO puede ser un único color fijo** — con colores
# claros y oscuros en la misma paleta, ni blanco ni negro solos dan ≥3:1
# (mínimo WCAG no textual) contra todos a la vez. El halo de cada evento
# reutiliza el mismo valor que `textColor` (`_text_color_for`, ya
# calculado por color con el mismo criterio de contraste) en vez de
# necesitar su propio campo.
#
# `#d00000` (la primera versión) daba solo 3.05:1 contra el halo negro —
# de sobra el mínimo de 3:1, pero por muy poco margen (encontrado
# revisando la captura: el anillo se veía flojo sobre los tonos claros,
# que son justo los que llevan halo negro). `#ed2121` es el rojo con
# mayor contraste MÍNIMO posible contra los dos halos a la vez —
# maximizado por barrido de lightness — con margen ≥4.0:1 contra
# blanco Y negro, en vez de rozar 3:1 contra uno de los dos.
_OVERLAP_RING_HEX = "#ed2121"

# Anillo neutro de los marcadores compactos de la vista Mes en móvil (ver
# `.day-marker` más abajo). Un marcador es un círculo/cuadrado de 10px
# relleno con el color de la asignatura — el MISMO color que ya se validó
# a ≥5:1 de contraste de TEXTO sobre ese color (`_ensure_contrast_margin`),
# pero esa es una relación distinta de la que necesita un marcador: aquí no
# hay texto encima, el propio color tiene que distinguirse del FONDO DE LA
# CELDA (`--card-bg`) como elemento gráfico — WCAG 1.4.11, mínimo 3:1, no
# 4.5:1. Un color oscurecido a propósito para que el texto blanco brille
# encima (p.ej. `CAL` a `#1c456e`, 9.88:1 de texto) es exactamente lo
# contrario de lo que hace falta para destacar sobre una celda YA oscura en
# modo oscuro (`--card-bg` oscuro ronda su misma franja de luminosidad) —
# encontrado con captura real, no calculado antes de verlo: el punto de
# `CAL` casi desaparecía en la celda. Un anillo fijo, independiente del
# color de la asignatura, evita medir esa segunda relación color por color
# cada vez que la paleta cambie: el límite pasa a ser "¿el anillo contrasta
# con la celda?" (constante por tema), no "¿contrasta CADA color de la
# paleta con la celda?" (cambiaría con cada asignatura nueva). Verificado
# con margen real, no solo el mínimo de 3:1 — ver
# `tests/test_colors.py::test_anillo_de_marcador_contra_fondo_de_celda`.
_MARKER_RING_LIGHT_HEX = "#4a4a4a"
_MARKER_RING_DARK_HEX = "#d8d8dc"


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(hexcolor: str) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _is_reddish(rgb: tuple[int, int, int]) -> bool:
    """Rojo, granate o naranja muy cálido — el rango de matiz que este
    proyecto reserva en exclusiva para los avisos de choque de horario
    (`--overlap-ring`/`--conflict-*`). Un color de leyenda que caiga
    aquí NUNCA se usa tal cual para una asignatura, aunque sea el color
    real del PDF."""
    r, g, b = (c / 255 for c in rgb)
    hue, _, sat = colorsys.rgb_to_hls(r, g, b)
    hue_deg = hue * 360
    if sat < 0.25:  # gris/casi gris: ningún matiz "se lee" como rojo
        return False
    return hue_deg <= 35 or hue_deg >= 340


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        c_norm = c / 255
        return c_norm / 12.92 if c_norm <= 0.03928 else ((c_norm + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> float:
    lum_a, lum_b = _relative_luminance(rgb_a), _relative_luminance(rgb_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _ensure_contrast_margin(
    rgb: tuple[int, int, int], target: float = _MIN_CONTRAST_TARGET
) -> tuple[int, int, int]:
    """Si `rgb` no alcanza `target` de contraste con blanco NI con
    `_DARK_TEXT_RGB`, oscurece o aclara (lo que ya diera más contraste
    para ESE color, nunca los dos a la vez) hasta alcanzarlo, conservando
    matiz y saturación — un ajuste de luminosidad, no un color distinto.
    Caso real que motivó esto: `IG` (color de leyenda real del PDF de
    referencia, `#377eb8`) daba 4.34:1 con su mejor texto (blanco),
    por DEBAJO del mínimo AA de 4.5:1 — documentado antes como "límite
    conocido, fuera de alcance" porque la fidelidad al color del PDF
    parecía suficiente motivo para no tocarlo. Ya no lo es: esa fidelidad
    se abandonó a propósito al reservar el rojo y al resolver colisiones
    entre páginas — no hay razón para conservarla
    aquí y no en los demás casos. Se aplica a CUALQUIER color que vaya a
    usarse tal cual (de leyenda o de `_FALLBACK_PALETTE`), no solo a los
    que fallan el mínimo: varias entradas de la paleta pasaban 4.5 por
    0.00-0.07 de margen, tan poco que un retoque posterior las habría
    dejado por debajo sin que ningún test lo notara."""
    if max(_contrast_ratio(rgb, _WHITE_RGB), _contrast_ratio(rgb, _DARK_TEXT_RGB)) >= target:
        return rgb
    r, g, b = (c / 255 for c in rgb)
    hue, lightness, sat = colorsys.rgb_to_hls(r, g, b)
    # Si el blanco ya daba más contraste que el negro, oscurecer aumenta
    # ESE contraste más rápido que aclarar aumentaría el otro — y viceversa.
    darken = _contrast_ratio(rgb, _WHITE_RGB) >= _contrast_ratio(rgb, _DARK_TEXT_RGB)
    step = -0.002 if darken else 0.002
    best = rgb
    new_lightness = lightness
    for _ in range(400):
        new_lightness += step
        if new_lightness <= 0.0 or new_lightness >= 1.0:
            break
        r2, g2, b2 = colorsys.hls_to_rgb(hue, new_lightness, sat)
        candidate = (round(r2 * 255), round(g2 * 255), round(b2 * 255))
        best = candidate
        if max(_contrast_ratio(candidate, _WHITE_RGB), _contrast_ratio(candidate, _DARK_TEXT_RGB)) >= target:
            return candidate
    return best  # mejor esfuerzo si el matiz no permite llegar al objetivo


def _text_color_for(hexcolor: str) -> str:
    """Blanco o negro de texto, el que dé más contraste sobre este fondo —
    los chips de evento fijaban `color:#fff` siempre, que no cumple AA
    (4.5:1) contra varios colores de la paleta (ni de respaldo ni algunos
    de leyenda muy claros/saturados). Se decide una vez por color, no por
    evento, así que es determinista igual que el propio color."""
    rgb = _hex_to_rgb(hexcolor)
    contrast_white = _contrast_ratio(rgb, _WHITE_RGB)
    contrast_dark = _contrast_ratio(rgb, _DARK_TEXT_RGB)
    return "#ffffff" if contrast_white >= contrast_dark else "#1a1a1a"


def _pattern_overlay_for(hexcolor: str) -> str:
    """Color translúcido de las líneas/puntos de la barra de patrón —
    blanco u oscuro, el mismo criterio que `_text_color_for` (el que más
    contraste dé). Opacidad baja a propósito (bajada de .5/.4 a .38/.30 en
    esta sesión, a petición explícita): con el patrón confinado a una
    barra de 4-6px (ver `.pat-*::before`) ya no compite con el texto, pero
    un patrón muy marcado seguía leyéndose "cargado" — un acento sutil
    basta para que la barra se distinga, no hace falta que grite."""
    text = _text_color_for(hexcolor)
    return "rgba(255,255,255,.38)" if text == "#ffffff" else "rgba(20,20,20,.30)"


def _assign_patterns(acronyms: list[str]) -> dict[str, str]:
    """Un patrón de `_PATTERNS` por acrónimo, cíclico sobre la lista YA
    ordenada — independiente del color (real o sustituto) de cada uno, ver
    comentario de `_PATTERNS`. Determinista: mismo documento, mismo orden
    de acrónimos, siempre el mismo reparto."""
    return {acr: _PATTERNS[i % len(_PATTERNS)] for i, acr in enumerate(acronyms)}


def _legend_colors(legend: list[SubjectLegendEntry] | dict[str, tuple[int, int, int]] | None) -> dict[str, tuple[int, int, int]]:
    if legend is None:
        return {}
    if isinstance(legend, dict):
        return legend
    # list[SubjectLegendEntry]: puede venir de varias páginas concatenadas;
    # primera aparición gana (el mismo acrónimo debería tener el mismo color
    # en todo el PDF, pero no se fuerza esa suposición aquí).
    colors: dict[str, tuple[int, int, int]] = {}
    for entry in legend:
        colors.setdefault(entry.acronym, entry.color)
    return colors


def _hue_degrees(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    hue, _, _ = colorsys.rgb_to_hls(r, g, b)
    return hue * 360


def _hue_distance(hue_a: float, hue_b: float) -> float:
    d = abs(hue_a - hue_b) % 360
    return min(d, 360 - d)


def _next_fallback_color(used_hex: set[str], start_index: int, palette: list[str] = _FALLBACK_PALETTE) -> tuple[str, int]:
    """Siguiente color de `palette` no usado todavía (mismo hex
    exacto), empezando en `start_index` — determinista (mismo documento,
    mismo orden de acrónimos, siempre el mismo resultado), nunca aleatorio.
    No evita matices parecidos a colores ya usados: eso importaba cuando el
    color era la única señal de identidad, pero ya no lo es EN MODO
    ACCESIBLE — el patrón de fondo (`_assign_patterns`) es quien garantiza
    que dos asignaturas con colores parecidos (incluso casi idénticos bajo
    algún tipo de daltonismo) se distingan igual, así que forzar variedad
    de matiz aquí ya no hace falta y solo agotaba antes la paleta (con solo
    3 familias de matiz por diseño — ver comentario de `_FALLBACK_PALETTE`
    — cualquier sustituto tenía muchas papeletas de acabar cerca de algún
    color real ya en uso). **El modo vibrante NO usa esta función** —
    ver `_next_fallback_color_by_hue_distance`, porque ahí sí vuelve a
    faltar el patrón que justifica ignorar el matiz."""
    n = len(palette)
    for offset in range(n):
        idx = (start_index + offset) % n
        candidate = palette[idx]
        if candidate not in used_hex:
            return candidate, idx + 1
    return palette[start_index % n], start_index + 1


def _next_fallback_color_by_hue_distance(used_hexes: list[str], used_hex_exact: set[str], palette: list[str]) -> str:
    """Variante para el modo vibrante (sin patrón de fondo por defecto, ver
    `_palette_table`): sin ese segundo canal, el color vuelve a ser el
    único que distingue una asignatura de otra, así que aquí SÍ hace falta
    evitar matices cercanos a los ya reclamados (reales de leyenda o
    sustitutos anteriores), no solo el mismo hex exacto como en
    `_next_fallback_color`. Elige, entre los candidatos de `palette` sin
    reclamar por hex exacto, el que quede más lejos en matiz del más
    cercano de `used_hexes` — máx-mín, no óptimo global, pero determinista
    (empate resuelto por orden de `palette`) y suficiente para el tamaño
    real de esta paleta (8 colores).

    **Encontrado con datos reales, no hipotético**: sin esta función, el
    caso de 9 asignaturas ya usado como fixture del "repetidor" (`CAL`,
    `IG`, `IP`, `MD`, `SDIG` de 1ºA + `AAED`, `AC`, `OGE`, `RC` de 2ºA —
    dos páginas que reparten el mismo azul/verde/púrpura "de fábrica" del
    generador de PDFs de la ESI) le tocaba a `RC`
    (sustituto) un matiz de solo 2° de distancia del azul REAL de `AC`,
    literalmente indistinguibles en modo vibrante sin patrón. Con esta
    selección, la distancia mínima de cualquier color al resto sube a
    ≥12° en ese mismo caso — sigue habiendo un par ajustado (`IP` real,
    verde de leyenda, y el sustituto de `SDIG`), documentado como límite
    conocido, no arreglado con más código: con una asignatura REAL fija
    en un hueco de la rueda de color y solo 8 sustitutos posibles, no
    siempre hay sitio para separarlos todos con margen amplio."""
    used_hues = [_hue_degrees(_hex_to_rgb(h)) for h in used_hexes]

    def min_dist(candidate: str) -> float:
        if not used_hues:
            return 999.0
        candidate_hue = _hue_degrees(_hex_to_rgb(candidate))
        return min(_hue_distance(candidate_hue, uh) for uh in used_hues)

    pool = [c for c in palette if c not in used_hex_exact] or list(palette)
    return max(pool, key=lambda c: round(min_dist(c), 3))


def _assign_colors(
    events: list[CalendarEvent],
    legend: list[SubjectLegendEntry] | dict[str, tuple[int, int, int]] | None = None,
    extra_acronyms: list[str] | None = None,
    palette: list[str] = _FALLBACK_PALETTE,
    avoid_hue_collisions: bool = False,
) -> dict[str, str]:
    # `extra_acronyms`: asignaturas que necesitan color/patrón aunque no
    # tengan ningún CalendarEvent — un examen añadido a mano sin ningún
    # grupo de clase seleccionado es exactamente ese caso; sin esto,
    # `_events_to_json` encontraría el acrónimo del examen sin color
    # asignado.
    acronyms = sorted({e.subject_acronym for e in events} | set(extra_acronyms or []))
    legend_colors = _legend_colors(legend)

    # Primera pasada: qué acrónimos se quedan con su color de leyenda real
    # (no rojizo Y no exactamente el mismo hex ya reclamado por un
    # acrónimo anterior) — se calcula ANTES de repartir ningún sustituto.
    # El caso de dos colores reales que coinciden es real, no hipotético:
    # dos asignaturas de PÁGINAS distintas pueden tener el mismo color de
    # leyenda sin que eso sea un error del PDF (cada página reparte su
    # propia paleta) — invisible con una sola página, pero al combinar
    # asignaturas de dos cursos a la vez (un alumno puede repetir o
    # adelantar una asignatura de otro curso) las dos pueden acabar
    # en el mismo calendario. Encontrado con un caso real:
    # `MD` (1ºA) y `RC` (2ºA) comparten el mismo púrpura `#984ea3`. El
    # primer acrónimo en orden alfabético que reclama un color se lo
    # queda; cualquier otro real que coincida EXACTO se trata igual que si
    # fuera rojizo — pasa a la paleta de respaldo en vez de duplicar el
    # color. Solo se evita el mismo hex exacto, no un matiz parecido: eso
    # importaba cuando el color era la única señal de identidad, ya no lo
    # es — ver `_next_fallback_color` y `_assign_patterns`.
    #
    # `_ensure_contrast_margin` se aplica ANTES de comprobar duplicados
    # (el hex "reclamado" es el ya ajustado, no el crudo de la leyenda) —
    # caso real que lo motivó: `IG` (`#377eb8`) daba 4.34:1 con su mejor
    # texto, por debajo del mínimo AA de 4.5. No se conserva el color
    # crudo del PDF "porque es fiel a la leyenda": esa fidelidad ya se
    # abandonó a propósito para el rojo y para las colisiones entre
    # páginas — no hay motivo para mantenerla aquí y no ahí.
    real_hex: dict[str, str] = {}
    claimed_hex: set[str] = set()
    for acr in acronyms:
        rgb = legend_colors.get(acr)
        if rgb is None or _is_reddish(rgb):
            continue
        hexcolor = _rgb_to_hex(_ensure_contrast_margin(rgb))
        if hexcolor in claimed_hex:
            continue
        real_hex[acr] = hexcolor
        claimed_hex.add(hexcolor)

    colors: dict[str, str] = {}
    used_hex: set[str] = set(claimed_hex)
    fallback_index = 0
    for acr in acronyms:
        if acr in real_hex:
            colors[acr] = real_hex[acr]
            continue
        # Sin color de leyenda, o el de la leyenda es rojo/granate/naranja
        # muy cálido — ese matiz está reservado para los avisos de choque
        # (ver `_is_reddish`), así que se sustituye por el siguiente color
        # libre de la paleta de respaldo en vez de usar el del PDF tal cual.
        if avoid_hue_collisions:
            hexcolor = _next_fallback_color_by_hue_distance(list(used_hex), used_hex, palette)
        else:
            hexcolor, fallback_index = _next_fallback_color(used_hex, fallback_index, palette)
        colors[acr] = hexcolor
        used_hex.add(hexcolor)
    return colors


def _palette_table(colors: dict[str, str], use_patterns: bool = True) -> dict[str, dict]:
    """Por acrónimo: color, texto y patrón — la misma combinación que antes
    calculaban por separado `_events_to_json`/`_exams_to_json`, ahora en un
    solo sitio para que el modo vibrante/accesible se pueda intercambiar en
    el CLIENTE (ver `PALETTE_ACCESSIBLE`/`PALETTE_VIBRANT` y
    `applyPalette` en la plantilla JS de `build_html`) sin volver a pedir
    nada al servidor — el evento/examen solo necesita su acrónimo para
    resolver el resto en cualquiera de las dos tablas.

    `use_patterns=False` (modo vibrante): todo acrónimo se manda con
    `pattern: "solid"` — no hay ninguna regla CSS `.pat-solid::before`
    (comprobado: solo existen `.pat-diagonal/.dots/.horizontal`), así que
    "solid" ya significa "sin textura" con el CSS que ya existía, sin
    añadir ninguna regla nueva."""
    text_colors = {acr: _text_color_for(hexcolor) for acr, hexcolor in colors.items()}
    # `colors` ya está en orden de acrónimo ascendente (así lo construye
    # `_assign_colors`, y los dict de Python conservan orden de inserción)
    # — se reutiliza ese mismo orden para repartir patrón, así el reparto
    # de patrones es independiente de cuál acrónimo tuvo color real y cuál
    # sustituto, pero sigue siendo determinista sobre el mismo documento.
    patterns = _assign_patterns(list(colors.keys())) if use_patterns else {acr: "solid" for acr in colors}
    pattern_overlays = {acr: _pattern_overlay_for(hexcolor) for acr, hexcolor in colors.items()}
    return {
        acr: {
            "color": colors[acr],
            "textColor": text_colors[acr],
            "pattern": patterns[acr],
            "patternOverlay": pattern_overlays[acr],
        }
        for acr in colors
    }


_DEFAULT_STYLE = {"color": "#999999", "textColor": "#ffffff", "pattern": "solid", "patternOverlay": "rgba(255,255,255,.5)"}


def _events_to_json(events: list[CalendarEvent], colors: dict[str, str]) -> str:
    table = _palette_table(colors, use_patterns=True)
    payload = []
    for e in sorted(events, key=lambda e: (e.date, e.start_time)):
        style = table.get(e.subject_acronym, _DEFAULT_STYLE)
        payload.append(
            {
                "kind": "class",
                "acronym": e.subject_acronym,
                "name": e.subject_name,
                "group": e.group_code,
                "type": e.group_type,
                "date": e.date,
                "weekday": e.weekday,
                "start": e.start_time,
                "end": e.end_time,
                "room": e.room,
                "movedFrom": e.moved_from,
                "color": style["color"],
                "textColor": style["textColor"],
                "pattern": style["pattern"],
                "patternOverlay": style["patternOverlay"],
            }
        )
    return json.dumps(payload, ensure_ascii=False)


def _exams_to_json(exams: list[ExamEntry], colors: dict[str, str], exams_available: list[ExamEntry] | None = None) -> str:
    """Mismo criterio de color/patrón/texto que `_events_to_json` — un
    examen se identifica visualmente con el mismo color+patrón que las
    clases de su asignatura (`colors` ya incluye su acrónimo, ver
    `extra_acronyms` en `_assign_colors`), para que un alumno reconozca de
    un vistazo a qué asignatura pertenece. **Nunca se manda una hora de
    fin**: el PDF de convocatoria no la trae, y la interfaz no debe
    inventar un intervalo — solo la hora de inicio.

    `exams_available` (opcional) son exámenes de convocatorias RELEVANTES
    para la selección pero no incluidas en el calendario (regla de
    inclusión automática no verificada para esa convocatoria, ver
    `pipeline.py::generate_calendar`) — se mandan con `included: false`
    para que la pestaña Exámenes los muestre como catálogo (atenuados,
    nunca ausentes), pero nunca entran en `find_exam_conflicts`/
    `find_exam_class_conflicts` (no tiene sentido avisar de un choque con
    algo que todavía no está en el calendario del alumno) ni en el `.ics`
    (eso lo decide únicamente la lista `exams` que recibe `build_ics`)."""
    table = _palette_table(colors, use_patterns=True)

    def _entry(e: ExamEntry, included: bool) -> dict:
        style = table.get(e.acronym, {**_DEFAULT_STYLE, "color": "#666666"})
        return {
            "kind": "exam",
            "code": e.code,
            "acronym": e.acronym,
            "name": e.name,
            "curso": e.curso,
            "date": e.date,
            "start": e.start_time,
            "room": e.room,
            "convocatoria": e.convocatoria,
            "included": included,
            "color": style["color"],
            "textColor": style["textColor"],
            "pattern": style["pattern"],
            "patternOverlay": style["patternOverlay"],
        }

    all_entries = [(e, True) for e in exams] + [(e, False) for e in (exams_available or [])]
    all_entries.sort(key=lambda pair: (pair[0].date, pair[0].start_time))
    payload = [_entry(e, included) for e, included in all_entries]
    return json.dumps(payload, ensure_ascii=False)


def _exam_conflicts_to_json(conflicts: list[ExamConflict]) -> str:
    payload = [
        {
            "date": c.date,
            "a": {"code": c.exam_a.code, "acronym": c.exam_a.acronym, "name": c.exam_a.name, "start": c.exam_a.start_time},
            "b": {"code": c.exam_b.code, "acronym": c.exam_b.acronym, "name": c.exam_b.name, "start": c.exam_b.start_time},
            "certain": c.certain,
        }
        for c in conflicts
    ]
    return json.dumps(payload, ensure_ascii=False)


def _exam_class_conflicts_to_json(conflicts: list[ExamClassConflict]) -> str:
    payload = [
        {
            "date": c.exam.date,
            "exam": {"code": c.exam.code, "acronym": c.exam.acronym, "name": c.exam.name, "start": c.exam.start_time},
            "event": {
                "acronym": c.event.subject_acronym,
                "group": c.event.group_code,
                "start": c.event.start_time,
                "end": c.event.end_time,
            },
        }
        for c in conflicts
    ]
    return json.dumps(payload, ensure_ascii=False)


def _conflict_side(event: CalendarEvent) -> dict:
    return {
        "acronym": event.subject_acronym,
        "group": event.group_code,
        "curso": event.curso,
        "start": event.start_time,
        "end": event.end_time,
        "room": event.room,
    }


def _conflicts_to_json(conflicts: list[ScheduleConflict]) -> str:
    payload = [
        {
            "date": c.date,
            "a": _conflict_side(c.event_a),
            "b": _conflict_side(c.event_b),
            "mismoCurso": c.event_a.curso == c.event_b.curso,
        }
        for c in conflicts
    ]
    return json.dumps(payload, ensure_ascii=False)


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f4f5f7;
    --text: #1a1a1a;
    --header-bg: #1a2744;
    --header-text: #fff;
    --btn-bg: #2d3f66;
    --btn-border: #445686;
    --btn-bg-hover: #3a4f80;
    --btn-bg-active: #4f6bb0;
    --btn-border-active: #6f8ad0;
    --card-bg: #fff;
    --card-shadow: rgba(0,0,0,.08);
    --card-shadow-soft: rgba(0,0,0,.06);
    --muted-text: #888;
    --weekday-label-text: #555;
    --week-day-heading: #333;
    /* Anillo de choque de un chip: mismo valor en claro y oscuro a propósito
       — ver más abajo, junto a `.event-chip.overlap`, por qué. El halo NO
       está aquí: varía por color de fondo, se manda inline por evento. */
    --overlap-ring: __OVERLAP_RING__;
    /* Anillo neutro del marcador compacto de Mes (ver `.day-marker` más
       abajo y el comentario de `_MARKER_RING_LIGHT_HEX` en Python) — fijo
       por tema, independiente del color de la asignatura que rellena el
       marcador. */
    --marker-ring: __MARKER_RING_LIGHT__;
    --moved-border: #222;
    --moved-badge-bg: rgba(0,0,0,.35);
    --conflict-bg: #fdecea;
    --conflict-border: #d00000;
    --conflict-text: #7a1212;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14161a;
      --text: #e8e8ea;
      --header-bg: #0d1424;
      --header-text: #f0f0f2;
      --btn-bg: #22304e;
      --btn-border: #3a4d78;
      --btn-bg-hover: #2c3d62;
      --btn-bg-active: #3f5590;
      --btn-border-active: #5a76bf;
      --card-bg: #1f2229;
      --card-shadow: rgba(0,0,0,.4);
      --card-shadow-soft: rgba(0,0,0,.3);
      --muted-text: #9a9aa2;
      --weekday-label-text: #b0b0b8;
      --week-day-heading: #d5d5da;
      --moved-border: #d5d5da;
      --moved-badge-bg: rgba(255,255,255,.25);
      --conflict-bg: #3a1a1a;
      --conflict-border: #ff6b6b;
      --conflict-text: #ffd2d2;
      --marker-ring: __MARKER_RING_DARK__;
    }
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    margin: 0; padding: 0; background: var(--bg); color: var(--text);
  }
  header {
    background: var(--header-bg); color: var(--header-text); padding: 14px 16px;
    display: flex; flex-direction: column; gap: 10px;
  }
  header h1 { font-size: 17px; margin: 0; font-weight: 600; overflow-wrap: break-word; }
  /* Interruptor de paleta — visible justo bajo el título, no enterrado en
     ningún menú ni pantalla aparte (pedido explícito: quien necesita el
     modo accesible a menudo no sabe que la opción existe, así que el
     modo accesible es el que se ve por defecto — ver "POR DEFECTO: el
     modo accesible" — y el interruptor para desactivarlo tiene que ser
     de los primeros elementos de la página, no algo que haya que buscar). */
  .palette-toggle { display: flex; flex-direction: column; gap: 3px; }
  .palette-toggle-row { display: flex; align-items: center; gap: 9px; cursor: pointer; user-select: none; }
  .palette-toggle-row input {
    position: absolute; opacity: 0; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0);
  }
  .palette-toggle-switch {
    position: relative; flex: none; width: 36px; height: 21px; border-radius: 999px;
    background: var(--btn-border); transition: background .15s;
  }
  .palette-toggle-switch::before {
    content: ""; position: absolute; top: 2px; left: 2px; width: 17px; height: 17px; border-radius: 50%;
    background: #fff; transition: transform .15s;
  }
  .palette-toggle-row input:checked + .palette-toggle-switch { background: var(--btn-bg-active); }
  .palette-toggle-row input:checked + .palette-toggle-switch::before { transform: translateX(15px); }
  .palette-toggle-row input:focus-visible + .palette-toggle-switch { outline: 2px solid var(--btn-border-active); outline-offset: 2px; }
  .palette-toggle-label { font-size: 13.5px; font-weight: 600; }
  .palette-toggle-help { margin: 0 0 0 45px; font-size: 11.5px; color: var(--header-text); opacity: .75; line-height: 1.35; }
  /* Dos filas, no una — "qué vista ves" (Mes/Semana/Exámenes) y "qué
     periodo ves" (←/mes/→) son dos cosas distintas y compiten por el
     mismo ancho si van en la misma fila. Con las DOS pestañas originales
     (Mes/Semana) + la navegación cabían justas a 380px; al añadir una
     tercera pestaña (Exámenes) en la MISMA fila, `#period-label` se
     encogía hasta truncar el nombre del mes por completo ("sep…") — el
     mismo desborde que ya se había resuelto una vez para dos pestañas,
     reintroducido al pasar a tres. Separar en dos filas quita la
     competencia por el ancho en vez de encoger más el periodo. */
  .view-tabs { display: flex; align-items: center; gap: 6px; }
  .period-nav { display: flex; align-items: center; gap: 6px; }
  button {
    background: var(--btn-bg); color: var(--header-text); border: 1px solid var(--btn-border); border-radius: 6px;
    padding: 6px 10px; cursor: pointer; font-size: 13px; flex: none;
  }
  button:hover { background: var(--btn-bg-hover); }
  button.active { background: var(--btn-bg-active); border-color: var(--btn-border-active); }
  /* flex:1 + min-width:0 (no un min-width fijo de 160px) para que el
     periodo se achique o trunque con ellipsis en vez de forzar el salto de
     línea de toda la barra de controles a un ancho estrecho — encontrado
     con una captura real a 380px. Ya en su
     propia fila (ver arriba), este mínimo debería bastar de sobra incluso
     con nombres de mes largos, pero se deja el mismo mecanismo de reserva
     por si acaso — no cuesta nada y evita un desborde silencioso si algún
     día el nombre de mes fuera aún más largo (u otro idioma). */
  #period-label {
    font-weight: 600; flex: 1; min-width: 0; text-align: center; font-size: 13px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  main { padding: 16px; max-width: 1200px; margin: 0 auto; }
  .legend { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; padding: 10px; background: var(--card-bg); border-radius: 8px; box-shadow: 0 1px 3px var(--card-shadow); }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 13px; }
  .swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
  /* minmax(0, 1fr), NUNCA solo 1fr — el mínimo implícito de una columna de
     grid es `auto` (el ancho de su contenido más ancho), así que un día
     con chips de texto largo empuja SU columna más ancha que las demás
     y las vacías quedan aplastadas a tiras de ~30px — visto de verdad
     con una selección real (lunes/martes con clases, miércoles-domingo
     vacíos). `minmax(0, 1fr)` fuerza las 7 columnas a medir EXACTAMENTE
     lo mismo pase lo que pase dentro, y `.event-chip` ya trunca con
     ellipsis lo que no quepa. */
  .month-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
  .weekday-label { text-align: center; font-weight: 600; font-size: 12px; color: var(--weekday-label-text); padding: 4px 0; }
  .day-cell { position: relative; background: var(--card-bg); border-radius: 6px; min-height: 90px; padding: 4px; box-shadow: 0 1px 2px var(--card-shadow-soft); }
  .day-cell.empty { background: transparent; box-shadow: none; }
  /* Choque en la vista Mes compacta: UNA marca por DÍA, no por marcador —
     con varias asignaturas chocando el mismo día (caso real: un
     repetidor de 9 asignaturas, 8 eventos algunos días), marcar el
     anillo rojo en CADA marcador dejaba los martes como "seis puntos
     rojos idénticos": el rojo, reservado para el choque, pasaba a ser
     el color dominante del día y "qué asignaturas hay" desaparecía.
     Encontrado con captura real, no razonado. Los marcadores individuales
     (`.day-marker`) YA NO llevan ningún anillo de choque — conservan
     siempre su anillo neutro (`--marker-ring`), así el color de cada uno
     sigue siendo legible. El choque se indica una vez, en la esquina de
     la celda — el detalle de CON QUÉ choca cada evento ya está a un toque
     de distancia (la vista Semana, adonde salta tocar el día). Solo en el
     ancho compacto: a ancho normal, cada chip de texto completo ya lleva
     su propio anillo+halo por evento (`overlapBoxShadow`, sin cambios) y
     un badge de celda adicional sería redundante. */
  .day-cell.has-conflict::after {
    content: "⚠"; position: absolute; top: 2px; right: 4px;
    font-size: 11px; line-height: 1; color: var(--overlap-ring); display: none;
  }
  @media (max-width: 480px) {
    .day-cell.has-conflict::after { display: block; }
  }
  .day-number { font-size: 12px; color: var(--muted-text); margin-bottom: 4px; }
  .event-chip {
    position: relative;
    font-size: 10.5px; padding: 2px 4px 2px 8px; border-radius: 4px; color: #fff; margin-bottom: 2px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: default;
  }
  /* Segundo canal de identidad, independiente del color — ver
     `_PATTERNS`/`_assign_patterns` en Python, por qué hace falta: dos
     asignaturas del mismo escalón de luminosidad pueden compartir matiz
     bajo cualquier tipo de daltonismo, y el patrón sigue distinguiéndolas
     porque no depende del color en absoluto.
     **El patrón vive SOLO en una barra lateral de 4px (`::before`), nunca
     detrás del texto** — una primera versión lo aplicaba a todo el fondo
     del chip y el texto quedaba ilegible encima (peor aún en escala de
     grises): el patrón tiene que identificar la asignatura, no competir
     con la información. El cuerpo del chip sigue siendo el color liso de
     la asignatura (`background-color`, nunca el shorthand `background` —
     lo resetearía a `none`); la barra se pinta ENCIMA de ese fondo, sin
     `background-color` propio, así que donde el patrón no dibuja nada se
     sigue viendo el color base por debajo. `--pattern-overlay` (blanco u
     oscuro translúcido, el que más contraste dé contra ESE fondo, opacidad
     baja para que sea un acento discreto) se manda inline por evento. */
  .event-chip.pat-diagonal::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background-image: repeating-linear-gradient(45deg, var(--pattern-overlay) 0 1.5px, transparent 1.5px 4px); }
  .event-chip.pat-dots::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background-image: radial-gradient(var(--pattern-overlay) 1px, transparent 1.2px); background-size: 4px 4px; }
  .event-chip.pat-horizontal::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background-image: repeating-linear-gradient(180deg, var(--pattern-overlay) 0 1.5px, transparent 1.5px 4px); }
  /* La marca de choque (halo + anillo rojo) NO se define aquí como regla
     de clase — se manda inline por evento desde `overlapBoxShadow(e)` más
     abajo, porque el color del halo tiene que variar según el fondo del
     chip (ver esa función por qué: ni blanco ni negro fijos bastan contra
     los 8 colores de la paleta a la vez) y `box-shadow` no se puede
     "acumular" entre una regla de clase y un valor inline — el inline
     sustituye al de la clase entero, no se suman. Un contorno rojo solo
     (sin halo), probado primero, daba 1.0-1.6:1 de contraste contra los 8
     colores de `_FALLBACK_PALETTE` — muy por debajo del mínimo de
     contraste no textual de WCAG (3:1) — invisible en la práctica pese a
     "verse bien" en una captura con solo dos colores de prueba. */
  /* Barra de "trasladado" en el borde DERECHO, no el izquierdo — ese lado
     ya lo ocupa la barra de patrón de la asignatura (arriba); con las dos
     en el mismo borde se pisarían. */
  .event-chip.moved { border-right: 3px solid var(--moved-border); }
  /* Examen: mismo color/patrón que las clases de su asignatura (mismo
     sistema seguro para daltonismo/escala de grises ya construido para
     clases), más un borde discontinuo — un canal de FORMA, no de color,
     para que "esto es un examen" se distinga incluso en blanco y negro o
     bajo cualquier daltonismo. El color del borde se manda inline por
     evento (`e.textColor`, ya elegido por contraste contra ESE fondo
     concreto — mismo criterio que el halo del anillo de choque), nunca
     fijo: un borde de un solo color no bastaría contra toda la paleta a
     la vez. El prefijo "Examen:" en el propio texto es el discriminador
     principal, el borde es solo refuerzo. */
  .event-chip.exam, .week-event.exam { border-style: dashed; border-width: 2px; }
  /* Marcadores compactos de la vista Mes en móvil — con 7 columnas iguales
     (ver `.month-grid` arriba) cada día mide ~40px a 380px de ancho, y un
     chip de texto truncado a "1…"/"⚠…" no transmite ninguna información:
     parece un dato y no lo es. Por debajo de `--month-compact-bp` cada día
     se representa como un marcador sin texto — círculo para clase, cuadrado
     para examen (canal de FORMA, igual criterio que el borde discontinuo de
     `.event-chip.exam`: se distingue en blanco y negro y bajo cualquier
     daltonismo sin depender del color) — y `.day-chips` (el texto completo)
     se oculta. Por encima del umbral, todo sigue exactamente como antes:
     `.day-markers` oculto, `.day-chips` visible. */
  .day-markers { display: none; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 2px; cursor: pointer; }
  /* 10px, no 8px — a 8px, incluso con el anillo de abajo, costaba verlo en
     una captura real. Anillo de 1px con `--marker-ring` SIEMPRE presente,
     sin excepción (ni siquiera en choque, ver `.day-cell.has-conflict` más
     abajo): el relleno es el color de la asignatura, que se valida para
     contraste de TEXTO encima (≥5:1, ver `_ensure_contrast_margin`), no
     para destacar como forma pequeña sobre `--card-bg` — un color
     oscurecido a propósito para que el texto brille encima (p.ej. `CAL`)
     puede fundirse casi por completo con una celda oscura sin este
     anillo. */
  .day-marker { width: 10px; height: 10px; flex: none; box-shadow: 0 0 0 1px var(--marker-ring); }
  .day-marker.marker-class { border-radius: 50%; }
  .day-marker.marker-exam { border-radius: 1.5px; }
  .day-marker-more { font-size: 9px; color: var(--muted-text); line-height: 10px; }
  @media (max-width: 480px) {
    .day-chips { display: none; }
    .day-markers { display: flex; }
  }
  .week-view { display: flex; flex-direction: column; gap: 10px; }
  .week-day { background: var(--card-bg); border-radius: 8px; padding: 10px 14px; box-shadow: 0 1px 3px var(--card-shadow); }
  .week-day h3 { margin: 0 0 8px 0; font-size: 14px; color: var(--week-day-heading); }
  .week-event {
    position: relative;
    display: flex; align-items: center; gap: 10px; padding: 6px 8px 6px 12px; border-radius: 6px; margin-bottom: 6px; color: #fff;
  }
  /* Mismo patrón que `.event-chip` (barra lateral, nunca detrás del
     texto), escala algo mayor porque la fila de Semana es más alta. */
  .week-event.pat-diagonal::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 6px; background-image: repeating-linear-gradient(45deg, var(--pattern-overlay) 0 2px, transparent 2px 5px); }
  .week-event.pat-dots::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 6px; background-image: radial-gradient(var(--pattern-overlay) 1.2px, transparent 1.5px); background-size: 6px 6px; }
  .week-event.pat-horizontal::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 6px; background-image: repeating-linear-gradient(180deg, var(--pattern-overlay) 0 2px, transparent 2px 5px); }
  .week-event .time { font-weight: 600; font-size: 13px; min-width: 110px; }
  .week-event .info { font-size: 13px; }
  .week-event .room { font-size: 12px; opacity: .9; }
  .moved-badge { font-size: 10px; background: var(--moved-badge-bg); padding: 1px 5px; border-radius: 4px; margin-left: 4px; }
  .empty-msg { color: var(--muted-text); padding: 20px; text-align: center; }
  .conflict-banner {
    position: relative;
    background: var(--conflict-bg); color: var(--conflict-text); border: 1px solid var(--conflict-border);
    border-radius: 8px; padding: 12px 40px 12px 14px; margin-bottom: 16px; font-size: 13px;
  }
  /* Estado neutro: la combinación tuvo choques, pero todos ya pasaron — no
     es una alarma que necesite rojo, pero el dato no se borra. Reutiliza
     los mismos tokens que una tarjeta normal en vez de definir otros
     nuevos, para que el modo oscuro no necesite duplicar la paleta. */
  .conflict-banner.neutral {
    background: var(--card-bg); color: var(--text); border-color: var(--btn-border);
  }
  .conflict-banner .conflict-title { font-weight: 700; display: block; margin-bottom: 4px; }
  /* Sin max-height/overflow propios: que la lista crezca con la página en
     vez de abrir un tercer scroll anidado dentro del banner — encontrado a
     380px real junto con el bug de la cabecera Mes/Semana de arriba. */
  .conflict-banner ul { margin: 8px 0 0 0; padding-left: 18px; }
  .conflict-banner li { margin-bottom: 4px; }
  /* Botón de cerrar: 44x44px de área táctil real (no solo el glifo visual,
     que es más pequeño) — mínimo recomendado para objetivos táctiles en
     móvil, y no hay razón para hacerlo más pequeño en escritorio. */
  .conflict-banner .conflict-close {
    position: absolute; top: 0; right: 0; width: 44px; height: 44px;
    background: transparent; border: none; color: inherit; font-size: 20px; line-height: 1;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
  }
  .conflict-banner .conflict-close:focus-visible { outline: 2px solid currentColor; outline-offset: -4px; }
  /* El botón para reabrir es deliberadamente discreto (texto pequeño, sin
     fondo de alarma) — cerrar es "ahora no", no "olvídalo", así que este
     enlace tiene que seguir siendo fácil de encontrar sin volver a gritar. */
  .conflict-reopen { margin-bottom: 16px; }
  .conflict-reopen button {
    background: transparent; border: 1px solid var(--btn-border); color: var(--text);
    border-radius: 6px; padding: 8px 12px; font-size: 13px; cursor: pointer; min-height: 44px;
  }
  /* Pestaña Exámenes: lista plana ordenada por fecha, no una rejilla — los
     exámenes son pocos y dispersos en el tiempo, una rejilla de mes vacía
     casi siempre no ayudaría. */
  .exam-list { display: flex; flex-direction: column; gap: 8px; }
  /* Solo aparece cuando hay más de una convocatoria a la vez (varios PDF
     de convocatoria subidos) — con una sola, la lista plana de siempre
     basta y no hace falta ninguna cabecera. */
  .exam-group { margin-top: 16px; }
  .exam-group:first-child { margin-top: 0; }
  .exam-group-title {
    /* Sin text-transform: capitalize — el texto ya llega bien formado
       desde JS ("Convocatoria de febrero de 2027"); capitalize pondría
       en mayúscula también las preposiciones ("De"), que en español no
       llevan mayúscula en mitad de una frase. */
    font-size: 13.5px; font-weight: 700; color: var(--week-day-heading);
    margin: 0 0 8px;
  }
  .exam-row {
    background: var(--card-bg); border-radius: 8px; padding: 10px 14px;
    box-shadow: 0 1px 3px var(--card-shadow); display: flex; flex-direction: column; gap: 4px;
  }
  .exam-row .exam-date { font-size: 13px; font-weight: 600; color: var(--week-day-heading); }
  .exam-row .exam-info { display: flex; align-items: center; gap: 8px; font-size: 13.5px; }
  .exam-row .exam-swatch { width: 12px; height: 12px; border-radius: 3px; flex: none; border-style: dashed; border-width: 2px; }
  /* Nombre de la asignatura y "hora · aula" van en líneas SEPARADAS a
     propósito (no un único bloque con `flex-wrap`) — con un solo bloque,
     un nombre largo envolvía en un punto distinto según cuánto ocupara,
     dejando a veces un "·" colgando solo al final de una línea. Separando
     ambas líneas, el punto de corte nunca cae en mitad de un separador. */
  .exam-row .exam-meta { font-size: 13px; }
  .exam-row .exam-room { font-size: 12.5px; color: var(--muted-text); }
  .exam-row .exam-conflict-note { font-size: 12.5px; color: var(--conflict-text); }
  .exam-row.past { opacity: .6; }
  /* Examen relevante para la selección pero no incluido en el calendario
     (regla de convocatoria sin verificar, ver pipeline.py) — atenuado y
     con borde discontinuo, nunca con el mismo aspecto que uno ya en el
     calendario (se confundirían sin poder distinguirlos, ver el aviso que
     motivó esto). */
  .exam-row.not-included { opacity: .6; border: 1.5px dashed var(--btn-border); box-shadow: none; }
  .exam-row .exam-availability-note { font-size: 12.5px; color: var(--muted-text); margin-top: 2px; }
  .exam-add-btn {
    margin-left: 4px; background: none; border: none; color: var(--btn-bg-active); text-decoration: underline;
    font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 0;
  }
  .event-chip, .week-event { cursor: pointer; }
  .event-detail {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 20;
    background: var(--card-bg); color: var(--text); border-top: 1px solid var(--btn-border);
    box-shadow: 0 -4px 16px var(--card-shadow); padding: 14px 16px calc(14px + env(safe-area-inset-bottom));
    max-width: 1200px; margin: 0 auto; border-radius: 12px 12px 0 0;
  }
  .event-detail[hidden] { display: none; }
  .event-detail .row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .event-detail .swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; flex: none; }
  .event-detail h4 { margin: 0 0 4px 0; font-size: 15px; display: flex; align-items: center; }
  .event-detail p { margin: 2px 0; font-size: 13px; color: var(--muted-text); }
  .event-detail .moved-note, .event-detail .conflict-note { margin-top: 8px; font-size: 13px; padding: 8px 10px; border-radius: 8px; }
  .event-detail .moved-note { background: var(--btn-bg); color: var(--header-text); }
  .event-detail .conflict-note { background: var(--conflict-bg); color: var(--conflict-text); border: 1px solid var(--conflict-border); }
  .event-detail button.close {
    background: transparent; border: none; color: var(--muted-text); font-size: 20px; line-height: 1; padding: 4px 6px;
  }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="palette-toggle">
    <label class="palette-toggle-row" for="palette-toggle-input">
      <input type="checkbox" id="palette-toggle-input" checked>
      <span class="palette-toggle-switch" aria-hidden="true"></span>
      <span class="palette-toggle-label">Colores accesibles</span>
    </label>
    <p class="palette-toggle-help">Paleta pensada para daltonismo e impresión en blanco y negro. Desactívala para colores más vivos.</p>
  </div>
  <div class="view-tabs">
    <button id="btn-view-month" class="active">Mes</button>
    <button id="btn-view-week">Semana</button>
    <button id="btn-view-exams">Exámenes</button>
  </div>
  <div class="period-nav" id="period-nav">
    <button id="btn-prev">&larr;</button>
    <span id="period-label"></span>
    <button id="btn-next">&rarr;</button>
  </div>
</header>
<main>
  <div id="conflict-banner" class="conflict-banner" style="display:none"></div>
  <div id="conflict-reopen" class="conflict-reopen" style="display:none"></div>
  <div id="exam-conflict-banner" class="conflict-banner" style="display:none"></div>
  <div id="exam-conflict-reopen" class="conflict-reopen" style="display:none"></div>
  <div class="legend" id="legend"></div>
  <div id="content"></div>
</main>
<div id="event-detail" class="event-detail" hidden></div>
<script>
const EVENTS = __EVENTS_JSON__;
const EXAMS = __EXAMS_JSON__;
const CONFLICTS = __CONFLICTS_JSON__;
const EXAM_CONFLICTS = __EXAM_CONFLICTS_JSON__;
const EXAM_CLASS_CONFLICTS = __EXAM_CLASS_CONFLICTS_JSON__;
const EXAM_NOMINAL_DURATION_MINUTES = __EXAM_NOMINAL_DURATION__;
// Dos tablas completas (acrónimo -> color/texto/patrón), ver
// `_palette_table` en build_html — el interruptor "Colores accesibles"
// solo decide cuál de las dos usar, nunca vuelve a pedir nada al
// servidor (ver `applyPalette` más abajo).
const PALETTE_ACCESSIBLE = __PALETTE_ACCESSIBLE_JSON__;
const PALETTE_VIBRANT = __PALETTE_VIBRANT_JSON__;
EVENTS.forEach((e, i) => { e._i = i; });
EXAMS.forEach((e, i) => { e._i = i; });
// EXAMS trae TODOS los exámenes relevantes para la selección, incluidos o
// no (ver `_exams_to_json`) — la pestaña Exámenes los muestra todos como
// catálogo, pero el calendario en sí (Mes/Semana, legend, paginación) solo
// debe reflejar lo que de verdad está en el .ics: nunca un examen "que se
// podría añadir" mezclado con los que sí están.
const EXAMS_ON_CALENDAR = EXAMS.filter(e => e.included !== false);

const DIA_NOMBRE = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"];
const MES_NOMBRE = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"];

function toMinutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// Un sufijo "(s)" pegado ("1 choque(s)") no concuerda en número con el
// artículo/adjetivo del resto de la frase y lo lee gente sin conocimiento
// técnico — cada frase con cantidad se escribe entera en singular y en
// plural, nunca con un sufijo, y esta función solo elige cuál de las dos.
function pluralPhrase(n, singular, plural) {
  return n === 1 ? singular : plural;
}

// CONFLICTS ya viene calculado por Python (select.conflicts.find_conflicts,
// la misma función que usa el informe de confirmación de la Fase 4) — el JS
// no recalcula solapes, solo indexa por evento para poder pintar .overlap
// en el chip/fila correspondiente.
function eventKey(e) {
  return `${e.date}|${e.acronym}|${e.group}|${e.start}`;
}
const CONFLICT_KEYS = new Set();
CONFLICTS.forEach(c => {
  CONFLICT_KEYS.add(`${c.date}|${c.a.acronym}|${c.a.group}|${c.a.start}`);
  CONFLICT_KEYS.add(`${c.date}|${c.b.acronym}|${c.b.group}|${c.b.start}`);
});
function isConflict(e) {
  return CONFLICT_KEYS.has(eventKey(e));
}

// Mismo criterio que arriba, para los dos tipos de choque de examen —
// indexados por código+fecha (la clave estable de un examen, nunca
// siglas, ver `parse/exams.py`).
const EXAM_CONFLICT_CODES = new Set();
EXAM_CONFLICTS.forEach(c => {
  EXAM_CONFLICT_CODES.add(`${c.date}|${c.a.code}`);
  EXAM_CONFLICT_CODES.add(`${c.date}|${c.b.code}`);
});
const EXAM_CLASS_CONFLICT_CODES = new Set();
EXAM_CLASS_CONFLICTS.forEach(c => { EXAM_CLASS_CONFLICT_CODES.add(`${c.date}|${c.exam.code}`); });
function isExamConflict(exam) {
  return EXAM_CONFLICT_CODES.has(`${exam.date}|${exam.code}`) || EXAM_CLASS_CONFLICT_CODES.has(`${exam.date}|${exam.code}`);
}

function fechaLarga(iso) {
  const d = parseDate(iso);
  return `${d.getDate()} de ${MES_NOMBRE[d.getMonth()]} de ${d.getFullYear()}`;
}

function diaDeIso(iso) {
  const d = parseDate(iso);
  return DIA_NOMBRE[(d.getDay() + 6) % 7];
}

function movedSentence(e) {
  if (!e.movedFrom) return null;
  return `Esta clase se traslada del ${diaDeIso(e.movedFrom)} ${fechaLarga(e.movedFrom)} al ${diaDeIso(e.date)} ${fechaLarga(e.date)}.`;
}

function conflictSentence(e) {
  const other = CONFLICTS.find(c =>
    (c.a.acronym === e.acronym && c.a.group === e.group && c.a.start === e.start && c.date === e.date) ||
    (c.b.acronym === e.acronym && c.b.group === e.group && c.b.start === e.start && c.date === e.date)
  );
  if (!other) return null;
  const rival = (other.a.acronym === e.acronym && other.a.group === e.group) ? other.b : other.a;
  const rivalCurso = !other.mismoCurso && rival.curso ? ` (${rival.curso})` : "";
  const motivo = other.mismoCurso
    ? "no se puede cursar esta combinación de grupos."
    : "al cursar asignaturas de más de un curso a la vez puede que no exista ninguna combinación sin choques.";
  return `Choca con ${rival.acronym} ${rival.group}${rivalCurso} (${rival.start}–${rival.end}${rival.room ? ", aula " + rival.room : ""}) — ${motivo}`;
}

// El panel de detalle es `position: fixed; bottom: 0` — sin reservar
// espacio para su propia altura, cubre la cola del contenido al hacer
// scroll hasta el final (mismo patrón de bug que `.actionbar` en
// `index.html`: un elemento fijo sin padding-bottom que lo compense tapa
// contenido real). Encontrado
// aquí barriendo posiciones de scroll con una selección de examenes
// larga (5 exámenes) — con pocos exámenes el contenido nunca llegaba a
// necesitar scroll y el solape no se manifestaba, exactamente el motivo
// por el que esta verificación se hace con contenido largo, no solo
// corto. `openDetailBox`/`closeDetailBox` centralizan la apertura para
// que la reserva de espacio no dependa de acordarse de aplicarla en cada
// sitio que abre el panel (clase Y examen).
function openDetailBox(html) {
  const box = document.getElementById("event-detail");
  box.innerHTML = html;
  box.hidden = false;
  document.body.style.paddingBottom = box.offsetHeight + "px";
  box.querySelector(".close").addEventListener("click", closeDetailBox);
}
function closeDetailBox() {
  const box = document.getElementById("event-detail");
  box.hidden = true;
  document.body.style.paddingBottom = "";
}

function showEventDetail(e) {
  const moved = movedSentence(e);
  const conflict = conflictSentence(e);
  openDetailBox(`
    <div class="row">
      <h4><span class="swatch" style="background:${e.color}"></span>${e.acronym} ${e.group}</h4>
      <button class="close" aria-label="Cerrar">&times;</button>
    </div>
    <p>${e.name} · ${e.type}</p>
    <p>${e.start}–${e.end}${e.room ? " · Aula " + e.room : ""} · ${fechaLarga(e.date)}</p>
    ${moved ? `<div class="moved-note">${moved}</div>` : ""}
    ${conflict ? `<div class="conflict-note">⚠ ${conflict}</div>` : ""}
  `);
}

// Mismo panel de detalle que un evento de clase (`#event-detail`), nunca
// uno nuevo — el examen es otro tipo de fila, pero la interacción táctil
// (tocar el chip para ver el detalle) es la misma. Nunca muestra un
// intervalo de horas: solo la hora de inicio (el PDF de convocatoria no
// trae hora de fin, y este fichero no la inventa en ningún sitio).
function examConflictSentence(exam) {
  const other = EXAM_CONFLICTS.find(c =>
    c.date === exam.date && (c.a.code === exam.code || c.b.code === exam.code)
  );
  if (other) {
    const rival = other.a.code === exam.code ? other.b : other.a;
    return other.certain
      ? `Coincide con el examen de ${rival.name} el mismo día a las ${rival.start}. Cuando se abra el plazo, solicita llamamiento especial para una de ellas.`
      : `El mismo día hay también examen de ${rival.name}, a las ${rival.start} — sin hora de fin no se puede confirmar si se solapan, revísalo cuando se abra el plazo.`;
  }
  const classConflict = EXAM_CLASS_CONFLICTS.find(c => c.date === exam.date && c.exam.code === exam.code);
  if (classConflict) {
    const ev = classConflict.event;
    return `Coincide con tu clase de ${ev.acronym} ${ev.group} (${ev.start}–${ev.end}) ese mismo día — la duración del examen mostrada aquí es una estimación.`;
  }
  return null;
}

function showExamDetail(exam) {
  const conflict = examConflictSentence(exam);
  openDetailBox(`
    <div class="row">
      <h4><span class="swatch" style="background:${exam.color}"></span>Examen: ${exam.acronym}</h4>
      <button class="close" aria-label="Cerrar">&times;</button>
    </div>
    <p>${exam.name} · Curso ${exam.curso}º</p>
    <p>${exam.start}${exam.room ? " · Aula " + exam.room : " · Aula por confirmar"} · ${fechaLarga(exam.date)}</p>
    ${conflict ? `<div class="conflict-note">⚠ ${conflict}</div>` : ""}
  `);
}

document.getElementById("content").addEventListener("click", (evt) => {
  if (evt.target.closest('[data-action="add-convocatorias"]')) {
    addConvocatoriasFromHere();
    return;
  }
  const chipEl = evt.target.closest("[data-i]");
  if (chipEl) {
    if (chipEl.dataset.kind === "exam") {
      const exam = EXAMS[Number(chipEl.dataset.i)];
      if (exam) showExamDetail(exam);
    } else {
      const e = EVENTS[Number(chipEl.dataset.i)];
      if (e) showEventDetail(e);
    }
    return;
  }
  const dayEl = evt.target.closest("[data-day]");
  if (dayEl) goToWeekOf(dayEl.dataset.day);
});

function conflictItem(c) {
  const aRoom = c.a.room ? `, aula ${c.a.room}` : "";
  const bRoom = c.b.room ? `, aula ${c.b.room}` : "";
  const aCurso = !c.mismoCurso && c.a.curso ? ` (${c.a.curso})` : "";
  const bCurso = !c.mismoCurso && c.b.curso ? ` (${c.b.curso})` : "";
  return `<li>${fechaLarga(c.date)}: <strong>${c.a.acronym} ${c.a.group}</strong>${aCurso} (${c.a.start}–${c.a.end}${aRoom}) choca con <strong>${c.b.acronym} ${c.b.group}</strong>${bCurso} (${c.b.start}–${c.b.end}${bRoom})</li>`;
}

// Un choque es "pasado" cuando ha terminado la clase más tardía de las dos
// implicadas, no a medianoche de su fecha — comparado contra un Date local
// (isoLocal/parseDate, nunca .toISOString(), mismo criterio que el resto de
// este fichero: ver la nota de isoLocal más abajo sobre el bug de huso).
function conflictEndDate(c) {
  const day = parseDate(c.date);
  const [ah, am] = c.a.end.split(":").map(Number);
  const [bh, bm] = c.b.end.split(":").map(Number);
  const aEnd = new Date(day); aEnd.setHours(ah, am, 0, 0);
  const bEnd = new Date(day); bEnd.setHours(bh, bm, 0, 0);
  return aEnd > bEnd ? aEnd : bEnd;
}
function isConflictPast(c, now) {
  return conflictEndDate(c) <= now;
}

// "Cerrado" NUNCA se guarda entre sesiones (ninguna escritura a
// localStorage aquí a propósito): cerrar es "ahora no", no "no me lo
// vuelvas a decir" — si el alumno vuelve a abrir el calendario, el choque
// sigue existiendo y el aviso debe volver a aparecer. Arranca siempre
// abierto en cada carga de la página.
let conflictBannerClosed = false;

function buildConflictBanner() {
  const box = document.getElementById("conflict-banner");
  const reopen = document.getElementById("conflict-reopen");
  if (CONFLICTS.length === 0) {
    box.style.display = "none";
    box.innerHTML = "";
    reopen.style.display = "none";
    reopen.innerHTML = "";
    return;
  }

  const now = new Date();
  const pendientes = CONFLICTS.filter(c => !isConflictPast(c, now));
  const pasados = CONFLICTS.filter(c => isConflictPast(c, now));

  if (conflictBannerClosed) {
    box.style.display = "none";
    box.innerHTML = "";
    reopen.style.display = "block";
    const label = pendientes.length > 0
      ? pluralPhrase(pendientes.length, `Ver la fecha que choca`, `Ver las ${pendientes.length} fechas que chocan`)
      : pluralPhrase(pasados.length, `Ver el choque ya pasado`, `Ver los ${pasados.length} choques ya pasados`);
    reopen.innerHTML = `<button type="button" id="conflict-reopen-btn">${label}</button>`;
    document.getElementById("conflict-reopen-btn").addEventListener("click", () => {
      conflictBannerClosed = false;
      buildConflictBanner();
    });
    return;
  }
  reopen.style.display = "none";
  reopen.innerHTML = "";

  let html = `<button type="button" class="conflict-close" aria-label="Cerrar aviso">&times;</button>`;

  if (pendientes.length > 0) {
    // Filtra la LISTA a las fechas pendientes y ajusta el recuento a ellas
    // — los choques ya pasados no desaparecen del todo (ver la rama de
    // abajo si no quedara ninguno pendiente), pero no ocupan sitio en la
    // lista de "lo que viene" mientras siga habiendo alguna pendiente.
    box.classList.remove("neutral");
    const mismoCurso = pendientes.filter(c => c.mismoCurso);
    const otroCurso = pendientes.filter(c => !c.mismoCurso);
    html += `<span class="conflict-title">⚠ ${pluralPhrase(pendientes.length,
      `1 fecha próxima con combinación de grupos incompatible`,
      `${pendientes.length} fechas próximas con combinación de grupos incompatible`)}</span>`;
    if (mismoCurso.length) {
      html += `<p>${mismoCurso.length} dentro del mismo curso — se resuelven eligiendo otro grupo:</p>` +
        `<ul>${mismoCurso.map(conflictItem).join("")}</ul>`;
    }
    if (otroCurso.length) {
      html += `<p>${otroCurso.length} entre cursos distintos — al cursar asignaturas de más de un curso a la ` +
        `vez puede que no exista ninguna combinación de grupos sin choques:</p>` +
        `<ul>${otroCurso.map(conflictItem).join("")}</ul>`;
    }
  } else {
    // Todos los choques de esta selección ya pasaron: si el aviso
    // desapareciera del todo, un alumno que abre su calendario en mayo no
    // tendría forma de saber que en septiembre eligió una combinación
    // incompatible. Se queda, pero en tono neutro — no es una alarma sobre
    // algo que vaya a pasar.
    box.classList.add("neutral");
    html += `<span class="conflict-title">${pluralPhrase(pasados.length,
      `Esta combinación de grupos tuvo 1 choque de horario, ya pasado.`,
      `Esta combinación de grupos tuvo ${pasados.length} choques de horario, todos ya pasados.`)}</span>`;
  }

  box.style.display = "block";
  box.innerHTML = html;
  box.querySelector(".conflict-close").addEventListener("click", () => {
    conflictBannerClosed = true;
    buildConflictBanner();
  });
}

// Mismo mecanismo que `conflictEndDate`/`isConflictPast` (arriba) pero
// para exámenes: sin hora de fin real, se estima con
// EXAM_NOMINAL_DURATION_MINUTES — SOLO para decidir si el aviso ya está
// "pasado", nunca se muestra esa hora de fin estimada en ningún sitio de
// la interfaz.
function examConflictEndDate(c) {
  const day = parseDate(c.date);
  const [ah, am] = c.a.start.split(":").map(Number);
  const [bh, bm] = c.b.start.split(":").map(Number);
  const aEnd = new Date(day); aEnd.setHours(ah, am, 0, 0); aEnd.setMinutes(aEnd.getMinutes() + EXAM_NOMINAL_DURATION_MINUTES);
  const bEnd = new Date(day); bEnd.setHours(bh, bm, 0, 0); bEnd.setMinutes(bEnd.getMinutes() + EXAM_NOMINAL_DURATION_MINUTES);
  return aEnd > bEnd ? aEnd : bEnd;
}
function examClassConflictEndDate(c) {
  const day = parseDate(c.date);
  const [eh, em] = c.exam.start.split(":").map(Number);
  const examEnd = new Date(day); examEnd.setHours(eh, em, 0, 0); examEnd.setMinutes(examEnd.getMinutes() + EXAM_NOMINAL_DURATION_MINUTES);
  const [ch, cm] = c.event.end.split(":").map(Number);
  const classEnd = new Date(day); classEnd.setHours(ch, cm, 0, 0);
  return examEnd > classEnd ? examEnd : classEnd;
}

function examConflictItem(c) {
  const sentence = c.certain
    ? `El ${fechaLarga(c.date)} a las ${c.a.start} coinciden los exámenes de ${c.a.name} y ${c.b.name}. Cuando se abra el plazo, solicita llamamiento especial para una de ellas.`
    : `El ${fechaLarga(c.date)} hay examen de ${c.a.name} (${c.a.start}) y de ${c.b.name} (${c.b.start}) — sin hora de fin no se puede confirmar si se solapan, revísalo cuando se abra el plazo.`;
  return `<li>${sentence}</li>`;
}
function examClassConflictItem(c) {
  return `<li>El ${fechaLarga(c.date)} tienes clase de ${c.event.acronym} ${c.event.group} (${c.event.start}–${c.event.end}) a la vez que el examen de ${c.exam.name} (${c.exam.start}, duración estimada).</li>`;
}

// Mismo patrón visual que `buildConflictBanner` (cerrar/reabrir sin
// persistir, tono neutro cuando todo lo pendiente ya pasó) pero en su
// propio banner — un choque de examen se resuelve pidiendo llamamiento
// especial, no eligiendo otro grupo, así que conviene no mezclar los dos
// avisos en una sola lista.
let examConflictBannerClosed = false;

function buildExamConflictBanner() {
  const box = document.getElementById("exam-conflict-banner");
  const reopen = document.getElementById("exam-conflict-reopen");
  const total = EXAM_CONFLICTS.length + EXAM_CLASS_CONFLICTS.length;
  if (total === 0) {
    box.style.display = "none"; box.innerHTML = "";
    reopen.style.display = "none"; reopen.innerHTML = "";
    return;
  }

  const now = new Date();
  const pendientesExamenes = EXAM_CONFLICTS.filter(c => examConflictEndDate(c) > now);
  const pasadosExamenes = EXAM_CONFLICTS.filter(c => examConflictEndDate(c) <= now);
  const pendientesClase = EXAM_CLASS_CONFLICTS.filter(c => examClassConflictEndDate(c) > now);
  const pasadosClase = EXAM_CLASS_CONFLICTS.filter(c => examClassConflictEndDate(c) <= now);
  const pendientesTotal = pendientesExamenes.length + pendientesClase.length;
  const pasadosTotal = pasadosExamenes.length + pasadosClase.length;

  if (examConflictBannerClosed) {
    box.style.display = "none"; box.innerHTML = "";
    reopen.style.display = "block";
    const label = pendientesTotal > 0
      ? pluralPhrase(pendientesTotal, `Ver el choque de examen`, `Ver los ${pendientesTotal} choques de examen`)
      : pluralPhrase(pasadosTotal, `Ver el choque de examen ya pasado`, `Ver los ${pasadosTotal} choques de examen ya pasados`);
    reopen.innerHTML = `<button type="button" id="exam-conflict-reopen-btn">${label}</button>`;
    document.getElementById("exam-conflict-reopen-btn").addEventListener("click", () => {
      examConflictBannerClosed = false;
      buildExamConflictBanner();
    });
    return;
  }
  reopen.style.display = "none"; reopen.innerHTML = "";

  let html = `<button type="button" class="conflict-close" aria-label="Cerrar aviso">&times;</button>`;

  if (pendientesTotal > 0) {
    box.classList.remove("neutral");
    html += `<span class="conflict-title">⚠ ${pluralPhrase(pendientesTotal,
      `1 choque de examen próximo`,
      `${pendientesTotal} choques de examen próximos`)}</span>`;
    if (pendientesExamenes.length) {
      html += `<ul>${pendientesExamenes.map(examConflictItem).join("")}</ul>`;
    }
    if (pendientesClase.length) {
      html += `<ul>${pendientesClase.map(examClassConflictItem).join("")}</ul>`;
    }
  } else {
    box.classList.add("neutral");
    html += `<span class="conflict-title">${pluralPhrase(pasadosTotal,
      `Esta selección tuvo 1 choque de examen, ya pasado.`,
      `Esta selección tuvo ${pasadosTotal} choques de examen, todos ya pasados.`)}</span>`;
  }

  box.style.display = "block";
  box.innerHTML = html;
  box.querySelector(".conflict-close").addEventListener("click", () => {
    examConflictBannerClosed = true;
    buildExamConflictBanner();
  });
}

function buildLegend() {
  // Incluye también asignaturas que solo tienen examen (añadido a mano,
  // sin ningún grupo de clase seleccionado): su color/patrón ya se asigna
  // igual que a cualquier otra (`extra_acronyms` en `_assign_colors`), así que la
  // leyenda debe reconocerlo igual, o el color del chip de examen no
  // tendría ninguna referencia visible.
  const seen = new Map();
  EVENTS.forEach(e => { if (!seen.has(e.acronym)) seen.set(e.acronym, e); });
  EXAMS_ON_CALENDAR.forEach(e => { if (!seen.has(e.acronym)) seen.set(e.acronym, e); });
  const legend = document.getElementById("legend");
  legend.innerHTML = "";
  [...seen.values()].sort((a,b) => a.acronym.localeCompare(b.acronym)).forEach(e => {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `<span class="swatch" style="background:${e.color}"></span>${e.acronym} — ${e.name}`;
    legend.appendChild(item);
  });
}

function dateKey(y, m, d) {
  return `${y}-${String(m+1).padStart(2,"0")}-${String(d).padStart(2,"0")}`;
}

function parseDate(iso) {
  const [y,m,d] = iso.split("-").map(Number);
  return new Date(y, m-1, d);
}

// `.toISOString()` convierte a UTC — con `new Date(y,m,d)` (medianoche LOCAL),
// en cualquier huso con offset positivo (Europe/Madrid, CEST/CET) eso cae en
// el día anterior en UTC, así que `.toISOString().slice(0,10)` desplazaba la
// fecha un día hacia atrás. Verificado el bug con Node bajo
// TZ=Europe/Madrid: `new Date(2026,9,14).toISOString()` da "2026-10-13...".
// `isoLocal` construye la clave a partir de los getters LOCALES, nunca de
// una conversión UTC, para una fecha que ya es medianoche local.
function isoLocal(date) {
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
}

// Incluye las fechas de EXAMS_ON_CALENDAR, no solo EVENTS — un examen puede
// caer en un mes/semana sin ninguna clase (típico: fuera del periodo
// lectivo), y sin esto la paginación de Mes/Semana lo saltaría en
// silencio, invisible hasta que alguien navegara ahí por casualidad. Los
// exámenes solo "disponibles" (no incluidos) no cuentan aquí — no están
// en el calendario todavía, no deben forzar una página nueva.
let months = [...new Set([...EVENTS, ...EXAMS_ON_CALENDAR].map(e => e.date.slice(0,7)))].sort();
if (months.length === 0) months = [isoLocal(new Date()).slice(0,7)];
let monthIndex = 0;

function mondayOf(date) {
  const d = new Date(date);
  const wd = (d.getDay() + 6) % 7; // 0=lunes
  d.setDate(d.getDate() - wd);
  return d;
}
let weeks = [...new Set([...EVENTS, ...EXAMS_ON_CALENDAR].map(e => isoLocal(mondayOf(parseDate(e.date)))))].sort();
if (weeks.length === 0) weeks = [isoLocal(mondayOf(new Date()))];
let weekIndex = 0;

let view = "month";

// Anillo de choque: halo + rojo, el halo con el mismo color que el texto
// del chip (`textColor`, ya calculado en Python por contraste contra ESE
// fondo concreto) — un halo fijo (blanco o negro) no basta contra los 8
// colores de la paleta a la vez. `box-shadow` no se
// puede repartir entre una regla CSS y un valor inline (el inline
// sustituye al de la clase entero), así que el anillo completo se calcula
// aquí, por evento, no en una regla `.overlap` de la hoja de estilos.
function overlapBoxShadow(e) {
  return `box-shadow: inset 0 0 0 1px ${e.textColor}, inset 0 0 0 3px var(--overlap-ring);`;
}

function renderEventChip(e) {
  const cls = ["event-chip", "pat-" + e.pattern];
  const conflict = isConflict(e);
  if (e.movedFrom) cls.push("moved");
  const title = `${e.acronym} ${e.group} ${e.start}-${e.end}${e.room ? " · Aula " + e.room : ""}${e.movedFrom ? " (trasladado desde " + e.movedFrom + ")" : ""}${conflict ? " — ⚠ choca con otro grupo de la selección, ver aviso arriba" : ""}`;
  // El "⚠" en el propio texto no depende del color de fondo para leerse,
  // a diferencia del anillo — refuerzo adicional en la vista Mes, donde el
  // chip es pequeño (en la vista Semana ya existía una insignia de texto
  // equivalente, ver más abajo).
  const label = conflict ? `⚠ ${e.start} ${e.acronym} ${e.group}` : `${e.start} ${e.acronym} ${e.group}`;
  // background-color, NUNCA el shorthand background: el shorthand
  // resetea background-image a "none" y se comería el patrón de la clase
  // .pat-* (ver comentario CSS junto a esas reglas).
  const style = `background-color:${e.color};color:${e.textColor};--pattern-overlay:${e.patternOverlay};${conflict ? overlapBoxShadow(e) : ""}`;
  return `<div class="${cls.join(' ')}" style="${style}" title="${title}" data-kind="class" data-i="${e._i}">${label}</div>`;
}

// Examen: mismo color/patrón que las clases de su asignatura, borde
// discontinuo (canal de forma, ver CSS `.event-chip.exam`) y prefijo
// "Examen:" en el propio texto — el discriminador que no depende de la
// vista de color de nadie. NUNCA un intervalo de horas, solo la hora de
// inicio (el PDF de convocatoria no trae hora de fin y este fichero no la
// inventa en ningún sitio).
function renderExamChip(exam) {
  const cls = ["event-chip", "exam", "pat-" + exam.pattern];
  const conflict = isExamConflict(exam);
  const roomText = exam.room ? " · Aula " + exam.room : " · Aula por confirmar";
  const title = `Examen: ${exam.name} (${exam.acronym}) ${exam.start}${roomText}${conflict ? " — ⚠ " + examConflictShortLabel(exam) : ""}`;
  const label = conflict ? `⚠ ${exam.start} Examen: ${exam.acronym}` : `${exam.start} Examen: ${exam.acronym}`;
  const style = `background-color:${exam.color};color:${exam.textColor};border-color:${exam.textColor};--pattern-overlay:${exam.patternOverlay};${conflict ? overlapBoxShadow(exam) : ""}`;
  return `<div class="${cls.join(' ')}" style="${style}" title="${title}" data-kind="exam" data-i="${exam._i}">${label}</div>`;
}

// Marcador compacto de un día en la vista Mes a poco ancho (ver
// `.day-markers` en el CSS) — sin texto, un círculo por clase y un cuadrado
// por examen (canal de FORMA, no de color). **Ningún marcador individual
// lleva ya anillo de choque** — con varias asignaturas chocando el mismo
// día, un anillo rojo por marcador convertía el día entero en "puntos
// rojos idénticos" y tapaba qué asignaturas había (encontrado con captura
// real de un día de 8 eventos). El choque se marca UNA vez por día, ver
// `dayHasConflict`/`.day-cell.has-conflict` más abajo — cada marcador
// conserva siempre su anillo neutro normal (`--marker-ring`, en la
// plantilla CSS). Máximo `MAX_DAY_MARKERS` visibles, el resto se resume en
// "+N" — un día con 8 eventos no cabría igual aunque cada marcador midiera
// 8px.
const MAX_DAY_MARKERS = 6;
function renderDayMarkers(dayItems, dateKey) {
  const shown = dayItems.slice(0, MAX_DAY_MARKERS);
  const extra = dayItems.length - shown.length;
  let html = `<div class="day-markers" data-day="${dateKey}">`;
  shown.forEach(({item, isExam}) => {
    const cls = "day-marker " + (isExam ? "marker-exam" : "marker-class");
    html += `<span class="${cls}" style="background-color:${item.color}"></span>`;
  });
  if (extra > 0) html += `<span class="day-marker-more">+${extra}</span>`;
  html += '</div>';
  return html;
}

// Choque del DÍA (no de un evento concreto) — cualquier evento u examen de
// este día está en CONFLICTS/EXAM_CONFLICTS/EXAM_CLASS_CONFLICTS. Solo se
// usa para el badge `.day-cell.has-conflict` de la vista Mes compacta; el
// detalle de CON QUÉ choca cada evento sigue viviendo en el chip completo
// (pantalla ancha) o en la vista Semana (a un toque, tras `goToWeekOf`).
function dayHasConflict(dayItems) {
  return dayItems.some(({item, isExam}) => isExam ? isExamConflict(item) : isConflict(item));
}

// Tocar el resumen compacto de un día salta a la vista Semana de ese día
// (la otra opción documentada era un detalle bajo la rejilla; esta reutiliza
// la vista Semana ya existente, que a este mismo ancho ya se verificó
// legible con texto completo — sin construir un segundo panel de detalle
// para lo mismo). Solo se llega aquí desde `.day-markers`, que solo existe
// en días con al menos un evento, así que su semana ya está en `weeks`.
function goToWeekOf(dateKeyStr) {
  const monday = isoLocal(mondayOf(parseDate(dateKeyStr)));
  const idx = weeks.indexOf(monday);
  if (idx === -1) return;
  weekIndex = idx;
  view = "week";
  setActiveViewButton("btn-view-week");
  togglePeriodControls(true);
  render();
}

function renderMonth() {
  const [y, m] = months[monthIndex].split("-").map(Number);
  document.getElementById("period-label").textContent = `${MES_NOMBRE[m-1]} ${y}`;
  const first = new Date(y, m-1, 1);
  const startOffset = (first.getDay() + 6) % 7; // lunes=0
  const daysInMonth = new Date(y, m, 0).getDate();

  const byDate = {};
  EVENTS.forEach(e => { (byDate[e.date] = byDate[e.date] || []).push({ item: e, chip: renderEventChip, isExam: false }); });
  EXAMS_ON_CALENDAR.forEach(e => { (byDate[e.date] = byDate[e.date] || []).push({ item: e, chip: renderExamChip, isExam: true }); });

  let html = '<div class="month-grid">';
  ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"].forEach(d => html += `<div class="weekday-label">${d}</div>`);
  for (let i = 0; i < startOffset; i++) html += '<div class="day-cell empty"></div>';
  for (let day = 1; day <= daysInMonth; day++) {
    const key = dateKey(y, m-1, day);
    const dayItems = (byDate[key] || []).slice().sort((a,b) => toMinutes(a.item.start)-toMinutes(b.item.start));
    const cellCls = dayItems.length > 0 && dayHasConflict(dayItems) ? "day-cell has-conflict" : "day-cell";
    html += `<div class="${cellCls}"><div class="day-number">${day}</div>`;
    html += '<div class="day-chips">';
    dayItems.forEach(({item, chip}) => html += chip(item));
    html += '</div>';
    if (dayItems.length > 0) html += renderDayMarkers(dayItems, key);
    html += '</div>';
  }
  document.getElementById("content").innerHTML = html || '<div class="empty-msg">Sin eventos</div>';
}

function renderWeek() {
  const monday = parseDate(weeks[weekIndex]);
  const sunday = new Date(monday); sunday.setDate(sunday.getDate()+6);
  document.getElementById("period-label").textContent =
    `${monday.getDate()} ${MES_NOMBRE[monday.getMonth()].slice(0,3)} – ${sunday.getDate()} ${MES_NOMBRE[sunday.getMonth()].slice(0,3)} ${sunday.getFullYear()}`;

  let html = '<div class="week-view">';
  let any = false;
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday); d.setDate(d.getDate()+i);
    const key = isoLocal(d);
    const dayEvents = EVENTS.filter(e => e.date === key);
    const dayExams = EXAMS_ON_CALENDAR.filter(e => e.date === key);
    const dayItems = [...dayEvents.map(e => ({item: e, isExam: false})), ...dayExams.map(e => ({item: e, isExam: true}))]
      .sort((a,b) => toMinutes(a.item.start)-toMinutes(b.item.start));
    if (dayItems.length === 0) continue;
    any = true;
    html += `<div class="week-day"><h3>${DIA_NOMBRE[i]} ${d.getDate()} ${MES_NOMBRE[d.getMonth()]}</h3>`;
    dayItems.forEach(({item: e, isExam}) => {
      if (isExam) {
        const cls = ["week-event", "exam", "pat-" + e.pattern];
        const conflict = isExamConflict(e);
        const roomText = e.room ? ' · Aula ' + e.room : ' · Aula por confirmar';
        const style = `background-color:${e.color};color:${e.textColor};border-color:${e.textColor};--pattern-overlay:${e.patternOverlay};${conflict ? overlapBoxShadow(e) : ""}`;
        html += `<div class="${cls.join(' ')}" style="${style}" data-kind="exam" data-i="${e._i}">
          <span class="time">${e.start}</span>
          <span class="info">Examen: ${e.name} (${e.acronym})${roomText}${conflict ? '<span class="moved-badge">⚠ ' + examConflictShortLabel(e) + '</span>' : ''}</span>
        </div>`;
        return;
      }
      const cls = ["week-event", "pat-" + e.pattern];
      const conflict = isConflict(e);
      const style = `background-color:${e.color};color:${e.textColor};--pattern-overlay:${e.patternOverlay};${conflict ? overlapBoxShadow(e) : ""}`;
      html += `<div class="${cls.join(' ')}" style="${style}" data-kind="class" data-i="${e._i}">
        <span class="time">${e.start}–${e.end}</span>
        <span class="info">${e.acronym} ${e.group} · ${e.type}${e.room ? ' · Aula ' + e.room : ''}${e.movedFrom ? '<span class="moved-badge">trasladado de ' + e.movedFrom + '</span>' : ''}${conflict ? '<span class="moved-badge">⚠ choca con otro grupo</span>' : ''}</span>
      </div>`;
    });
    html += '</div>';
  }
  html += '</div>';
  document.getElementById("content").innerHTML = any ? html : '<div class="empty-msg">Sin eventos esta semana</div>';
}

// El programa ya sabe con qué choca un examen concreto (mismo dato que
// `examConflictSentence` usa para la frase larga del panel de detalle) —
// una etiqueta genérica "coincide con otro examen/clase" no dice nada que
// el alumno no supiera ya con solo ver el icono; esta versión corta nombra
// al rival, para el badge de la vista Semana y el `title` del chip.
function examConflictShortLabel(exam) {
  const examOther = EXAM_CONFLICTS.find(c => c.date === exam.date && (c.a.code === exam.code || c.b.code === exam.code));
  if (examOther) {
    const rival = examOther.a.code === exam.code ? examOther.b : examOther.a;
    return `coincide con el examen de ${rival.name}`;
  }
  const classConflict = EXAM_CLASS_CONFLICTS.find(c => c.date === exam.date && c.exam.code === exam.code);
  if (classConflict) {
    const ev = classConflict.event;
    return `coincide con tu clase de ${ev.acronym} ${ev.group}`;
  }
  return "";
}

function examRowConflictNote(exam) {
  const sentence = examConflictSentence(exam);
  return sentence ? `<div class="exam-conflict-note">⚠ ${sentence}</div>` : "";
}

// Este mismo HTML sirve tanto para el .html descargable (standalone) como
// para la vista previa embebida en el asistente web (`#preview-iframe`,
// mismo origen). Solo en el segundo caso tiene sentido un botón que añada
// la convocatoria de verdad — el asistente expone la función en
// `window.horarioUcaAddExamConvocatorias` justo antes de insertar este
// HTML en el iframe; en el fichero descargado, sin asistente alrededor,
// esa función nunca existe y el botón simplemente no se pinta.
function canAddConvocatoriasFromHere() {
  try {
    return window.top !== window.self && typeof window.parent.horarioUcaAddExamConvocatorias === "function";
  } catch (e) {
    return false; // acceso a window.parent bloqueado (origen distinto) — no debería pasar aquí, pero nunca por eso romper el render
  }
}
function addConvocatoriasFromHere() {
  if (canAddConvocatoriasFromHere()) window.parent.horarioUcaAddExamConvocatorias();
}

function examRowHtml(exam, now) {
  const examDate = parseDate(exam.date);
  const [h, m] = exam.start.split(":").map(Number);
  examDate.setHours(h, m, 0, 0);
  const past = examDate <= now;
  const roomText = exam.room ? `Aula ${exam.room}` : "Aula por confirmar";
  const notIncluded = exam.included === false;
  const addBtnHtml = notIncluded && canAddConvocatoriasFromHere()
    ? ' <button type="button" class="exam-add-btn" data-action="add-convocatorias">Añadir esta convocatoria</button>'
    : "";
  const bottomNote = notIncluded
    ? `<div class="exam-availability-note">No añadido a tu calendario todavía.${addBtnHtml}</div>`
    : examRowConflictNote(exam);
  return `<div class="exam-row${past ? ' past' : ''}${notIncluded ? ' not-included' : ''}" data-kind="exam" data-i="${exam._i}">
    <span class="exam-date">${fechaLarga(exam.date)}</span>
    <span class="exam-info">
      <span class="exam-swatch" style="background-color:${exam.color};border-color:${exam.textColor}"></span>
      <strong>Examen: ${exam.name} (${exam.acronym})</strong>
    </span>
    <span class="exam-meta">${exam.start} · <span class="exam-room">${roomText}</span></span>
    ${bottomNote}
  </div>`;
}

function renderExams() {
  document.getElementById("period-label").textContent = "";
  if (EXAMS.length === 0) {
    document.getElementById("content").innerHTML = '<div class="empty-msg">No se ha añadido ningún examen a esta selección</div>';
    return;
  }
  const now = new Date();
  const sorted = EXAMS.slice().sort((a,b) => (a.date + a.start).localeCompare(b.date + b.start));

  // Con una sola convocatoria (el caso normal), lista plana de siempre —
  // agrupar solo aparece cuando de verdad hace falta distinguir varias
  // fechas del MISMO examen (una por convocatoria subida), para que no
  // parezcan duplicados o un error.
  const convocatorias = [...new Set(sorted.map(e => e.convocatoria || "Sin convocatoria"))];
  if (convocatorias.length <= 1) {
    document.getElementById("content").innerHTML = `<div class="exam-list">${sorted.map(e => examRowHtml(e, now)).join("")}</div>`;
    return;
  }

  const groups = convocatorias.map(conv => {
    const items = sorted.filter(e => (e.convocatoria || "Sin convocatoria") === conv);
    const title = conv === "Sin convocatoria" ? conv : `Convocatoria de ${conv.toLowerCase()}`;
    return `<div class="exam-group">
      <h3 class="exam-group-title">${title}</h3>
      <div class="exam-list">${items.map(e => examRowHtml(e, now)).join("")}</div>
    </div>`;
  });
  document.getElementById("content").innerHTML = groups.join("");
}

function render() {
  if (view === "month") renderMonth();
  else if (view === "week") renderWeek();
  else renderExams();
}

const VIEW_BUTTONS = ["btn-view-month", "btn-view-week", "btn-view-exams"];
function setActiveViewButton(id) {
  VIEW_BUTTONS.forEach(btnId => document.getElementById(btnId).classList.toggle("active", btnId === id));
}
// Mes/Semana usan ←/→ y la etiqueta de periodo para paginar; la pestaña
// Exámenes es una lista plana sin paginación (todos los exámenes de la
// selección a la vez, ordenados por fecha) — se esconde la fila entera
// (no solo los botones sueltos), no dejar una fila vacía sin sentido.
function togglePeriodControls(show) {
  document.getElementById("period-nav").style.display = show ? "" : "none";
}
document.getElementById("btn-view-month").addEventListener("click", () => {
  view = "month";
  setActiveViewButton("btn-view-month");
  togglePeriodControls(true);
  render();
});
document.getElementById("btn-view-week").addEventListener("click", () => {
  view = "week";
  setActiveViewButton("btn-view-week");
  togglePeriodControls(true);
  render();
});
document.getElementById("btn-view-exams").addEventListener("click", () => {
  view = "exams";
  setActiveViewButton("btn-view-exams");
  togglePeriodControls(false);
  render();
});
document.getElementById("btn-prev").addEventListener("click", () => {
  if (view === "month") monthIndex = Math.max(0, monthIndex-1);
  else weekIndex = Math.max(0, weekIndex-1);
  render();
});
document.getElementById("btn-next").addEventListener("click", () => {
  if (view === "month") monthIndex = Math.min(months.length-1, monthIndex+1);
  else weekIndex = Math.min(weeks.length-1, weekIndex+1);
  render();
});

// Sin exámenes en esta selección, la pestaña no se deja ahí vacía y sin
// explicación — se oculta del todo, no una lista con un único mensaje.
if (EXAMS.length === 0) {
  document.getElementById("btn-view-exams").hidden = true;
}

// ---------- Modo de paleta: accesible (por defecto) / vibrante ----------
// Se recuerda entre sesiones con localStorage, envuelto en try/catch: si
// falla (modo privado, cuota agotada, navegador que lo bloquea), el
// interruptor sigue funcionando en la sesión actual, solo no se recuerda
// para la próxima — nunca debe romper el render por esto.
const PALETTE_STORAGE_KEY = "horario-uca:paleta";
function loadPaletteMode() {
  try {
    return localStorage.getItem(PALETTE_STORAGE_KEY) === "vibrant" ? "vibrant" : "accessible";
  } catch (e) {
    return "accessible"; // por defecto el modo accesible — ver POR DEFECTO en el pedido original
  }
}
function savePaletteMode(mode) {
  try { localStorage.setItem(PALETTE_STORAGE_KEY, mode); } catch (e) { /* no se recuerda, pero el interruptor sigue funcionando */ }
}

// Reasigna color/texto/patrón de CADA evento y examen desde la tabla del
// modo elegido y vuelve a pintar — nunca una petición nueva al servidor
// (el `.ics` no se ve afectado en absoluto: build_ics ni sabe que este
// interruptor existe). EVENTS/EXAMS son los mismos objetos que ya lee
// showEventDetail/showExamDetail/buildLegend/render, así que mutarlos in
// situ basta para que TODO lo que ya pinta esos datos se actualice solo,
// sin duplicar la lista de eventos por modo.
function applyPalette(mode) {
  const table = mode === "vibrant" ? PALETTE_VIBRANT : PALETTE_ACCESSIBLE;
  const paint = (item) => {
    const p = table[item.acronym];
    if (!p) return;
    item.color = p.color; item.textColor = p.textColor; item.pattern = p.pattern; item.patternOverlay = p.patternOverlay;
  };
  EVENTS.forEach(paint);
  EXAMS.forEach(paint);
  buildLegend();
  render();
}

let paletteMode = loadPaletteMode();
document.getElementById("palette-toggle-input").checked = paletteMode === "accessible";
document.getElementById("palette-toggle-input").addEventListener("change", (evt) => {
  paletteMode = evt.target.checked ? "accessible" : "vibrant";
  savePaletteMode(paletteMode);
  applyPalette(paletteMode);
});

applyPalette(paletteMode);
buildConflictBanner();
buildExamConflictBanner();
</script>
</body>
</html>
"""


def _escape_html_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html(
    events: list[CalendarEvent],
    *,
    legend: list[SubjectLegendEntry] | dict[str, tuple[int, int, int]] | None = None,
    title: str = "Horario ESI (UCA)",
    exams: list[ExamEntry] | None = None,
    exams_available: list[ExamEntry] | None = None,
) -> str:
    """`exams_available` (opcional): exámenes de convocatorias relevantes
    para la selección pero no incluidas en el calendario — ver
    `_exams_to_json`. Nunca alimenta conflictos ni `.ics` (ver `build_ics`,
    que solo recibe `exams`), solo la vista de catálogo de la pestaña
    Exámenes."""
    exams = exams or []
    exams_available = exams_available or []
    all_acronyms = [e.acronym for e in exams] + [e.acronym for e in exams_available]

    # Dos paletas completas, calculadas las dos siempre — el cambio de modo
    # en el cliente (interruptor "Colores accesibles") es una relectura de
    # tabla, nunca una petición nueva al servidor (ver `applyPalette` en la
    # plantilla JS). El modo accesible es el que se hornea por defecto en
    # `events`/`exams` (ver "POR DEFECTO: el modo accesible" — quien no
    # sepa que existe el interruptor debe encontrarse un calendario
    # utilizable, no al revés).
    colors_accessible = _assign_colors(events, legend, extra_acronyms=all_acronyms)
    colors_vibrant = _assign_colors(
        events, legend, extra_acronyms=all_acronyms,
        palette=_FALLBACK_PALETTE_VIBRANT, avoid_hue_collisions=True,
    )

    events_json = _events_to_json(events, colors_accessible)
    exams_json = _exams_to_json(exams, colors_accessible, exams_available=exams_available)
    conflicts_json = _conflicts_to_json(find_conflicts(events))
    exam_conflicts_json = _exam_conflicts_to_json(find_exam_conflicts(exams))
    exam_class_conflicts_json = _exam_class_conflicts_to_json(find_exam_class_conflicts(exams, events))
    palette_accessible_json = json.dumps(_palette_table(colors_accessible, use_patterns=True), ensure_ascii=False)
    palette_vibrant_json = json.dumps(_palette_table(colors_vibrant, use_patterns=False), ensure_ascii=False)
    # Sustitución por marcador único, no `string.Template`: la plantilla usa
    # `${...}` en decenas de template literals de JS, que colisiona con la
    # sintaxis de sustitución `$identificador`/`${identificador}` de
    # `string.Template` (verificado: lanza ValueError al montar la plantilla).
    html = _TEMPLATE.replace("__TITLE__", _escape_html_text(title))
    html = html.replace("__EVENTS_JSON__", events_json)
    html = html.replace("__EXAMS_JSON__", exams_json)
    html = html.replace("__CONFLICTS_JSON__", conflicts_json)
    html = html.replace("__EXAM_CONFLICTS_JSON__", exam_conflicts_json)
    html = html.replace("__EXAM_CLASS_CONFLICTS_JSON__", exam_class_conflicts_json)
    html = html.replace("__PALETTE_ACCESSIBLE_JSON__", palette_accessible_json)
    html = html.replace("__PALETTE_VIBRANT_JSON__", palette_vibrant_json)
    html = html.replace("__EXAM_NOMINAL_DURATION__", str(EXAM_NOMINAL_DURATION_MINUTES))
    html = html.replace("__OVERLAP_RING__", _OVERLAP_RING_HEX)
    html = html.replace("__MARKER_RING_LIGHT__", _MARKER_RING_LIGHT_HEX)
    html = html.replace("__MARKER_RING_DARK__", _MARKER_RING_DARK_HEX)
    return html

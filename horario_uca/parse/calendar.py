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

"""Minicalendario académico: estado lectivo/no lectivo por semana y día.

La semana se lee del número azul de cada fila (dentro del bloque de mes al
que corresponde); el día y su mes/año permiten calcular la fecha real y, con
ella, el día de la semana — así no hace falta ninguna suposición sobre en qué
columna cae cada día de la semana dentro de un bloque de mes, solo aritmética
de fechas sobre el número de día + mes + año ya leídos del PDF.

Los bloques de mes NO se delimitan por la posición de la palabra del mes (se
comprobó que el offset entre la cabecera y su propia columna de días varía de
~2pt a ~17pt según el mes, y que un punto medio entre cabeceras corta a veces
la última celda de un mes). En su lugar, los números de día de cada fila se
agrupan por hueco horizontal (~4.4pt entre celdas de un mismo mes, >20pt entre
meses — igual que la agrupación de columnas de fondo en grid.py) y cada grupo
resultante se asigna a la cabecera de mes más cercana.

**Segundo layout de minicalendario, encontrado al depurar páginas fuera de la
0**: en páginas donde el minicalendario cae en una franja vertical estrecha
(p.ej. p.2, p.21), los 5 meses se apilan uno debajo de otro (misma X, Y muy
distinta) en vez de ponerse en fila (misma Y, X distinta, como en p.0).
Asignar por "cabecera más cercana en X" es una decisión sin sentido cuando
las 5 cabeceras comparten casi la misma X — verificado que producía fechas
completamente disparatadas (semanas repitiendo días de septiembre en
noviembre, etc.). El layout se detecta comparando el rango de X de las
cabeceras contra su rango de Y en esa página: si X varía más, están en fila
(nearest-X); si Y varía más, están apiladas (nearest-Y, cabecera con mayor
`y1` que siga estando por encima de la fila).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from horario_uca.extract import RawPage, RawSpan
from horario_uca.model import CalendarWeek, DayLectivoStatus, DayStatus, ParseInfo, ParseWarning

MONTH_NUM = {
    "Enero": 1, "Febrero": 2, "Marzo": 3, "Abril": 4, "Mayo": 5, "Junio": 6,
    "Julio": 7, "Agosto": 8, "Septiembre": 9, "Octubre": 10, "Noviembre": 11,
    "Diciembre": 12,
}

WEEK_MARKER_COLOR = (0, 0, 255)
# Igual que en session.py: el tamaño de letra del minicalendario escala con
# la página (verificado: la cabecera de mes va de 4.328pt a 4.685pt en las
# páginas comprobadas, y probablemente más en otras). DAY_SIZE y
# WEEK_MARKER_SIZE se derivan como ratio sobre el tamaño de la cabecera de
# mes de CADA página (ver `_size_bands()`), nunca como puntos absolutos — se
# comprobó el bug real: en la página 1 el marcador de semana mide 3.64pt,
# fuera del rango fijo `(3.2, 3.6)` calibrado solo contra la página 0, así
# que NINGÚN marcador se detectaba y el calendario entero salía vacío
# (0 `CalendarWeek`) sin ningún error visible.
_REF_HEADER_SIZE = 4.513  # tamaño de la cabecera de mes en la página 0
_DAY_SIZE_RATIO = (4.0 / _REF_HEADER_SIZE, 4.7 / _REF_HEADER_SIZE)
_WEEK_MARKER_SIZE_RATIO = (3.2 / _REF_HEADER_SIZE, 3.6 / _REF_HEADER_SIZE)
# La distancia vertical real entre el y0 de una fila de días y el y0 de SU
# PROPIO marcador de semana (línea base de fuentes distintas, no están
# perfectamente alineadas) escala con la tipografía de la página igual que
# todo lo demás en este módulo — verificado: 0.73-0.94pt en las páginas de
# referencia (header ~4.5-5.1pt, ratio ~0.16-0.19) y 1.5-1.7pt en una página
# de otro grado con tipografía ~1.8x más grande (header ~9.15pt, MISMO ratio
# ~0.17-0.19). Un umbral fijo en puntos (`<= 1.5`, la versión anterior de
# esta constante) es exactamente el bug que este módulo ya arregló una vez
# para DAY_SIZE/WEEK_MARKER_SIZE: pasaba por los pelos en GII y fallaba para
# TODAS las filas de una página con tipografía más grande, dejando 0
# CalendarWeek sin ningún error. Con margen de ~2x sobre
# el ratio real observado, y muy por debajo de la separación entre filas
# consecutivas (no se confunde nunca con el marcador de la fila vecina).
_MARKER_ROW_Y_TOLERANCE_RATIO = 0.4
COLOR_STATUS = {
    (0, 0, 0): DayLectivoStatus.LECTIVO,
    (255, 0, 0): DayLectivoStatus.NO_LECTIVO,
    (191, 191, 191): DayLectivoStatus.FUERA_DE_PERIODO,
}
IN_PERIOD_COLORS = {(0, 0, 0), (255, 0, 0)}
# El hueco horizontal real entre dos dígitos de día consecutivos DEL MISMO
# mes escala con la tipografía de la página (verificado: ~4.4pt a
# header_size~4.5-5.1pt en GII, ratio ~0.86-0.98; ~9pt a header_size~9.15pt
# en una página de otro grado, MISMO ratio ~0.98) — el mismo patrón que
# `_MARKER_ROW_Y_TOLERANCE_RATIO` más arriba, con margen ~1.5x sobre el
# ratio real máximo observado. Un valor fijo en puntos (`CLUSTER_GAP = 12.0`,
# la versión anterior) es la MISMA clase de bug que las dos constantes de
# arriba: a tipografía suficientemente grande, el hueco real entre dígitos
# supera el umbral fijo y cada dígito de día se convierte en su propio
# cluster de un solo número en vez de agruparse en la fila de semana
# completa — no llegó a fallar contra ninguna página comprobada (el margen
# real más ajustado medido fue ~33% en la página de mayor tipografía vista),
# pero es el mismo patrón sin convertir, no una hipótesis. Se mantiene muy
# por debajo del hueco real entre MESES (>20pt siempre, verificado en las
# páginas comprobadas) para no fusionar nunca columnas de dos meses
# distintos en un mismo cluster.
_CLUSTER_GAP_RATIO = 1.5
# Offset normal cabecera de semana -> primer día de su fila: ~13-15pt. Pero
# cuando una fila es un fragmento de un solo día que cae al FINAL de la
# semana (p.ej. una fila que solo contiene un domingo, primer día de un mes),
# el marcador sigue anclado donde empezaría el lunes de esa fila — offset
# observado hasta 53.3pt (p.14, layout apilado) y 47.5pt (p.0, layout en
# fila). En layout horizontal el límite SÍ debe ser ajustado porque los 5
# meses comparten fila y hay que evitar coger el marcador del mes vecino
# (columnas de marcador separadas ~59-61pt); en layout apilado los meses NO
# comparten fila (cada uno tiene su propio rango de Y, ya filtrado por la
# tolerancia de Y de más abajo), así que ahí no hace falta ese límite tan
# ajustado — se usa uno mucho más generoso.
MARKER_MAX_GAP_HORIZONTAL = 50.0
MARKER_MAX_GAP_VERTICAL = 100.0


def _year_for_month(academic_year: str, month_num: int) -> int:
    y1, y2 = (int(p) for p in academic_year.split("-"))
    return y1 if month_num >= 8 else y2


def _cluster_by_gap(spans: list[RawSpan], gap: float) -> list[list[RawSpan]]:
    spans = sorted(spans, key=lambda s: s.bbox[0])
    clusters: list[list[RawSpan]] = [[spans[0]]]
    for s in spans[1:]:
        if s.bbox[0] - clusters[-1][-1].bbox[2] < gap:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    return clusters


def parse_calendar_day_status(
    raw: RawPage, academic_year: str
) -> tuple[dict[tuple[int, int], DayStatus], list[ParseWarning], list[ParseInfo]]:
    """Devuelve {(week_number, weekday 0-6): DayStatus(fecha, estado)}."""
    warnings: list[ParseWarning] = []
    infos: list[ParseInfo] = []

    raw_headers = [
        (MONTH_NUM[s.text.strip()], s.bbox[0], s.bbox[3], s.size)
        for s in raw.spans
        if s.text.strip() in MONTH_NUM
    ]
    if not raw_headers:
        return {}, warnings, infos

    header_size = sum(h[3] for h in raw_headers) / len(raw_headers)
    DAY_SIZE = (_DAY_SIZE_RATIO[0] * header_size, _DAY_SIZE_RATIO[1] * header_size)
    WEEK_MARKER_SIZE = (
        _WEEK_MARKER_SIZE_RATIO[0] * header_size,
        _WEEK_MARKER_SIZE_RATIO[1] * header_size,
    )
    marker_row_y_tolerance = _MARKER_ROW_Y_TOLERANCE_RATIO * header_size
    cluster_gap = _CLUSTER_GAP_RATIO * header_size

    x_spread = max(h[1] for h in raw_headers) - min(h[1] for h in raw_headers)
    y_spread = max(h[2] for h in raw_headers) - min(h[2] for h in raw_headers)
    horizontal_layout = x_spread >= y_spread
    marker_max_gap = MARKER_MAX_GAP_HORIZONTAL if horizontal_layout else MARKER_MAX_GAP_VERTICAL
    # Orden cronológico de los meses según el eje que realmente los separa en
    # esta página (X en layout horizontal, Y en layout apilado) — mantiene
    # con sentido el retroceso "mes anterior" del bloque de reparación de más
    # abajo en ambos layouts.
    month_headers = sorted(
        ((m, x0, y1) for m, x0, y1, _ in raw_headers), key=lambda h: h[1] if horizontal_layout else h[2]
    )
    min_header_y1 = min(y1 for _, _, y1 in month_headers)

    def _header_index_for(cluster_x0: float, row_y: float) -> int:
        if horizontal_layout:
            return min(range(len(month_headers)), key=lambda hi: abs(month_headers[hi][1] - cluster_x0))
        # Apilados: la cabecera responsable de una fila es la más cercana por
        # encima de ella (mayor y1 que siga siendo <= row_y); si ninguna
        # cabecera está por encima (no debería pasar), se usa la primera.
        above = [hi for hi in range(len(month_headers)) if month_headers[hi][2] <= row_y + 1]
        if not above:
            return 0
        return max(above, key=lambda hi: month_headers[hi][2])

    # Un día de mes nunca tiene más de 2 dígitos — sin este límite, en
    # páginas con letra grande la marca de agua de generación del PDF
    # ("20260915160600", tamaño ~7pt) cae dentro de la banda ensanchada de
    # DAY_SIZE y provoca un OverflowError al construir `date()` con un "día"
    # de 14 cifras. Visto en páginas 10 y 20.
    day_spans = [
        s
        for s in raw.spans
        if s.bbox[1] > min_header_y1
        and s.text.strip().isdigit()
        and len(s.text.strip()) <= 2
        and DAY_SIZE[0] <= s.size <= DAY_SIZE[1]
    ]
    marker_spans = [
        s
        for s in raw.spans
        if s.bbox[1] > min_header_y1
        and s.text.strip().isdigit()
        and len(s.text.strip()) <= 2
        and WEEK_MARKER_SIZE[0] <= s.size <= WEEK_MARKER_SIZE[1]
        and s.color == WEEK_MARKER_COLOR
    ]

    rows: dict[float, list[RawSpan]] = defaultdict(list)
    for s in day_spans:
        rows[round(s.bbox[1], 1)].append(s)

    result: dict[tuple[int, int], DayStatus] = {}

    def _header_distance(hi: int, cluster_x0: float, row_y: float) -> float:
        if horizontal_layout:
            return abs(month_headers[hi][1] - cluster_x0)
        return abs(row_y - month_headers[hi][2])

    for row_y, spans_in_row in rows.items():
        clusters = _cluster_by_gap(spans_in_row, cluster_gap)
        cluster_x0s = [c[0].bbox[0] for c in clusters]
        header_idx = [_header_index_for(cx0, row_y) for cx0 in cluster_x0s]

        # Reparación por duplicado: un mismo (mes, día) no puede aparecer dos
        # veces de verdad en el calendario. Si el vecino más cercano por
        # distancia produce ese choque, es la señal de que el cluster peor
        # anclado (mayor distancia a su cabecera) se atribuyó al mes
        # equivocado — se comprobó con datos reales: el "1" suelto de
        # Noviembre (domingo, lejos de su propia cabecera "Noviembre" y más
        # cerca en X de "Diciembre") duplicaba el 1 de diciembre y dejaba el
        # 1 de noviembre sin ningún cluster. Se retrocede al mes anterior del
        # cluster con mayor distancia hasta que el choque desaparece.
        # Además de choque por duplicado, un mes mal atribuido puede producir
        # directamente un día inválido para ese mes (p.ej. día 31 asignado a
        # un mes de 30) — visto en páginas fuera de la 0. Se trata igual que
        # el duplicado: retroceder al mes anterior.
        def _invalid_for_month(day: int, month_num: int) -> bool:
            try:
                date(_year_for_month(academic_year, month_num), month_num, day)
            except (ValueError, OverflowError):
                return True
            return False

        seen_day_month: dict[tuple[int, int], int] = {}
        for i in sorted(range(len(clusters)), key=lambda i: _header_distance(header_idx[i], cluster_x0s[i], row_y)):
            for s in clusters[i]:
                day = int(s.text.strip())
                key = (header_idx[i], day)
                while header_idx[i] > 0 and (
                    _invalid_for_month(day, month_headers[header_idx[i]][0])
                    or (key in seen_day_month and seen_day_month[key] != i)
                ):
                    header_idx[i] -= 1
                    key = (header_idx[i], day)
                seen_day_month.setdefault(key, i)

        cluster_months = [month_headers[hi][0] for hi in header_idx]
        cluster_dates: list[list[date]] = []
        for cluster, m in zip(clusters, cluster_months):
            dates_in_cluster = []
            for s in cluster:
                if not s.text.strip().isdigit():
                    continue
                day = int(s.text.strip())
                if _invalid_for_month(day, m):
                    warnings.append(
                        ParseWarning(
                            code="dia_invalido_para_mes",
                            message=f"día {day} no existe en mes={m} (bbox={s.bbox})",
                            page_index=raw.page_index,
                            bbox=s.bbox,
                        )
                    )
                    continue
                dates_in_cluster.append(date(_year_for_month(academic_year, m), m, day))
            cluster_dates.append(sorted(dates_in_cluster))
        week_numbers: list[int | None] = []
        for cluster in clusters:
            cluster_x0 = cluster[0].bbox[0]
            candidates = [
                m
                for m in marker_spans
                if abs(m.bbox[1] - row_y) <= marker_row_y_tolerance and 0 < cluster_x0 - m.bbox[0] < marker_max_gap
            ]
            marker = min(candidates, key=lambda m: cluster_x0 - m.bbox[0], default=None)
            week_numbers.append(int(marker.text.strip()) if marker else None)

        # Un fragmento de días en el borde de un mes puede no tener su
        # propio marcador porque el marcador real está pegado al cluster
        # DOMINANTE de esa semana partida, en el mes vecino de la misma fila
        # (una semana que empieza en un mes y termina en el siguiente).
        # Todos los bloques de mes de la página comparten la misma rejilla
        # de filas, así que
        # "misma fila" NO basta para decidir que dos clusters pertenecen a
        # la misma semana partida (probado: producía falsos positivos entre
        # Diciembre y Enero en filas no relacionadas). El criterio correcto,
        # sin ambigüedad, es la CONTIGÜIDAD DE FECHAS: el cluster resuelto
        # cuya fecha máxima es exactamente el día anterior a la fecha mínima
        # de este fragmento (o viceversa).
        for i, week_number in enumerate(week_numbers):
            if week_number is not None or not cluster_dates[i]:
                continue
            i_min, i_max = cluster_dates[i][0], cluster_dates[i][-1]
            for j in range(len(clusters)):
                if week_numbers[j] is None or not cluster_dates[j]:
                    continue
                j_min, j_max = cluster_dates[j][0], cluster_dates[j][-1]
                if j_max + timedelta(days=1) == i_min or i_max + timedelta(days=1) == j_min:
                    week_numbers[i] = week_numbers[j]
                    break

        for cluster, week_number, month_num in zip(clusters, week_numbers, cluster_months):
            cluster_x0 = cluster[0].bbox[0]
            year = _year_for_month(academic_year, month_num)

            if week_number is None:
                cluster_colors = {s.color for s in cluster}
                in_period = bool(cluster_colors & IN_PERIOD_COLORS)
                if in_period:
                    # Íntegramente NO_LECTIVO (verificado en las 24 páginas:
                    # 34/34 casos así, 0 mixtos o con algún día LECTIVO) es
                    # el resultado esperado de un tramo de vacaciones sin
                    # semana numerada — no es una anomalía, es ParseInfo. Si
                    # el tramo tuviera algún día LECTIVO, sí sería una fecha
                    # real perdida y debe seguir siendo ParseWarning.
                    entry = (
                        ParseInfo
                        if cluster_colors == {(255, 0, 0)}
                        else ParseWarning
                    )(
                        code="fila_calendario_sin_semana",
                        message=f"mes={month_num}, y={row_y}, x0={cluster_x0:.1f}: no se encontró número de semana",
                        page_index=raw.page_index,
                    )
                    (infos if isinstance(entry, ParseInfo) else warnings).append(entry)
                continue

            for s in cluster:
                day = int(s.text.strip())
                if _invalid_for_month(day, month_num):
                    continue  # ya registrado como dia_invalido_para_mes arriba
                status = COLOR_STATUS.get(s.color)
                if status is None:
                    warnings.append(
                        ParseWarning(
                            code="color_dia_calendario_desconocido",
                            message=f"día {day}/{month_num}/{year}: color {s.color} no reconocido",
                            page_index=raw.page_index,
                            bbox=s.bbox,
                        )
                    )
                    continue
                day_date = date(year, month_num, day)
                weekday = day_date.weekday()
                key = (week_number, weekday)
                if key in result and result[key].status != status:
                    warnings.append(
                        ParseWarning(
                            code="calendario_semana_dia_inconsistente",
                            message=f"semana {week_number}, weekday {weekday}: {result[key].status} vs {status} (día {day}/{month_num})",
                            page_index=raw.page_index,
                        )
                    )
                result[key] = DayStatus(date=day_date.isoformat(), status=status)

    return result, warnings, infos


def build_calendar_weeks(day_status: dict[tuple[int, int], DayStatus]) -> list[CalendarWeek]:
    by_week: dict[int, dict[int, DayStatus]] = defaultdict(dict)
    for (week_number, weekday), status in day_status.items():
        by_week[week_number][weekday] = status

    weeks: list[CalendarWeek] = []
    for week_number in sorted(by_week):
        days = [by_week[week_number][wd] for wd in sorted(by_week[week_number])]
        weeks.append(CalendarWeek(week_number=week_number, days=days))
    return weeks

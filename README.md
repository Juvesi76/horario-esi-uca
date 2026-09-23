# Horario ESI (UCA)

Nombre técnico del repositorio: `horario-esi-uca` (GitHub no admite
espacios ni paréntesis en el nombre de un repositorio).

Sube el PDF de horarios que publica tu Escuela, elige tus asignaturas y
grupos, y descarga un calendario con fechas reales que puedes abrir en el
navegador o importar en Google Calendar/Apple Calendar/Outlook.

> **Esta herramienta NO es oficial de la UCA ni de la ESI.** Es un proyecto
> personal, sin relación con la universidad. Ante cualquier duda sobre tu
> horario real, consulta siempre el que publica la Escuela Superior de
> Ingeniería — este calendario es una comodidad, no una fuente autorizada.

Convierte los horarios en PDF de la Escuela Superior de Ingeniería (Universidad
de Cádiz) en un calendario HTML autocontenido con fechas reales (vista mensual
y semanal, color por asignatura tomado de la leyenda del propio PDF, solapes
de horario resaltados y explicados) y en un fichero `.ics` conforme a
RFC 5545 (con `VTIMEZONE` de Europe/Madrid y `UID` estables entre
regeneraciones). También lee el calendario de exámenes oficial (PDF aparte)
y marca los choques entre exámenes o entre un examen y una clase.

## De dónde descargar los PDFs

Este repositorio **no incluye ningún PDF de la UCA** — son documentos con
derechos de la universidad, no del proyecto; incluirlos aquí sería
redistribuirlos. Descarga el horario de tu grado (y, si quieres, el
calendario de exámenes de tu convocatoria) desde la web de horarios de tu
Escuela y guárdalo en `data/` con el nombre que prefieras — los ejemplos
de este README usan `data/GII_horario2627.pdf` porque es el que se usó
para desarrollar y probar el proyecto (Grado en Ingeniería Informática,
curso 2026-2027), pero la herramienta funciona con el PDF de cualquier
grado que siga el mismo formato.

`scripts/descargar_pdfs.py` es una plantilla opcional para automatizar esa
descarga — **está vacía a propósito, no rota**: el diccionario `URLS` no
trae ninguna entrada porque este proyecto nunca inventa URLs. Si la
ejecutas tal cual, imprime un aviso y no
descarga nada — es el comportamiento esperado, no un error. Para que
funcione, ábrela y añade una entrada por cada PDF que quieras:

```python
URLS: dict[str, str] = {
    "GII_horario2627.pdf": "https://...",              # el horario de tu grado
    "GII.calendarioExamenes.Feb27.pdf": "https://...",  # opcional, el calendario de exámenes
}
```

La clave es el nombre exacto con el que quedará en `data/` (usa los mismos
nombres que ya usan los tests si quieres correr la suite completa contra
ellos — ver la lista de fixtures en `tests/conftest.py` y en los propios
`tests/test_*.py`); el valor, la URL real de la web de horarios de tu
Escuela — confirma antes que sea estable (que no cambie de curso a curso
ni dependa de una sesión iniciada) antes de depender de ella en un script.

Sin ningún PDF en `data/`, la suite de tests se salta los que dependen de
uno (mensaje claro, no un fallo — ver `tests/conftest.py`) y sigue siendo
útil para trabajar en el código; con el PDF de referencia presente, corre
completa.

## Instalación

Requiere Python ≥ 3.11.

```bash
pip install -e ".[dev]"
```

Esto instala las dependencias de la librería (`pymupdf`, `pydantic`,
`icalendar`, `pyyaml`) y de test (`pytest`), y registra el comando `horario`
en el `PATH` del entorno virtual activo. Sin el extra `[dev]` solo se
instalan las dependencias de librería — suficiente para usar la CLI, no para
correr los tests.

## Uso

### 1. Averiguar qué hay en el PDF: `horario listar`

Sin filtros, lista los 24 combos (curso, semestre, itinerario) del PDF de
referencia:

```bash
horario listar data/GII_horario2627.pdf
```

Con `--curso` se ve el detalle de asignaturas y grupos disponibles (letra,
tipo de grupo y códigos concretos) — imprescindible para saber qué grupos
existen antes de construir una selección:

```bash
horario listar data/GII_horario2627.pdf --curso "4º"
```

Si ese curso tiene itinerarios, `listar` avisa explícitamente y pide
`--itinerario` para no mezclar asignaturas de itinerarios distintos:

```bash
horario listar data/GII_horario2627.pdf --curso "4º" --itinerario "Itinerario de Ingeniería del Software"
```

### 2. Generar el calendario: `horario generar`

Sintaxis de selección por línea de comandos, repetible (una por
asignatura):

```
ACRONIMO:CURSO:GRUPOS
ACRONIMO:CURSO:ITINERARIO:GRUPOS   (solo hace falta en 3º-2º sem. y 4º, cuando
                                     el mismo acrónimo podría existir en más
                                     de un itinerario del mismo curso)
```

`GRUPOS` es una lista separada por comas (`A1,B1`). Ejemplo real (1ºA,
verificado evento a evento contra el PDF):

```bash
horario generar data/GII_horario2627.pdf \
  "MD:1ºA:A1,B1" \
  "CAL:1ºA:A1,B3,C1" \
  --titulo "Horario 1ºA" \
  --salida out/horario_1A
```

Genera `out/horario_1A.html` y `out/horario_1A.ics`, y antes imprime por
pantalla un **informe de confirmación**: la selección, cualquier aviso de
validación (grupo/asignatura inexistente, itinerario ambiguo, letras con
docencia real sin elegir), **los conflictos de horario dentro de la propia
selección** (dos grupos elegidos que el PDF programa a la vez — no es un
fallo del calendario, es una combinación de grupos incompatible; se avisa
aquí y también, con el mismo cálculo, dentro del HTML generado), el resumen
de eventos y los avisos de las páginas de origen. Un error de validación
(asignatura o grupo inexistente, itinerario ambiguo) **no genera ningún
fichero** y termina con código de salida 1; un conflicto de horario sí deja
generar (es información, no un error de la selección) pero queda escrito con
todo detalle en el informe y en el propio calendario.

Ejemplo con itinerario explícito, 4º curso (más irregular: bloques con
sesiones de hasta 4h):

```bash
horario generar data/GII_horario2627.pdf \
  "CS:4º:Itinerario de Ingeniería del Software:A1,B1,C1" \
  "DGPS:4º:Itinerario de Ingeniería del Software:A1,B1,C1" \
  "ES:4º:Itinerario de Ingeniería del Software:A1,B1,C1" \
  "MPS:4º:Itinerario de Ingeniería del Software:A1,B1,C1" \
  --titulo "Horario 4º Ingeniería del Software · Semestre 1" \
  --salida out/horario_4A_ISW_sem1
```

`--verbose` añade al informe los `ParseInfo` (comprobaciones ejecutadas con
el resultado esperado, ya verificadas como benignas), útiles para depurar
pero no necesarios en el uso normal.

### 3. Alternativa: selección en YAML con `--config`

Para no repetir la sintaxis por línea de comandos cada vez, o para guardar
una selección bajo control de versiones:

```yaml
# seleccion.yaml
titulo: "Horario MD + CAL · 1ºA"
salida: out/horario_1A
selecciones:
  - acronimo: MD
    curso: 1ºA
    grupos: [A1, B1]
  - acronimo: CAL
    curso: 1ºA
    grupos: [A1, B3, C1]
  # 'itinerario' es opcional, igual que en la sintaxis de línea de comandos:
  # - acronimo: CS
  #   curso: 4º
  #   itinerario: Itinerario de Ingeniería del Software
  #   grupos: [A1, B1, C1]
```

```bash
horario generar data/GII_horario2627.pdf --config seleccion.yaml
```

`--titulo`/`--salida` en línea de comandos, si se dan, tienen prioridad sobre
los del YAML.

### 4. Depuración geométrica: `horario debug`

Genera un PNG de una página del PDF con overlays: los `ClassBlock`
detectados en verde, los bloques sin interpretar (`unparsed`) en rojo con el
motivo, y las columnas de día detectadas como líneas discontinuas azules —
la herramienta de referencia para diagnosticar un fallo de extracción sin
tener que releer coordenadas en crudo.

```bash
horario debug data/GII_horario2627.pdf --page 0
```

`--page` es el índice 0-based del PDF (página 0 = 1ºA, semestre 1). El PNG
se guarda en `out/debug_page_index_N.png` salvo que se indique `--out`.

## Interfaz web

Para alguien que no quiera tocar una terminal: sube el PDF, elige curso y
grupos de listas (nunca tecleando un acrónimo) y descarga el calendario.
Móvil primero. Sin cuentas, sin base de datos: tu selección de asignaturas
nunca se guarda; el PDF ya parseado sí se cachea en memoria del servidor un
rato (nunca a disco) para que el segundo alumno de tu grado no tenga que
esperar lo mismo que el primero.

En local:

```bash
pip install -e ".[web]"
uvicorn horario_uca.web.app:app --reload
```

Abre `http://127.0.0.1:8000/` en el navegador (o desde el móvil, si está en
la misma red). `http://127.0.0.1:8000/docs` sigue disponible para probar la
API directamente (Swagger), pero no es la interfaz pensada para un alumno —
es la página de arriba.

## Desplegar en Render (plan gratuito)

1. Crea una cuenta en [render.com](https://render.com) (puedes entrar con
   tu cuenta de GitHub).
2. Sube este repositorio a GitHub (ver más abajo si no lo has hecho nunca).
3. En el panel de Render: **New +** → **Blueprint**, y elige el
   repositorio — Render lee `render.yaml` (ya incluido) y configura solo
   el servicio: imagen Docker (`Dockerfile`, también incluido), plan
   gratuito, comprobación de salud en `/api/salud`.
4. Pulsa **Apply**/**Create**. El primer arranque tarda unos minutos
   (construir la imagen); una vez desplegado, Render te da una URL
   `https://<nombre>.onrender.com`.
5. El plan gratuito **duerme el servicio tras ~15 minutos sin peticiones**
   y tarda cerca de un minuto en despertar en la siguiente visita — la
   propia interfaz avisa de esto en el paso 1 si la subida tarda.

Para ver los logs y comprobar que el despliegue fue bien: en el panel de
Render, entra en el servicio → pestaña **Logs** (arranque, peticiones,
cualquier error) y pestaña **Events** (cada despliegue, si tuvo éxito o
falló). Si el servicio no arranca, mira ahí primero — casi siempre dice la
línea exacta que falló.

## Tests

```bash
pytest
```

137 tests en verde (`tests/test_*.py`) con los PDFs de referencia presentes
en `data/` (30 pasan igualmente sin ellos, el resto se salta con un mensaje
claro — ver "De dónde descargar los PDFs" arriba), verificado en esta misma
sesión ejecutando la suite con `data/` vacío y con `data/` completo. La
mayoría corre contra `data/GII_horario2627.pdf` como fixture real — no hay
tests basados en datos inventados salvo los pocos casos marcados
explícitamente como "sintético" en su nombre (p.ej. un caso de itinerario
ambiguo, que no existe de verdad en este PDF, o el caso de fusión
propio+trasladado, que tampoco existe). Cubren:

- `test_fixture.py`, `test_session.py`: el fixture de regresión IG/IP·A1 de
  la página 0 (emparejamiento cabecera↔rectángulo, columna→día, semanas
  disjuntas en horas solapadas).
- `test_completeness.py`: las expectativas mínimas por módulo (`calendario`,
  `notas`, `leyenda`, `bloques`) en las 24 páginas — una colección vacía no
  es éxito por sí sola, cada módulo declara qué mínimo espera.
- `test_dates.py`: Fase 3 completa — los 8 casos frontera de traslados, los
  dos casos reales que discriminan semana de origen vs. destino en páginas
  de familias distintas (`IP·B2` en p.2, `MPS·A1` en p.16, itinerario de 4º),
  el recuento de control (5475 eventos en 24 páginas), la validación de que
  todo evento cae en un día lectivo, y la evidencia directa (no agregada) de
  que la fusión propio+trasladado no ocurre: 0 bloques de martes con semana
  15 activa página a página, y el listado completo del 25/05/2027 en las 24
  páginas (38 eventos, el 100% trasladados).
- `test_select.py`: Fase 4 — validación accionable de selección (asignatura/
  grupo inexistente, itinerario ambiguo, letra sin elegir).
- `test_conflicts.py`: detección de conflictos de horario dentro de una
  selección, con el caso real `IG·C1` + `CAL·C1` (6 fechas, una trasladada).
- `test_cli.py`: los tres subcomandos de la CLI ejecutados de verdad
  (`generar`, `listar`, `debug` vía las funciones que invoca), incluida la
  ruta `--config` YAML y los códigos de salida de error.

## Si la ESI cambia el formato del PDF

El parseo entero se apoya en constantes geométricas calibradas contra
`data/GII_horario2627.pdf` — un cambio de plantilla del generador de
horarios de la ESI (otro tamaño de letra, otra disposición del
minicalendario, otro conjunto de colores) puede romper el parseo de forma
silenciosa. Antes de tocar código:

1. **Corre `horario debug --page N` sobre una página del PDF nuevo** y
   compárala visualmente con una página conocida del PDF de referencia. Si
   los rectángulos verdes (`ClassBlock`) no cubren todos los bloques de
   clase reales, o aparecen bloques rojos (`unparsed`) con un motivo nuevo,
   ahí está el síntoma.
2. **Corre la suite de tests contra el PDF nuevo** (cambia
   `tests/conftest.py::FIXTURE_PDF` a un fixture temporal, o añade el nuevo
   PDF como fixture adicional sin borrar el antiguo). Los fallos de forma
   (`len(page.warnings)`, recuentos exactos) señalan qué módulo se rompió.
3. **Los puntos calibrados que más probablemente rompan**:
   - `horario_uca/parse/session.py::_size_bands` — deriva `ACRONYM_SIZE`,
     `GROUP_LINE_SIZE`, `WEEK_STRIP_SIZE` como ratio sobre el tamaño de la
     cabecera de hora de la propia página, NO en puntos absolutos (ya rompió
     una vez así con una tipografía mucho más grande de otro grado). Si la
     plantilla nueva usa una tipografía muy distinta, ajustar aquí primero.
   - `horario_uca/parse/calendar.py` — `DAY_SIZE`/`WEEK_MARKER_SIZE`
     (también relativos, mismo criterio), `MARKER_MAX_GAP_HORIZONTAL`/
     `_VERTICAL` (offset marcador de semana → fila) y la detección de layout
     horizontal vs. apilado de los 5 bloques de mes (`_header_index_for`).
   - Los tres colores de semana/día lectivo (`(0,0,0)` activo/lectivo,
     `(196,196,196)` semana inactiva, `(255,0,0)` no lectivo, `(191,191,191)`
     fuera de periodo) están hardcodeados como literales RGB en
     `parse/session.py` y `parse/calendar.py` — si la ESI cambia la paleta
     del PDF (no solo el tamaño), estos son los primeros sitios a revisar.
   - `horario_uca/parse/legend.py` — el mapeo pastel↔saturado de
     `parse/grid.py`/`session.py` es solo una señal auxiliar para *detectar*
     bloques de clase, nunca la fuente de verdad de a qué asignatura
     pertenece un bloque (el mapeo fiable es leyenda → texto del acrónimo
     dentro del rectángulo, nunca color → asignatura); no debería hacer
     falta tocarlo salvo que cambie el rango de luminosidad que distingue
     pastel de fondo gris.
4. **Ningún módulo de `parse/` debe lanzar excepción** — es una regla del
   proyecto, no una casualidad: si al adaptar el parser a un PDF nuevo una
   función empieza a lanzar, la primera corrección es convertir ese fallo
   en un `ParseWarning`/entrada en `unparsed` con un código descriptivo, y
   solo después investigar la causa con calma — nunca dejar que un fallo
   de una página tumbe las otras 23.
5. **Documenta el cambio de constantes en el propio commit**: qué umbral se
   tocó, contra qué PDF se verificó y con qué cifra exacta — sin esa
   constancia, el próximo ajuste de la misma constante parte de cero.

## Licencia

El código de este proyecto es software libre bajo la **GNU General Public
License v3.0 (GPLv3)** — texto completo en [LICENSE](LICENSE).

Este proyecto depende de **[PyMuPDF](https://pymupdf.readthedocs.io/)**
para leer los PDF, que está licenciado bajo **AGPL-3.0** (confirmado con
`pip show pymupdf`, no de memoria). La GPLv3 (sección 13) permite combinar
código GPLv3 con código AGPL-3.0, pero el programa combinado, al ofrecerse
como servicio web, sigue obligado por los términos de la AGPL a poner su
código fuente a disposición de quien use ese servicio — por eso **la
interfaz web desplegada lleva en el pie un enlace "Código fuente" a este
mismo repositorio**, siempre visible, en los cuatro pasos del asistente.

Resto de dependencias directas (todas con licencia permisiva, compatibles
con GPLv3 — confirmado con `pip show`, no de memoria):

| Paquete | Licencia |
|---|---|
| pydantic | MIT |
| icalendar | BSD-2-Clause |
| pyyaml | MIT |
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| starlette (dependencia de fastapi) | BSD-3-Clause |
| anyio (dependencia de starlette) | MIT |
| pytest, httpx (solo desarrollo/tests, no se distribuyen con la app) | MIT / BSD-3-Clause |

Cada fichero `.py` propio del proyecto lleva la cabecera de licencia
recomendada por la propia GPLv3 ("How to Apply These Terms to Your New
Programs", al final de [LICENSE](LICENSE)).

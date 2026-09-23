# Imagen para el servicio web (FastAPI). Sin build-essential: PyMuPDF trae
# wheels prebuilt para linux/amd64 en Python 3.12, así que no hace falta
# compilar nada — mantiene la imagen pequeña y el build rápido.
FROM python:3.12-slim

WORKDIR /app

# Solo lo que pip necesita para resolver/instalar, antes de copiar el
# código — así un cambio en horario_uca/ no invalida la capa de deps.
COPY pyproject.toml README.md ./
COPY horario_uca ./horario_uca
RUN pip install --no-cache-dir ".[web]"

# Sin datos de la UCA ni ficheros de prueba en la imagen — el PDF lo sube
# el alumno en cada petición, nunca vive en el contenedor (sin disco
# persistente en ningún punto de este servicio).

# Usuario sin privilegios — coste cero, endurecimiento mínimo razonable.
RUN useradd -m appuser
USER appuser

# Render inyecta $PORT en tiempo de ejecución (no es fijo) — uvicorn tiene
# que escuchar ahí, nunca en un puerto hardcodeado.
CMD ["sh", "-c", "uvicorn horario_uca.web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]

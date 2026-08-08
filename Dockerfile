# AgroLens — imagen de producción
#
# Las ruedas de geopandas, rasterio, pyproj y fiona ya traen GDAL y PROJ
# adentro, así que no hace falta instalar librerías del sistema: la imagen
# queda en ~1,2 GB en vez de los ~3 GB de la variante con GDAL de apt.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGROLENS_DATA_DIR=/data

# curl es sólo para el HEALTHCHECK; el resto de las dependencias son ruedas
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Las dependencias primero: así el caché de capas sobrevive a los cambios de código
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agrolens/ ./agrolens/
COPY views/ ./views/
COPY app.py .
COPY .streamlit/config.toml ./.streamlit/config.toml

# Los lotes, el caché y las exportaciones viven acá: montar un volumen o se
# pierden en cada redeploy.
RUN mkdir -p /data && useradd --create-home --uid 1000 agrolens \
    && chown -R agrolens:agrolens /data /app
USER agrolens

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

# Cloud Run y varios PaaS inyectan $PORT; en local vale el 8501 por defecto.
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true"]

# Imagen de los scrapers que corren en Railway (ver railway/entrypoint.sh). Contexto: raiz del repositorio.
# En Railway: variable RAILWAY_DOCKERFILE_PATH=railway/Dockerfile y SERVICE=<servicio>.
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client rsync ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY nas/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY nas /app/nas
COPY scripts /app/scripts
COPY data /app/data
COPY db/postgres-migrations /app/db/postgres-migrations
COPY railway /app/railway
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["sh", "/app/railway/entrypoint.sh"]

FROM python:3.13-slim
WORKDIR /app
COPY nas/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY nas /app/nas
COPY scripts /app/scripts
COPY db/postgres-migrations /app/db/postgres-migrations
COPY data /app/data
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "nas/sync_agent.py"]

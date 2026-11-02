# The data-ngin service image (GraphQL API / one-shot ingestion runs).
# Build context is the REPO ROOT because data-ngin depends on the workspace
# lib platform-db, which is not published to PyPI:
#   docker build -f services/data-ngin/Dockerfile .
# The Airflow deployment uses Dockerfile.airflow instead; dags/ ship there.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY platform/db ./platform/db
COPY services/data-ngin ./services/data-ngin
RUN pip install --no-cache-dir \
    ./platform/db \
    ./services/data-ngin

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "data_ngin.api.server:app", "--host", "0.0.0.0", "--port", "8000"]

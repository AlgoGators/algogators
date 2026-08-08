FROM python:3.11-slim

WORKDIR /app
COPY src/ /app/src/

ENV PYTHONPATH=/app/src
CMD ["python", "-c", "import research_api; print(research_api.app_name())"]

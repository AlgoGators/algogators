FROM python:3.11-slim

WORKDIR /app
COPY src/ /app/src/

ENV PYTHONPATH=/app/src
CMD ["python", "-c", "import data_ngin; print(data_ngin.service_name())"]

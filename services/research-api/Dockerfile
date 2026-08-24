# Build context is the REPO ROOT (not this directory) because research-api
# depends on the workspace libs research-core and platform-db, which are not
# published to PyPI:
#   docker build -f services/research-api/Dockerfile .
# Installing the libs first means pip finds their requirements already
# satisfied and never consults PyPI for those names (also closes the
# dependency-confusion hole of resolving internal names from a public index).
FROM python:3.13-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY platform/db ./platform/db
COPY libs/research-core ./libs/research-core
COPY services/research-api ./services/research-api
RUN pip install --no-cache-dir \
    ./platform/db \
    ./libs/research-core \
    ./services/research-api

# Create logs directory
RUN mkdir -p logs

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 5000

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "research_api.wsgi:app"]

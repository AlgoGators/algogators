FROM python:3.13-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install the member straight from its manifest. Build context is the repo
# root so the workspace lockfile could be consulted later if needed:
#   docker build -f services/research-api/Dockerfile services/research-api
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Create logs directory
RUN mkdir -p logs

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 5000

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "research_api.wsgi:app"]

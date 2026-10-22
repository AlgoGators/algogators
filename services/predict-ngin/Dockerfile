# The predict-ngin CLI with the core dependencies. Build context is this directory
# (predict-ngin depends on no workspace libs):
#   docker build -t predict-ngin services/predict-ngin
#   docker run --rm -v /srv/predict-data:/data predict-ngin data pull polymarket
# Data lives on the mounted volume, never in the image.
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY predict_ngin ./predict_ngin
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd -m -u 1000 appuser && mkdir /data && chown appuser:appuser /data
USER appuser

ENV PREDICT_NGIN_DATA=/data
VOLUME /data

ENTRYPOINT ["predict-ngin"]
CMD ["data", "status"]

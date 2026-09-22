FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY aeris ./aeris

RUN pip install --no-cache-dir .

ENV AERIS_DB_PATH=/app/data/aeris.db
ENV AERIS_HOST=0.0.0.0
ENV AERIS_PORT=8000

EXPOSE 8000
VOLUME ["/app/data"]

CMD ["uvicorn", "aeris.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

"""Dockerfile for SAND localhost API + web UI."""

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e . \
    && python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL excel'); c.execute('LOAD excel')"

EXPOSE 8765

ENV SAND_DATA_DIR=/data
VOLUME ["/data"]

# Binds 0.0.0.0 — set SAND_API_TOKEN for any non-localhost publish.
CMD ["sand", "serve", "--host", "0.0.0.0", "--port", "8765"]

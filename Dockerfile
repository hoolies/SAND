"""Dockerfile for SAND API + web UI."""

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src ./src

# Pinned transitive deps from requirements.txt, then the local package without re-resolving.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps -e . \
    && python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL excel'); c.execute('LOAD excel')"

EXPOSE 8765

ENV SAND_DATA_DIR=/data
ENV SAND_HOST=0.0.0.0
ENV SAND_PORT=8765
VOLUME ["/data"]

# Container listens on all interfaces inside the network namespace.
# Compose should publish only 127.0.0.1 on the host, or set SAND_API_TOKEN.
CMD ["sand", "serve", "--host", "0.0.0.0", "--port", "8765"]

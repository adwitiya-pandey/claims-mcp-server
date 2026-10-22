FROM python:3.12-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    CLAIMS_DB_PATH=/data/claims.db \
    CLAIMS_AUDIT_PATH=/app/audit/audit.jsonl

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY schema.sql ./
COPY src/ src/
COPY data/adversarial_note_ids.txt data/adversarial_note_ids.txt

RUN uv sync --frozen --no-dev \
    && groupadd --gid 10001 claims \
    && useradd --uid 10001 --gid claims --no-create-home claims \
    && mkdir -p /app/audit /data \
    && chown -R claims:claims /app/audit

USER 10001:10001

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["uv", "run", "--frozen", "python", "-c", "from claims_mcp.db import open_readonly; c=open_readonly('/data/claims.db'); c.execute('SELECT 1'); c.close()"]

CMD ["uv", "run", "--frozen", "python", "-m", "claims_mcp", "--transport", "stdio"]

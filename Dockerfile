# =============================================================================
# STAGE 1: Builder (Instala dependencias de producción con uv sin dev-deps)
# =============================================================================
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Primero copiamos solo pyproject.toml y uv.lock para aprovechar el caché de capas de Docker
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# =============================================================================
# STAGE 2: Production Runtime para AWS ECS Fargate (0.25 vCPU / 0.5 GB RAM)
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Seguridad: Usuario y grupo sin privilegios de root (UID 10001)
RUN groupadd --gid 10001 koloala \
    && useradd --uid 10001 --gid koloala --shell /bin/false --create-home koloala

WORKDIR /app

# Copiamos el entorno virtual compilado desde el builder y el código de la aplicación
COPY --from=builder --chown=koloala:koloala /app/.venv /app/.venv
COPY --chown=koloala:koloala app ./app

# Creamos el directorio de logs con permisos para el usuario no-root
RUN mkdir -p /app/logs && chown -R koloala:koloala /app/logs

USER koloala

EXPOSE 8000

# En ECS Fargate usamos exec-form para que Uvicorn sea PID 1 y reciba SIGTERM directamente,
# disparando el lifespan (cierre de pool PostgreSQL + compresión y subida de audit.jsonl.gz a S3)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

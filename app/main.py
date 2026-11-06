import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.lots import router as lots_router
from app.api.v1.passes import router as passes_router
from app.core.audit import flush_audit_logs_on_shutdown
from app.core.config import settings
from app.db.session import check_db_readiness, dispose_db_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    Maneja el ciclo de vida del contenedor en AWS ECS Fargate (SIGTERM) y en local (Ctrl+C).
    Al iniciar (startup) prepara recursos; al apagarse (shutdown), cierra el pool de PostgreSQL
    y fuerza el volcado, compresión (.gz) y subida a S3 de cualquier log de auditoría pendiente.
    """
    import logging

    logger = logging.getLogger("uvicorn.error")
    logger.info("[Koloala Lifespan] Motor de Garita iniciado correctamente.")
    yield
    logger.info(
        "[Koloala Lifespan] Señal de apagado (SIGINT/SIGTERM) detectada. Cerrando DB y asegurando audit logs..."
    )
    await dispose_db_engine()
    flush_audit_logs_on_shutdown()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="API for Koloala, core service for high-throughput containerized workloads.",
    docs_url=None,  # Personalizado abajo con StandaloneLayout + Selector de Definiciones
    lifespan=lifespan,
)

app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(passes_router, prefix=settings.api_v1_prefix)
app.include_router(lots_router, prefix=settings.api_v1_prefix)


def _build_domain_openapi(
    *,
    title: str,
    version: str,
    description: str,
    allowed_path_prefixes: tuple[str, ...],
    strip_security: bool = False,
) -> dict[str, Any]:
    """
    Toma el esquema OpenAPI oficial de FastAPI y filtra únicamente los endpoints (paths)
    del dominio correspondiente. Si strip_security=True (Login público), oculta el botón 'Authorize'.
    """
    full_schema = copy.deepcopy(app.openapi())
    full_schema["info"] = {
        "title": title,
        "version": version,
        "description": description,
    }
    full_schema["paths"] = {
        path: path_item
        for path, path_item in full_schema.get("paths", {}).items()
        if any(path.startswith(prefix) for prefix in allowed_path_prefixes)
    }
    if strip_security and "components" in full_schema:
        full_schema["components"].pop("securitySchemes", None)
    return full_schema


@app.get("/openapi/auth.json", include_in_schema=False)
async def openapi_auth_definition() -> dict[str, Any]:
    return _build_domain_openapi(
        title="Las Villas Club - Public Auth Definition",
        version="1.0",
        description="Portal público de inicio de sesión sectorizado por Villa (`vc1`, `vc2`, `vc3`). No requiere Authorize previo.",
        allowed_path_prefixes=(f"{settings.api_v1_prefix}/auth/login",),
        strip_security=True,
    )


@app.get("/openapi/passes.json", include_in_schema=False)
async def openapi_passes_definition() -> dict[str, Any]:
    return _build_domain_openapi(
        title="Las Villas Club - Gatehouse & Passes Definition",
        version="1.0",
        description="Operación diaria de Garita: Generación de códigos/QR por residentes al día y verificación con DNI + Placa por vigilantes.",
        allowed_path_prefixes=(
            f"{settings.api_v1_prefix}/passes",
            f"{settings.api_v1_prefix}/auth/change-password",
        ),
    )


@app.get("/openapi/admin.json", include_in_schema=False)
async def openapi_admin_definition() -> dict[str, Any]:
    return _build_domain_openapi(
        title="Las Villas Club - Villa Admin & Security Definition",
        version="1.0",
        description="Herramientas exclusivas para la Junta Directiva: Padrón de mantenimiento, Bulk Onboarding y Radar Anti-Password Spraying.",
        allowed_path_prefixes=(
            f"{settings.api_v1_prefix}/lots",
            f"{settings.api_v1_prefix}/auth/security",
        ),
    )


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui() -> HTMLResponse:
    """
    Renderiza Swagger UI con 'StandaloneLayout' (que habilita la barra superior con el
    selector 'Select a definition').
    """
    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
        <link rel="shortcut icon" href="https://fastapi.tiangolo.com/img/favicon.png">
        <title>{settings.app_name} - API Docs</title>
        <style>
            body {{ margin: 0; padding: 0; }}
        </style>
    </head>
    <body>
        <div id="swagger-ui"></div>
        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
        <script>
        window.onload = function() {{
            window.ui = SwaggerUIBundle({{
                dom_id: "#swagger-ui",
                layout: "StandaloneLayout",
                deepLinking: true,
                persistAuthorization: true,
                urls: [
                    {{
                        name: "1. Auth Login Definition v1.0 (Public - No Authorize)",
                        url: "/openapi/auth.json"
                    }},
                    {{
                        name: "2. Gatehouse & Passes Definition v1.0",
                        url: "/openapi/passes.json"
                    }},
                    {{
                        name: "3. Villa Admin & Security Definition v1.0",
                        url: "/openapi/admin.json"
                    }},
                    {{
                        name: "4. Complete API Definition v1.0",
                        url: "/openapi.json"
                    }}
                ],
                "urls.primaryName": "2. Gatehouse & Passes Definition v1.0",
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ]
            }});
        }};
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# Endpoint for the root path, returning a welcome message
@app.get("/")
async def root():
    return {"service": settings.app_name, "status": "running"}


# Health check base (critical for when we configure the Application Load Balancer in ECS)
@app.get("/healthz/liveness")
async def liveness():
    return {"status": "ok"}


@app.get("/healthz/readiness")
async def readiness():
    """
    Readiness probe para AWS ALB / ECS Fargate: verifica que el pool de conexiones
    hacia PostgreSQL responda correctamente antes de enviar tráfico de garita.
    """
    is_ready = await check_db_readiness()
    if not is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection pool is not ready.",
        )
    return {
        "status": "ready",
        "database_backend": "postgresql" if settings.use_postgres else "in_memory",
    }

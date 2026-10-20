from fastapi import FastAPI

from app.api.v1.passes import router as passes_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="API for Koloala, core service for a high-throughput containerized workloads.",
)

app.include_router(passes_router, prefix=settings.api_v1_prefix)


# Endpoint for the root path, returning a welcome message
@app.get("/")
async def root():
    return {"service": settings.app_name, "status": "running"}


# Health check base (critical for when we configure the Application Load Balancer in ECS)
@app.get("/healthz/liveness")
async def liveness():
    return {"status": "ok"}

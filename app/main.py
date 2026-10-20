from fastapi import FastAPI

app = FastAPI(
    title="Koloala API",
    description="API for Koloala, core service for a high-throughput containerized workloads.",
    version="0.1.0",
)


# Endpoint for the root path, returning a welcome message
@app.get("/")
async def read_root():
    return {"message": "Welcome to the Koloala API!"}


# Health check base (critical for when we configure the Application Load Balancer in ECS)
@app.get("/healthz/liveness")
async def liveness():
    return {"status": "ok"}

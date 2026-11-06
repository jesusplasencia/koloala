from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# Connection Pool persistente en memoria calibrado para ECS Fargate (0.25 vCPU / 0.5 GB RAM)
# y ~500 lotes por Villa. Cero necesidad de RDS Proxy.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """
    Dependency / Unit of Work de sesión asíncrona por cada Request HTTP.
    Si el endpoint termina sin excepciones hace commit automático; si lanza error hace rollback.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_db_readiness() -> bool:
    """Ejecuta un SELECT 1 ligero para el /healthz/readiness de AWS ALB / ECS Fargate."""
    if not settings.use_postgres:
        return True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_db_engine() -> None:
    """Cierra limpiamente el pool de conexiones hacia PostgreSQL cuando el contenedor recibe SIGTERM."""
    if settings.use_postgres:
        await engine.dispose()

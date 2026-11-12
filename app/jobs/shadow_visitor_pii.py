"""Job diario (cron en Railway): pasa a shadow los DNIs/placas de visitantes con más de N días.

Uso: uv run python -m app.jobs.shadow_visitor_pii
Solo hace UPDATE (doc_number=NULL, vehicle_plate=NULL); conserva doc_hmac + doc_last2 para búsquedas.
"""

import asyncio

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.db.visit_repository import PostgresVisitRepository
from app.repositories.visit_repo import in_memory_visit_repo
from app.services.visit_service import VisitService


async def run() -> int:
    if not settings.use_postgres:
        return await VisitService(repo=in_memory_visit_repo).shadow_old_pii()
    async with AsyncSessionLocal() as session:
        count = await VisitService(repo=PostgresVisitRepository(session)).shadow_old_pii()
        await session.commit()
        return count


if __name__ == "__main__":
    total = asyncio.run(run())
    print(f"[shadow_visitor_pii] pases pasados a shadow: {total}")

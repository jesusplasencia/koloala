import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _offline_repositories(monkeypatch):
    """Los tests siempre usan repositorios en memoria, aunque el .env apunte a Supabase."""
    monkeypatch.setattr(settings, "use_postgres", False)

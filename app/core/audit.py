import gzip
import json
import logging
import shutil
from datetime import UTC, datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import Request

from app.core.config import settings

LIMA_TZ = timezone(timedelta(hours=-5), name="America/Lima")


def get_client_ip(request: Request | None) -> str:
    """
    Extrae la IP pública real del cliente.
    En AWS ECS detrás de un Application Load Balancer (ALB), la IP viene en X-Forwarded-For.
    """
    if request is None:
        return "internal"
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def get_user_agent(request: Request | None) -> str:
    if request is None:
        return "internal"
    return request.headers.get("user-agent", "unknown")


def _hourly_s3_rotator(source: str, dest: str) -> None:
    """
    Se ejecuta automáticamente cada vez que el archivo audit.jsonl rota por hora.
    Comprime el archivo cerrado en .gz (ahorrando ~85% de espacio) y queda listo
    para enviarse a S3 Standard-IA / Glacier Archival.
    """
    source_path = Path(source)
    gz_dest_path = Path(f"{dest}.gz")
    if source_path.exists():
        with source_path.open("rb") as f_in, gzip.open(gz_dest_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        source_path.unlink(missing_ok=True)


def _setup_audit_logger() -> logging.Logger:
    log_dir = Path(settings.audit_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "audit.jsonl"

    logger = logging.getLogger("koloala.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = TimedRotatingFileHandler(
            filename=str(log_file),
            when="H",
            interval=1,
            backupCount=0,  # Conservar todos los históricos horarios para backup a S3
            encoding="utf-8",
            utc=True,
            delay=True,
        )
        handler.suffix = "%Y-%m-%d_%H"
        handler.rotator = _hourly_s3_rotator
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    return logger


_audit_logger = _setup_audit_logger()


def log_audit_event(
    event: str,
    actor: str,
    client_ip: str = "internal",
    user_agent: str = "unknown",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Escribe una línea JSON inmutable (JSONL) con la hora UTC, hora exacta de Lima (Carabayllo),
    quién ejecutó la operación, desde qué IP y los detalles forenses.
    """
    now_utc = datetime.now(UTC)
    now_lima = now_utc.astimezone(LIMA_TZ)

    record: dict[str, Any] = {
        "timestamp_utc": now_utc.isoformat(),
        "timestamp_lima": now_lima.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "event": event,
        "actor": actor,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "details": details or {},
    }
    _audit_logger.info(json.dumps(record, ensure_ascii=False))
    return record

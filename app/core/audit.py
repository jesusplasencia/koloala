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


def _upload_gz_to_s3(gz_path: Path) -> bool:
    """
    Si S3_AUDIT_BUCKET está configurado, sube el archivo .jsonl.gz particionado por fecha
    directamente con StorageClass='GLACIER_IR' (Glacier Instant Retrieval: costo mínimo de
    archivo frío para consultas de 1 vez por semana/mes con recuperación instantánea).
    """
    if not settings.s3_audit_bucket or not gz_path.exists():
        return False

    try:
        import boto3  # type: ignore[import-not-found]

        now_utc = datetime.now(UTC)
        s3_key = (
            f"{settings.s3_audit_prefix.strip('/')}/"
            f"year={now_utc:%Y}/month={now_utc:%m}/day={now_utc:%d}/"
            f"{gz_path.name}"
        )
        s3_client = boto3.client("s3", region_name=settings.aws_region)
        s3_client.upload_file(
            Filename=str(gz_path),
            Bucket=settings.s3_audit_bucket,
            Key=s3_key,
            ExtraArgs={
                "StorageClass": settings.s3_audit_storage_class,
                "ContentType": "application/x-ndjson",
                "ContentEncoding": "gzip",
            },
        )
        # Una vez asegurado en S3, liberamos el espacio del disco efímero de ECS Fargate
        gz_path.unlink(missing_ok=True)
        return True
    except Exception as exc:
        logging.getLogger("uvicorn.error").error(
            "Error subiendo audit log %s a S3 bucket %s: %s",
            gz_path.name,
            settings.s3_audit_bucket,
            exc,
        )
        return False


def _hourly_s3_rotator(source: str, dest: str) -> None:
    """
    Se ejecuta automáticamente cada vez que el archivo audit.jsonl cumple 1 hora
    o cuando el contenedor se apaga (SIGTERM).
    1. Comprime el archivo cerrado en .gz (ahorrando ~85% de espacio).
    2. Si hay un bucket S3 configurado, lo sube con clase GLACIER_IR.
    """
    source_path = Path(source)
    gz_dest_path = Path(f"{dest}.gz")
    if source_path.exists() and source_path.stat().st_size > 0:
        with source_path.open("rb") as f_in, gzip.open(gz_dest_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        source_path.unlink(missing_ok=True)
        _upload_gz_to_s3(gz_dest_path)
    elif source_path.exists():
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


def flush_audit_logs_on_shutdown() -> None:
    """
    Se invoca automáticamente cuando el contenedor recibe SIGTERM (AWS ECS)
    o cuando el desarrollador presiona Ctrl+C (SIGINT) en su terminal local.
    Si hay eventos escritos en audit.jsonl, los cierra, los comprime en .gz
    y (si S3_AUDIT_BUCKET está configurado) los sube a S3 antes de terminar el proceso.
    """
    uvicorn_logger = logging.getLogger("uvicorn.error")
    for handler in _audit_logger.handlers:
        handler.flush()
        if isinstance(handler, TimedRotatingFileHandler):
            base_path = Path(handler.baseFilename)
            if base_path.exists() and base_path.stat().st_size > 0:
                shutdown_stamp = datetime.now(UTC).strftime(
                    "%Y-%m-%d_%H-%M-%S_shutdown"
                )
                dest_name = f"{handler.baseFilename}.{shutdown_stamp}"
                handler.close()
                _hourly_s3_rotator(str(base_path), dest_name)
                uvicorn_logger.info(
                    "[Koloala Audit] Snapshot de cierre guardado en: %s.gz",
                    dest_name,
                )


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

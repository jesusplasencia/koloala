"""Protección de datos personales de visitantes (Ley 29733).

Un DNI tiene solo 8 dígitos: un SHA-256 simple se rompe por fuerza bruta en segundos.
Por eso usamos HMAC-SHA256 con una clave secreta que vive fuera de la base de datos
(variable de entorno PII_HMAC_SECRET). Así el Admin puede buscar si un DNI entró,
pero una copia filtrada de la BD no revela el padrón.
"""

import hashlib
import hmac
import re

from app.core.config import settings


def normalize_doc(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"[\s\-\.]", "", raw).upper()
    return cleaned or None


_DOC_ID_RE = re.compile(r"^(\d{8}|[A-Z0-9]{9,12})$")


def normalize_document_id(raw: str | None) -> str | None:
    """DNI (8 dígitos) o Carnet de Extranjería (9 a 12 alfanuméricos), sin espacios ni guiones."""
    doc = normalize_doc(raw)
    if doc is None:
        return None
    if not _DOC_ID_RE.fullmatch(doc):
        raise ValueError("Ingresa un DNI de 8 dígitos o un Carnet de Extranjería de 9 a 12 caracteres.")
    return doc


def normalize_plate(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"[\s\-]", "", raw).upper()
    return cleaned or None


def pii_hmac(value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(
        settings.pii_hmac_secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def mask_doc(doc_number: str | None, last2: str | None) -> str | None:
    if doc_number:
        return doc_number
    if last2:
        return f"******{last2}"
    return None

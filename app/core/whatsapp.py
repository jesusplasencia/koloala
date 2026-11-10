"""Cliente de Notificaciones WhatsApp vía Evolution API (con Fallback a Consola Local).

Permite enviar el PIN de 8 dígitos generado 'Just-In-Time' (en memoria RAM, nunca
guardado en texto plano en DB) directamente al WhatsApp del vecino cuando confirma
su número de teléfono en su primer ingreso.
"""

import logging

import httpx

from app.core.config import settings
from app.models.lot import normalize_peru_phone

logger = logging.getLogger("uvicorn.error")


def build_crafted_whatsapp_pin_message(
    *,
    villa_id: str,
    lot_id: str,
    owner_name: str,
    pin_password: str,
) -> str:
    portal_url = f"https://{villa_id.lower()}.lasvillas.club"
    return (
        f"🏡 *LAS VILLAS CLUB — PORTAL OFICIAL ({villa_id})*\n\n"
        f"¡Hola *{owner_name}*! 👋\n"
        f"Se ha activado tu acceso de residente para la *Casa {lot_id}*.\n\n"
        f"🔑 *Tu PIN de acceso (8 dígitos):*\n"
        f"*{pin_password}*\n\n"
        f"👤 *Usuario:* {lot_id}\n"
        f"🌐 *Portal:* {portal_url}\n\n"
        f"_Ingresa estos 8 dígitos en la pantalla donde estás ahora. "
        f"Tu sesión quedará guardada automáticamente por 30 días en tu teléfono "
        f"para que generes pases QR de Taxi, Delivery y Visitas en 1 toque._"
    )


async def send_whatsapp_pin_notification(
    *,
    phone: str,
    villa_id: str,
    lot_id: str,
    owner_name: str,
    pin_password: str,
) -> tuple[bool, str]:
    """
    Envía el mensaje con el PIN por Evolution API si está configurada en .env.
    Si no hay servidor de Evolution API activo (modo desarrollo local sin contenedor),
    imprime el mensaje formateado en la consola del servidor FastAPI y retorna éxito
    en modo 'console_preview' para que puedas probar sin bloqueos.
    """
    norm_phone = normalize_peru_phone(phone)
    if not norm_phone:
        return False, "Número de teléfono inválido"

    message_text = build_crafted_whatsapp_pin_message(
        villa_id=villa_id,
        lot_id=lot_id,
        owner_name=owner_name,
        pin_password=pin_password,
    )

    # 1. En entorno local sin Evolution API configurada, imprimimos en consola ASCII-safe
    if not settings.evolution_api_url:
        banner = (
            "\n"
            + "=" * 72
            + f"\n[WHATSAPP OUTBOX -> +{norm_phone} | Lote {villa_id}-{lot_id}]\n"
            + f"PIN GENERADO EN RAM: {pin_password}\n"
            + "=" * 72
            + "\n"
        )
        print(banner, flush=True)

    # 2. Si Evolution API está configurada en .env, disparamos el POST real
    if settings.evolution_api_url and settings.evolution_api_key:
        base_url = settings.evolution_api_url.rstrip("/")
        instance = settings.evolution_instance_name.strip()
        endpoint = f"{base_url}/message/sendText/{instance}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    endpoint,
                    headers={
                        "apikey": settings.evolution_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "number": norm_phone,
                        "text": message_text,
                    },
                )
                if resp.status_code in (200, 201):
                    return True, "evolution_api"
                logger.warning(
                    "[Evolution API] Error HTTP %s al enviar WhatsApp a %s: %s",
                    resp.status_code,
                    norm_phone,
                    resp.text,
                )
                return False, f"Evolution API devolvió HTTP {resp.status_code}"
        except Exception as exc:
            logger.warning("[Evolution API] Fallo de conexión: %s", exc)
            if settings.environment == "local":
                return True, "console_fallback"
            return False, "No se pudo conectar con el servidor de WhatsApp (Evolution API)"

    return True, "console_local"

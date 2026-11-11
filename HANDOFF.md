# HANDOFF — Koloala / Fofoala (`lasvillas.club`)

> Documento de traspaso para que otro agente (Claude Desktop) retome el trabajo.
> Fecha: 2026-11-11. Idioma con el usuario: **español**. Usuario: Jesús Plasencia.

## 1. Ecosistema
| Repo | Ruta local | Stack | Rama | Deploy |
|---|---|---|---|---|
| **koloala** (backend) | `D:\Projects\koloala` | FastAPI + Python 3.12 (`uv`) + SQLAlchemy 2 async (`asyncpg`) + Alembic + Supabase Postgres | `dev` | Railway `https://koloala-api-production.up.railway.app` (**apagado** por el usuario para ahorrar créditos) |
| **fofoala** (frontend) | `D:\Projects\fofoala` | React 19 + Vite + TS + Tailwind v4 + TanStack Query + PWA | `dev` | Vercel `https://fofoala.vercel.app` |
| Evolution API (WhatsApp) | — | Railway | — | `https://evolution-api-production-f113d.up.railway.app`, instancia `lasvillas-dev` |

Comandos: `uv sync`, `uv run pytest -q` (offline, repos in-memory), `uv run alembic upgrade head`, `uv run python -m app.db.seed`.

## 2. Modelo de dominio actual (3NF por teléfono)
- **Identidad = celular de 9 dígitos** (`username`), **un solo PIN de 8 dígitos global** para todas las villas (`vc1..vc5.lasvillas.club`). Villa resuelta por subdominio o header `X-Villa-Id`.
- Tablas: `property_lots (villa_id, lot_id)`, **`lot_memberships (villa_id, lot_id, phone)`** con FK a lotes, `users (villa_id, username)` con `full_name`/`document_id`, `guest_passes` con trazabilidad (`created_by_phone/name/role`, `destination`, `checked_in_by_guard`), `ip_security_records`.
- Roles de usuario: `SUPER_ADMIN` (`916220518`, Jesús, global + ANCHOR de VC3-P20), `ADMIN` (`934253226`, admin VC3), `GUARD`, `RESIDENT`, `VISITOR`.
- Roles por lote: `ANCHOR` (precargado por admin; agrega/da de baja HOUSEHOLD) y `HOUSEHOLD` (solo genera QR; `403` al gestionar miembros).
- Migración `0002_3nf_memberships` **ya aplicada en Supabase** + seed ejecutado.

## 3. Reglas de seguridad (NO romper)
- `koloala_runtime` en Postgres solo tiene `SELECT, INSERT, UPDATE` → **nunca `DELETE`**; bajas = `is_active=False`.
- Nunca guardar PINs en texto plano (PBKDF2).
- **Nunca exponer teléfonos** de Admin/residentes a desconocidos; **no** devolver dígitos parciales (`masked_phone_hint` eliminado).
- Número sin cuenta ⇒ se asume **VISITOR** ⇒ onboarding Nombre + DNI (8) / CE (9-12).
- `register-visitor` → `409` si el número ya tiene cuenta o está precargado en `lot_memberships`.
- `activate-pin` → solo activación inicial; `409` si ya tiene PIN activo (anti-DoS de resets).
- Identidad telefónica sin membresía activa ⇒ no puede generar pases aunque tenga JWT vigente (HOUSEHOLD dado de baja).
- Un ANCHOR no puede degradar a otro ANCHOR vía upsert (`403`).
- Endpoint `GET /api/v1/passes/daily-log` solo GUARD/ADMIN/SUPER_ADMIN, aislado por villa.

## 4. SHADOW (implementado pero deshabilitado — no activar sin que el usuario lo pida)
- Catálogo de servicios por villa + minimapa en modo visitante: `app/models/villa_services.py`, flag `VISITOR_SERVICES_ENABLED = False`. Con el flag apagado, los pases de visitante salen con `destination="VISITA"` y `GET /api/v1/auth/villa-services` devuelve 404.

## 5. Estado de pruebas
`test/test_db.py`: **17 tests pasando**. Se auditaron y endurecieron los tests 12, 14 y 16 (asserts exactos en lugar de `>= 1`, revocación post-baja, anti-replay de activación, bitácora verificada por Guard **y** Admin, aislamiento VC2, visitante sin acceso a lotes ajenos).

## 6. Próximos pasos sugeridos
1. **Frontend `fofoala`**: adaptar el login al flujo por celular (`lookup` → `NEEDS_PHONE_ACTIVATION` / `READY_FOR_PASSWORD` / `UNREGISTERED_OFFER_VISITOR`), quitar uso de `masked_phone_hint`, selector multi-lote (`available_lots`, `GET /api/v1/lots/my-memberships`), gestión de HOUSEHOLD para ANCHOR, vista de bitácora diaria para Guard/Admin.
2. Encender Railway backend cuando se vaya a demostrar y redeployar desde `dev`.
3. Futuro: tabla `villa_services` administrable por cada Admin (hoy hardcodeada y en shadow).
4. Instructivo visual para el Administrador: `C:\Users\jesus\.gemini\antigravity\brain\b5e639e9-dd22-4f33-88d5-1f529af9be86\instructivo_lasvillas_3nf_1794358815306.jpg` (**desactualizado**: aún menciona el badge de contacto con el admin, que fue eliminado por seguridad; regenerar).

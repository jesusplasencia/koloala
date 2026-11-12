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
- Roles de usuario: `SUPER_ADMIN` (`916220518`, Jesús, global + ANCHOR de VC3-P20), `ADMIN` (`934253226`, admin VC3), `GUARD`, `RESIDENT`. **No existe rol VISITOR** (los visitantes entran sin cuenta).
- Roles por lote: `ANCHOR` (precargado por admin; agrega/da de baja HOUSEHOLD) y `HOUSEHOLD` (solo genera QR; `403` al gestionar miembros).
- Migraciones aplastadas en una sola `0001_initial_schema` (2026-11-11). Supabase debe reiniciarse (ver §7).

## 3. Reglas de seguridad (NO romper)
- `koloala_runtime` en Postgres solo tiene `SELECT, INSERT, UPDATE` → **nunca `DELETE`**; bajas = `is_active=False`.
- Nunca guardar PINs en texto plano (PBKDF2).
- **Nunca exponer teléfonos** de Admin/residentes a desconocidos; **no** devolver dígitos parciales (`masked_phone_hint` eliminado).
- `activate-pin` → solo activación inicial; `409` si ya tiene PIN activo (anti-DoS de resets).
- Identidad telefónica sin membresía activa ⇒ no puede generar pases aunque tenga JWT vigente (HOUSEHOLD dado de baja).
- Un ANCHOR no puede degradar a otro ANCHOR vía upsert (`403`).
- Bitácora `GET /api/v1/guard/visits/daily-log` solo GUARD/ADMIN/SUPER_ADMIN, aislada por villa. El sistema viejo `/passes` + `/passes/verify` fue **eliminado**.

## 5. Estado de pruebas
**30 tests pasando** (`test_db.py`, `test_visits.py`, `test_entries.py`). Se auditaron y endurecieron los tests 12, 14 y 16 (asserts exactos en lugar de `>= 1`, revocación post-baja, anti-replay de activación, bitácora verificada por Guard **y** Admin, aislamiento VC2, visitante sin acceso a lotes ajenos).

## 5b. Pases grupales sin cuenta (2026-11-11, migración `0003_group_visits` — **no aplicada aún en Supabase**)
- El QR es la única puerta de entrada; los visitantes **no** crean cuenta ni dan celular. Código en `app/models/visit.py`, `app/services/visit_service.py`, `app/api/v1/visits.py`, repos `app/repositories/visit_repo.py` / `app/db/visit_repository.py`.
- Orígenes: `HOST_INVITE` (`POST /visits/invite`) y `GUARD_DESK` (`POST /guard/visits/desk`, solo hacia servicios ML/CA públicos). **Ya no existe autoservicio** (`POST /public/visits` eliminado): quien llega sin invitación muestra su DNI físico, el vigilante fotografía el reverso, el OCR (MRZ, tesseract.js en el teléfono) rellena nombres/apellidos/DNI y la foto se descarta. `pass_attendees.doc_source` = `OCR`/`MANUAL` (migración `0002_attendee_doc_source`, aplicada). `GET /public/villa` queda como vitrina informativa.
- Asistentes con DNI opcional; verificación `DOC_SHOWN` / `DECLARED` / `VOUCHED` (avalado por otro asistente). Sin check-out.
- MiniLotes/áreas comunes: `property_lots.lot_type` + `display_name` + `is_public`, carga masiva `POST /admin/catalog/bulk`.
- Privacidad: DNI/placa de visitantes visibles 90 días; job `uv run python -m app.jobs.shadow_visitor_pii` los pasa a HMAC (`PII_HMAC_SECRET`). `GET /admin/access-search` registra cada consulta en `data_access_log` (solo INSERT).
- Tests: `test/test_visits.py`.
- **Carnet QR del residente**: `GET /me/carnet` (rota cada 30 s, HMAC con `RESIDENT_QR_SECRET`, por persona, muestra todos sus lotes), `POST /guard/resident-entry` (QR o MANUAL lote+celular; el carnet no se gasta; la deuda solo advierte), `GET /me/entries`. Tabla `entry_events` solo INSERT.
- Verificación `DOC_DIGITAL`; tope `MAX_HOUSEHOLD_PER_LOT=12` (Admin lo puede superar); bitácora de garita enmascara DNIs salvo `?reveal=true` (auditado). Tests: `test/test_entries.py`.
- Pendiente: tabla `people` (nombre/DNI fuera de `lot_memberships`) y rate limit del cartel persistido en BD.

## 6. Próximos pasos sugeridos
1. **Frontend `fofoala`**: adaptar el login al flujo por celular (`lookup` → `NEEDS_PHONE_ACTIVATION` / `READY_FOR_PASSWORD` / `UNREGISTERED_NO_ACCOUNT`), pantallas de Carnet QR, invitación (GUEST/TAXI/DELIVERY), página `/v/{token}`, vitrina pública y escáner de garita, quitar uso de `masked_phone_hint`, selector multi-lote (`available_lots`, `GET /api/v1/lots/my-memberships`), gestión de HOUSEHOLD para ANCHOR, vista de bitácora diaria para Guard/Admin.
2. Encender Railway backend cuando se vaya a demostrar y redeployar desde `dev`.
3. Futuro: tabla `people` (nombre/DNI fuera de `lot_memberships`) y rate limit del cartel persistido en BD.
4. Instructivo visual para el Administrador: `C:\Users\jesus\.gemini\antigravity\brain\b5e639e9-dd22-4f33-88d5-1f529af9be86\instructivo_lasvillas_3nf_1794358815306.jpg` (**desactualizado**: aún menciona el badge de contacto con el admin, que fue eliminado por seguridad; regenerar).

## 7. Reiniciar Supabase (desarrollo)
SQL Editor de Supabase (como `postgres`):
```sql
DROP TABLE IF EXISTS
  entry_events, data_access_log, pass_attendees, visit_passes,
  guest_passes, ip_security_records, users, lot_memberships,
  property_lots, alembic_version
CASCADE;
```
Luego: `uv run alembic upgrade head` y `uv run python -m app.db.seed`. Definir en Railway `PII_HMAC_SECRET` y `RESIDENT_QR_SECRET`.

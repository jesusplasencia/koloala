# HANDOFF — Koloala / Fofoala (`lasvillas.club`)

> Documento de traspaso para que otro agente retome el trabajo.
> Actualizado: 2026-11-15. Idioma con el usuario: **español** (explicaciones simples, paso a paso). Usuario: Jesús Plasencia.

## 1. Ecosistema
| Repo | Ruta local | Stack | Rama | Deploy |
|---|---|---|---|---|
| **koloala** (backend) | `D:\Projects\koloala` | FastAPI + Python 3.12 (`uv`) + SQLAlchemy 2 async (`asyncpg`) + Alembic + Supabase Postgres | `dev` | Railway `https://koloala-api-production.up.railway.app` |
| **fofoala** (frontend) | `D:\Projects\fofoala` | React 19 + Vite + TS + Tailwind v4 + TanStack Query + PWA (tema navideño) | `dev` | Vercel `https://fofoala.vercel.app` |
| Evolution API (WhatsApp) | — | Railway | — | `https://evolution-api-production-f113d.up.railway.app`, instancia `lasvillas-dev` |

Comandos: `uv sync`, `uv run pytest -q` (offline: `test/conftest.py` fuerza repos en memoria), `uv run alembic upgrade head`, `uv run python -m app.db.seed`.
Variables en Railway: `PII_HMAC_SECRET` (no cambiar nunca), `RESIDENT_QR_SECRET`.

## 2. Modelo de identidad (2026-11-15)
| Tabla | PK | Contenido |
|---|---|---|
| `people` | `id` UUID; `phone` UNIQUE | 1 fila por ser humano, global a todas las villas: nombre, DNI, password_hash, is_super_admin, token_version, access_reset_at, último login |
| `staff_roles` | (villa_id, person_id) | ADMIN / GUARD por villa |
| `property_lots` | (villa_id, lot_id) | lote físico o catálogo (`lot_type` LOT / MINI_LOT / COMMON_AREA, `display_name`, `is_public`). **Sin** datos del dueño |
| `lot_memberships` | (villa_id, lot_id, person_id) | ANCHOR / HOUSEHOLD, `added_by_person_id`, `is_active`. **Sin** nombre/DNI/celular |
| `visit_passes` / `pass_attendees` | — | QR grupal sin cuenta (GUEST/TAXI/DELIVERY) y sus asistentes; `host_person_id` |
| `entry_events` | id | ingresos de residentes con carnet (solo INSERT), `person_id` |
| `pin_requests` | id | envíos de PIN (solo INSERT), `person_id` |
| `data_access_log` | id | quién consultó DNIs/placas (solo INSERT) |
| `ip_security_records` | (villa_id, ip) | radar anti fuerza bruta |

- **Todos ingresan con su celular** (también guardias y admins). No existen cuentas por código de lote (`P20`, `ADMIN`, `GUARDIA1`).
- Rol efectivo por villa: SUPER_ADMIN si `is_super_admin` → ADMIN/GUARD si `staff_roles` activo → RESIDENT si tiene membresía → si no, 403.
- El JWT lleva `sub = person_id` (UUID) y `tv` (token_version); cada request valida la persona y el `tv` contra `people`.
- Nombre y DNI se cambian en un solo lugar (`people`).

## 3. Reglas de seguridad (NO romper)
- `koloala_runtime` solo `SELECT, INSERT, UPDATE` (nunca `DELETE`); bajas = `is_active=False`. Tablas de log solo `SELECT, INSERT`.
- PINs/contraseñas con PBKDF2; PIN inicial de 8 dígitos solo por accesibilidad; contraseñas propias de 8–64 caracteres.
- `lookup` nunca devuelve nombre/rol/lotes. Primer ingreso exige DNI/CE registrado (tiempo constante), 3 fallos bloquean IP, máx. 3 PIN por persona cada 24 h.
- Cambio de DNI por el Admin ⇒ `reset_access` (contraseña invalidada, sesiones cerradas, límite de PIN reiniciado).
- DNI enmascarado (`******NN`) en listas de miembros, personal y bitácora (revelar en bitácora queda auditado).
- Visitantes sin cuenta: invitación del residente o registro en mesa de garita con foto del reverso del DNI (OCR en el teléfono; la foto se descarta). DNI/placa de visitantes a los 90 días pasan a HMAC (`app/jobs/shadow_visitor_pii.py`).

## 4. Cuentas del seed (dev)
| Celular | Persona | Rol |
|---|---|---|
| 916220518 | Jesús Plasencia (oficial) | SUPER_ADMIN + ANCHOR de VC3-P20 |
| 934253226 | Administrador Villa Club 3 (oficial) | ADMIN de VC3 |
| 900000001 | Vigilante de Prueba (**ficticio**) | GUARD de VC3 |
| 980180219 | Angel Plasencia | HOUSEHOLD de P20 (sin PIN: activa con su DNI cuando el admin lo registre) |
Los PIN se imprimen en la consola al correr el seed por primera vez. Catálogo: ML01 Panadería, ML02 Botica, CA01 Canchita de Fútbol 1, CA02 Parque 1.

## 5. Reiniciar la base de datos (dev)
1. **Borrar todo** en Supabase → SQL Editor (como `postgres`):
```sql
DROP TABLE IF EXISTS
  pin_requests, data_access_log, entry_events, pass_attendees, visit_passes,
  ip_security_records, lot_memberships, staff_roles, property_lots, people,
  users, guest_passes, alembic_version
CASCADE;
```
2. **Volver a crear** (recomendado, desde la terminal del proyecto):
```
uv run alembic upgrade head
uv run python -m app.db.seed
```
3. **Alternativa sin Alembic:** pegar en el SQL Editor el contenido de `app/db/sql/02_schema_dev.sql` (CREATE TABLE, índices y GRANTs, generado con `uv run alembic upgrade head --sql`) y luego correr solo el seed.
4. Volver a cargar el padrón real (CSV `lote,propietario,telefono,aldia,dni`).

## 6. Pruebas
`uv run pytest -q` → 38 tests: `test_identity.py` (login por celular, padrón→personas, nombre en un solo lugar, ANCHOR/HOUSEHOLD, multi-villa, spraying, personal), `test_onboarding.py` (DNI, límites, reset de acceso), `test_visits.py`, `test_entries.py`.

## 7. Próximos pasos
1. Probar en el teléfono: pegado del PIN, cámara del DNI, carnet rotativo, escáner de garita.
2. Aviso de privacidad visible en garita e inscripción del banco de datos "Control de acceso" ante la ANPD.
3. Filtro global "Falta DNI" en el padrón.

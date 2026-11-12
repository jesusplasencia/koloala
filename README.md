# KOLOALA (Kode. Love. Lines.)
## A Line Away Of Loving Outstanding Kode (KLL)

---

## 🚀 Quickstart (Arranque Rápido, Migraciones y Testing)

Asumiendo que ya cuentas con **Python 3.12+** y **`uv`** instalados en tu máquina:

```powershell
# 1. Sincronizar dependencias (incluyendo entorno virtual .venv y herramientas de test)
uv sync

# 2. Aplicar migraciones SQL (3NF) en PostgreSQL / Supabase
uv run alembic upgrade head

# 3. Poblar datos iniciales (Super Admin 916220518, Admin VC3 934253226, Guardia y Lote P20)
uv run python -m app.db.seed

# 4. Levantar el servidor de desarrollo con recarga en caliente (Hot-Reload)
uv run uvicorn app.main:app --reload

# 5. Ejecutar la suite completa de 17 pruebas automatizadas (Pytest)
uv run pytest -v
```

Una vez encendido el servidor local en `http://127.0.0.1:8000`:
- **Documentación Interactiva (Swagger UI):** `http://127.0.0.1:8000/docs`
- **Health Check (Liveness Probe):** `http://127.0.0.1:8000/healthz/liveness`
- **Flujos HTTP Ejecutables:** Abre [`requests/api.http`](requests/api.http) en VS Code y haz clic en `Send Request` sobre cada paso.

---

## 🏛️ Arquitectura Relacional 3NF e Identidad Telefónica (`lasvillas.club`)

**Koloala** implementa un modelo relacional normalizado (**1NF, 2NF y 3NF**) que desacopla la **Identidad de la Persona (su número de celular de 9 dígitos)** de los **Lotes Físicos (`property_lots`)**, replicando el estándar de control de accesos de edificios residenciales modernos (NYC / Smart Condos) a través de las 5 etapas de **Las Villas (`vc1`..`vc5.lasvillas.club`)**.

### 1. Una Sola Contraseña Global por Persona
- Cada persona se identifica con su **número de celular de 9 dígitos** (`username = 9XXXXXXXX`) y un **único PIN numérico de 8 dígitos** enviado por **WhatsApp (Evolution API)** en su primer ingreso.
- Si un propietario posee lotes en distintas villas (por ejemplo, `P20` en `vc3.lasvillas.club` y `M20` en `vc2.lasvillas.club`), inicia sesión con **el mismo celular y el mismo PIN** en cualquier subdominio de `lasvillas.club`.

### 2. Roles de Administración y Jerarquía por Lote (`lot_memberships`)
- **`SUPER_ADMIN` (`916220518` — Jesús Plasencia):** Administrador global de la plataforma `lasvillas.club` con acceso transversal a todas las Villas y simultáneamente **`ANCHOR`** del lote `VC3-P20`.
- **`ADMIN` (`934253226` — Administrador Villa Club 3):** Administrador oficial de `VC3`. Precarga a las Personas-Ancla en el padrón, gestiona la habilitación mensual de mantenimiento y visualiza el listado diario de visitantes e ingresos de su Villa.
- **`ANCHOR` (Persona-Ancla / Titular del Lote):**
  - Siempre precargado por el Administrador de la Villa o el Super Admin.
  - Puede tener uno o varios lotes (ej. Pepito Juárez es `ANCHOR` en `P20` y `Q02`).
  - Puede generar códigos QR de acceso (propios, visitas, taxis, delivery) **y administrar la lista de integrantes (`HOUSEHOLD`) de su lote**.
- **`HOUSEHOLD` (Conviviente / Familiar / Invitado Frecuente):**
  - Agregado manualmente por la Persona-Ancla desde su sesión (ej. `Angel Plasencia`, `980180219`, asociado a `VC3 + P20`) o precargado por Administración.
  - Obtiene su PIN de 8 dígitos por WhatsApp en su primer ingreso.
  - **Permisos estrictos:** Dentro de la aplicación solo puede generar códigos QR de acceso asociados al lote, pero **NO puede agregar ni borrar a ningún integrante del lote (`403 Forbidden`)**.
  - Además, una persona puede ser `ANCHOR` en su propio lote (`Q03`) y simultáneamente `HOUSEHOLD` en el lote de un familiar (`P20`), eligiendo con un toque para qué lote genera el QR.
- **Visitantes (sin cuenta):** no existe rol VISITOR. Las personas de afuera entran con un QR grupal:
  - **Invitación** del ANCHOR/HOUSEHOLD (`POST /api/v1/visits/invite`, `kind` = `GUEST` | `TAXI` 30 min | `DELIVERY` 45 min; taxi/delivery exigen placa).
  - **Cartel de garita = vitrina** de la Villa (`GET /api/v1/public/villa`, `POST /api/v1/public/visits`): MiniLotes (`A01 - Panadería`) y áreas comunes.
  - **Mesa del guardia** (`POST /api/v1/guard/visits/desk`) para quien no tiene celular.
  - DNI opcional; verificación `DOC_SHOWN` (físico), `DOC_DIGITAL`, `DECLARED` (de memoria) o `VOUCHED` (avalado por un acompañante). Nadie se queda afuera; todos quedan registrados.
  - Un número sin cuenta recibe `UNREGISTERED_NO_ACCOUNT` en `/auth/lookup`. Nunca se exponen teléfonos.

### 3. Garita y Trazabilidad
- **Residentes:** Carnet QR rotativo (`GET /api/v1/me/carnet`, cambia cada 30 s). El guardia escanea en `POST /api/v1/guard/resident-entry` y ve *"RESIDENTE: NOMBRE - LOTE"*. El carnet no se gasta; cada ingreso es un evento en `entry_events`.
- **Visitas:** `GET /api/v1/guard/visits/{qr|código}` + `POST /api/v1/guard/visits/{ref}/check-in`.
- **Bitácora 24 h** (`GET /api/v1/guard/visits/daily-log`): DNIs enmascarados salvo `?reveal=true` (auditado en `data_access_log`).
- **Incidentes** (`GET /api/v1/admin/access-search`, solo Admin): por fechas, DNI, placa o destino. A los 90 días los DNIs/placas de visitantes pasan a HMAC (`uv run python -m app.jobs.shadow_visitor_pii`).

---

## 🗄️ Esquema de Base de Datos Relacional (3NF en PostgreSQL / Supabase)

| Tabla Física | Llave Primaria Compuesta | Llaves Foráneas / Índices | Propósito 3NF |
| :--- | :--- | :--- | :--- |
| **`property_lots`** | `(villa_id, lot_id)` | PK Compuesta | Predio físico y mantenimiento; `lot_type` (`LOT` / `MINI_LOT` / `COMMON_AREA`), `display_name` e `is_public` para la vitrina. |
| **`lot_memberships`** | `(villa_id, lot_id, phone)` | `FK -> property_lots(villa_id, lot_id)` + Index `phone` | Relación `N:M` normalizada entre Personas (`phone`) y Lotes (`villa_id, lot_id`) con su rol (`ANCHOR` o `HOUSEHOLD`) y baja lógica (`is_active`) compatible con `koloala_runtime`. |
| **`users`** | `(villa_id, username)` | Index `username` | Credenciales (`password_hash` PBKDF2-SHA256), `role` (`SUPER_ADMIN`, `ADMIN`, `GUARD`, `RESIDENT`), `full_name` y `document_id` (DNI/CE). |
| **`visit_passes`** | `(villa_id, token)` | `uq (villa_id, short_code)` | QR grupal sin cuenta (`GUEST`/`TAXI`/`DELIVERY`), origen, destino, anfitrión, placa (+HMAC). |
| **`pass_attendees`** | `(villa_id, token, attendee_no)` | `FK -> visit_passes` + Index `doc_hmac` | Cada persona del grupo: nombres, documento opcional, verificación y aval. |
| **`entry_events`** | `id` | Index `person_phone`, `at` | Ingresos de residentes con Carnet QR (solo INSERT). |
| **`data_access_log`** | `id` | Index `villa_id` | Quién consultó DNIs/placas (solo INSERT). |
| **`ip_security_records`** | `(villa_id, ip_address)` | `uq_villa_ip` | Radar Anti-Fuerza Bruta y Anti-Password Spraying (bloqueo al 3er fallo y correlación de lote sospechoso). |

---

## 🧪 Cobertura de Pruebas Automatizadas

`uv run pytest -v` (offline, repos en memoria):
- `test/test_db.py`: padrón y credenciales, password spraying, aislamiento VC2/VC3, paginación, auditoría `.gz`, login en 2 pasos con PIN por WhatsApp, admins oficiales, ANCHOR/HOUSEHOLD (Angel / Caillou), multi-lote (Pepito / Pepita), número sin cuenta, PIN global multi-villa.
- `test/test_visits.py`: catálogo y vitrina, invitación familiar sin teléfonos y abuelo avalado, taxi/delivery con placa, pase vencido, lote moroso antes de llegar, cartel público y rate limit, mesa del guardia, aislamiento, búsqueda de incidentes y shadow a 90 días.
- `test/test_entries.py`: Carnet QR rotativo, carnet vencido/adulterado/de otra villa, baja de HOUSEHOLD, ingreso manual, tope de HOUSEHOLD, `DOC_DIGITAL` y bitácora enmascarada.

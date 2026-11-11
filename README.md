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
- **`VISITOR` (Visitante Externo):**
  - Si el celular no tiene cuenta (no es `ANCHOR`, `HOUSEHOLD` ni `VISITOR`), se asume **VISITOR** y se inicia su onboarding: **Nombre y Apellido + DNI (8 dígitos) o Carnet de Extranjería (9-12 caracteres)** vía `POST /api/v1/auth/register-visitor`. Recibe su PIN por WhatsApp y genera un QR de visita trazable.
  - **Seguridad:** nunca se expone el teléfono del Administrador ni de residentes a desconocidos, ni dígitos parciales de ningún número (el celular ya es el username).
  - Un número con cuenta existente o precargado en el padrón no puede re-registrarse como visitante (`409`), y `/activate-pin` solo funciona para la activación inicial (`409` si ya tiene PIN activo) para evitar resets maliciosos.
  - **[SHADOW]** Catálogo de servicios internos por Villa (Panadería, Botica, negocios custom como *Marcianos Clarita* o *Brostería Marcos Huamán*) y minimapa: implementado en [`app/models/villa_services.py`](app/models/villa_services.py) pero deshabilitado (`VISITOR_SERVICES_ENABLED = False`).

### 3. Trazabilidad Forense en cada QR y Bitácora Diaria (`GET /api/v1/passes/daily-log`)
- Cada pase QR almacena exactamente quién lo generó:
  - Ejemplo: *"Angel Plasencia (`980180219`), rol `HOUSEHOLD` de `VC3` - Lote `P20`, generó QR para `Amigo Caillou`"*.
- Tanto **Garita (`GUARD`)** (desde celular o computadora de escritorio) como el **Administrador de la Villa (`ADMIN`)** disponen del endpoint `GET /api/v1/passes/daily-log` para consultar en vivo todos los visitantes y residentes que han ingresado en el día con su rol, lote/destino comercial, DNI, placa vehicular y hora de check-in.

---

## 🗄️ Esquema de Base de Datos Relacional (3NF en PostgreSQL / Supabase)

| Tabla Física | Llave Primaria Compuesta | Llaves Foráneas / Índices | Propósito 3NF |
| :--- | :--- | :--- | :--- |
| **`property_lots`** | `(villa_id, lot_id)` | PK Compuesta | Representa únicamente el predio físico y su estado de mantenimiento (`is_maintenance_up_to_date`). |
| **`lot_memberships`** | `(villa_id, lot_id, phone)` | `FK -> property_lots(villa_id, lot_id)` + Index `phone` | Relación `N:M` normalizada entre Personas (`phone`) y Lotes (`villa_id, lot_id`) con su rol (`ANCHOR` o `HOUSEHOLD`) y baja lógica (`is_active`) compatible con `koloala_runtime`. |
| **`users`** | `(villa_id, username)` | Index `username` | Credenciales (`password_hash` PBKDF2-SHA256), `role` (`SUPER_ADMIN`, `ADMIN`, `GUARD`, `RESIDENT`, `VISITOR`), `full_name` y `document_id` (DNI/CE). |
| **`guest_passes`** | `(villa_id, code)` | Index `lot_id` | Pases QR de un solo uso con trazabilidad completa (`created_by_phone`, `created_by_name`, `created_by_role`, `destination`, `checked_in_dni`, `checked_in_plate`, `checked_in_by_guard`). |
| **`ip_security_records`** | `(villa_id, ip_address)` | `uq_villa_ip` | Radar Anti-Fuerza Bruta y Anti-Password Spraying (bloqueo al 3er fallo y correlación de lote sospechoso). |

---

## 🧪 Cobertura de Pruebas Automatizadas (`test/test_db.py`)

La suite cuenta con **17 tests de integración exhaustivos** (`uv run pytest -v`):
1. `test_create_and_verify_pass_happy_path`: Ciclo completo de creación de QR y verificación en garita con DNI y placa.
2. `test_taxi_requires_plate_without_burning_pass`: Retención en garita si falta placa en Taxi/Delivery sin quemar el pase.
3. `test_cannot_reuse_already_used_pass`: Protección Single-Use Pass contra reuso de QR.
4. `test_delinquent_lot_cannot_create_pass`: Bloqueo `403 Forbidden` para lotes con deuda de mantenimiento.
5. `test_expired_pass_is_denied_at_gatehouse`: Expiración automática por TTL vencido.
6. `test_pass_denied_if_admin_marks_lot_delinquent_before_arrival`: Revocación en tiempo real en garita si el lote entra en mora.
7. `test_admin_bulk_upsert_generates_credentials_and_allows_login`: Sincronización masiva de padrón y reseteo Zero-Knowledge.
8. `test_password_spraying_blocks_attacker_ip_and_identifies_suspect_lot`: Bloqueo de IP al 3er intento fallido e identificación forense.
9. `test_multi_villa_isolation_between_vc3_and_vc2`: Aislamiento estricto entre Villas para cuentas locales.
10. `test_lots_pagination_and_filtering`: Paginación de lotes y filtro de morosidad.
11. `test_audit_gz_rotation_cleanup_and_s3_deletion`: Rotación horaria `.jsonl.gz` y limpieza FinOps para S3.
12. `test_two_step_login_lookup_and_just_in_time_whatsapp_pin`: Activación Just-In-Time de PIN de 8 dígitos por WhatsApp (Evolution API).
13. `test_official_phone_admins_916220518_and_934253226`: Verificación de permisos del Super Admin (`916220518`) y Admin de Villa 3 (`934253226`).
14. `test_anchor_adds_household_angel_plasencia_and_caillou_qr_traceability`: Flujo completo donde `916220518` (`ANCHOR` en `P20`) agrega a `Angel Plasencia` (`980180219`, `HOUSEHOLD`), quien activa su PIN por WhatsApp, genera un QR para `"Amigo Caillou"` con trazabilidad completa y recibe `403 Forbidden` si intenta borrar o agregar miembros al lote.
15. `test_3nf_multi_lot_and_cross_role_pepito_and_pepita`: Escenario 3NF donde Pepito Juárez es dueño de `P20` y `Q02`, y Pepita Reina es `ANCHOR` de `Q03` y `HOUSEHOLD` de `P20`.
16. `test_visitor_self_registration_for_bakery_and_daily_log`: Onboarding VISITOR sin exponer teléfonos, bloqueo de suplantación/re-registro (`409`), visitante sin acceso a lotes ajenos ni bitácora (`403`), verificación en garita, bitácora idéntica para Garita y Admin, y aislamiento VC2/VC3.
17. `test_single_global_phone_password_across_multiple_villas`: Experiencia de contraseña única entre `vc3.lasvillas.club` (`P20`) y `vc2.lasvillas.club` (`M20`).

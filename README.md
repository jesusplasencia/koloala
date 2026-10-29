# KOLOALA (Kode. Love. Lines.)
## A Line Away Of Loving Outstanding Kode (KLL)

---

## 🚀 Quickstart (Arranque Rápido y Testing)

Asumiendo que ya cuentas con **Python 3.12+** y **`uv`** instalados en tu máquina:

```powershell
# 1. Sincronizar dependencias (incluyendo entorno virtual .venv y herramientas de test)
uv sync

# 2. Levantar el servidor de desarrollo con recarga en caliente (Hot-Reload)
uv run uvicorn app.main:app --reload

# 3. Ejecutar la suite completa de pruebas automatizadas (Pytest)
uv run pytest -v
```

Una vez encendido el servidor local en `http://127.0.0.1:8000`:
- **Documentación Interactiva (Swagger UI):** `http://127.0.0.1:8000/docs`
- **Health Check (Liveness Probe para AWS ALB/ECS):** `http://127.0.0.1:8000/healthz/liveness`
- **Flujos HTTP Ejecutables:** Abre [`requests/api.http`](requests/api.http) en VS Code y haz clic en `Send Request` sobre cada paso.

---

## 🛠️ Herramientas Requeridas en el Sistema del Desarrollador

| Herramienta | Versión / ID | Propósito en el Proyecto |
| :--- | :--- | :--- |
| **Python** | `>= 3.12` | Runtime principal tipado moderno (`StrEnum`, `typing.Protocol`, `Annotated`). |
| **uv** (Astral) | Latest | Gestor ultrarrápido de paquetes, entornos virtuales (`.venv`) y lockfile (`uv.lock`). |
| **VS Code** | Latest | IDE recomendado con configuración lista en `.vscode/settings.json`. |
| **Ruff** (VS Code Ext) | `charliermarsh.ruff` | Linter y formateador en tiempo real al guardar (`formatOnSave`). |
| **Pylance** (VS Code Ext) | `ms-python.vscode-pylance` | Chequeo estático de tipos e inferencia de protocolos. |
| **REST Client** (VS Code Ext) | `humao.rest-client` | Ejecución encadenada de peticiones HTTP desde [`requests/api.http`](requests/api.http). |
| **Git** | `>= 2.40` | Control de versiones. |
| **Docker Desktop** | Latest *(Próxima fase)* | Contenerización local de PostgreSQL y pruebas de imagen para AWS ECS Fargate. |

---

## 🏘️ Presentación del Proyecto: Motor Multi-Villa de Control de Accesos (`lasvillas.club`)

**Koloala** es el motor backend de alta velocidad y grado de producción diseñado para digitalizar, asegurar y agilizar el control de acceso vehicular y peatonal en las garitas de vigilancia de condominios residenciales (iniciando con **Villa Club 3, Carabayllo** bajo `vc3.lasvillas.club` y escalable a `vc1`, `vc2`, `vc4`, etc.).

### ¿Qué problema real resuelve?
Tradicionalmente, el ingreso de un taxi, un delivery (Rappi, PedidosYa, couriers) o una visita familiar requería llamadas telefónicas a una garita saturada, validación manual en cuadernos de papel y discusiones con propietarios en mora de mantenimiento.

**Koloala** transforma la experiencia en un flujo **SaaS Multi-Tenant de fricción cero**:
1. **Privilegio Automatizado para el Vecino al Día (1 Toque):**
   - El propietario inicia sesión una sola vez en su portal (`vc3.lasvillas.club`) y obtiene una sesión válida por **30 días** (pensado para ser amigable incluso con adultos mayores, sin vencimiento forzado de su clave inicial).
   - Para autorizar un ingreso solo elige **`DELIVERY`**, **`TAXI`** o **`GUEST`** (y opcionalmente una nota breve). El sistema toma automáticamente su Villa y Lote (`VC3 + B14`) desde su token JWT, haciendo imposible suplantar a otro vecino, y le entrega un **código único de 6 caracteres / QR** con tiempo de vida dinámico (**45 min** para Delivery, **30 min** para Taxi, **8 horas** para Visitas).
   - Si el lote registra deuda de mantenimiento (`is_maintenance_up_to_date = False`), el sistema bloquea la generación automática (`403 Forbidden`) e indica que el residente debe acercarse personalmente a abrir la reja.
2. **Doble Verificación Forense en Garita (Closure con DNI + Placa):**
   - El visitante o conductor presenta el código/QR en la garita.
   - El vigilante valida el código ingresando obligatoriamente el **DNI** del visitante y la **Placa del vehículo** (obligatoria para todo Taxi/Delivery; opcional para visitas a pie).
   - Si el vigilante olvida digitar la placa de un vehículo, el sistema **no quema el pase**, permitiéndole completar la placa y autorizar el ingreso en segundos. Una vez autorizado, el código de un solo uso queda **quemado (`USED`)**.
3. **Gestión para la Junta Directiva / Administración de cada Villa:**
   - **Edición en tiempo real (`PATCH /api/v1/lots/{lot_id}/maintenance`):** Habilita o pasa a mora un lote al instante cuando el vecino regulariza su cuota mensual.
   - **Onboarding y Cierre Mensual Masivo (`PUT /api/v1/lots/bulk`):** Sincroniza el padrón de lotes con sus correos electrónicos y autogenera las credenciales iniciales (`VC3-P20-XXXX`) listas para enviar por WhatsApp o Email.

---

## 🛡️ Seguridad de Grado Bancario y Auditoría Forense (FinOps)

- **Aislamiento Multi-Villa por Llave Compuesta `(villa_id, lot_id)`:**
  - Un solo despliegue en **AWS ECS Fargate** atiende múltiples etapas (`vc1.lasvillas.club`, `vc2.lasvillas.club`, `vc3.lasvillas.club`). El lote `("VC3", "B14")`, sus guardias y su Junta Directiva están criptográficamente aislados de `("VC2", "B14")`.
- **Defensa Activa contra Fuerza Bruta y *Password Spraying* con Correlación de Vecino:**
  - Si una dirección IP acumula **3 intentos fallidos de inicio de sesión** (ya sea atacando a un mismo lote o probando una clave común contra varios lotes como `B14`, `U28`, `P20`), **se bloquea exclusivamente la IP atacante** sin bloquearle la cuenta a la víctima inocente.
  - Además, el motor cruza automáticamente la IP bloqueada contra el historial de inicios de sesión exitosos previos de la Villa, revelando al Administrador qué lote dentro del condominio originó el ataque (`suspected_lots_from_previous_logins`).
- **Audit Logging Incremental en `.jsonl` con Rotación Horaria a S3 Glacier:**
  - Cada operación (`AUTH_LOGIN_SUCCESS`, `PASS_CREATED`, `GATE_VERIFY_ALLOWED` con DNI y Placa, `SECURITY_IP_BLOCKED_BRUTE_FORCE`, etc.) se escribe en tiempo real en `logs/audit.jsonl` con hora UTC, hora exacta de **Lima (`America/Lima`)**, IP real detrás del Load Balancer (`X-Forwarded-For`) y `User-Agent`.
  - Cada hora el log rota y se comprime automáticamente en `.gz`, listo para archivarse en **Amazon S3 (Glacier / Standard-IA)** por centavos de dólar al año sin inflar el almacenamiento de PostgreSQL ni pagar costos excesivos de ingesta en CloudWatch.

---

## 📂 Estructura del Proyecto (Clean Architecture)

```text
D:\Projects\koloala\
├── app/
│   ├── main.py                  # Entrypoint FastAPI + Routers + /healthz/liveness
│   ├── api/v1/
│   │   ├── auth.py              # Login Multi-Villa, Cambio de Clave y Reporte de IPs Bloqueadas
│   │   ├── passes.py            # Creación de Pase (1 toque) y Verificación en Garita (DNI + Placa)
│   │   └── lots.py              # Administración de Lotes y Carga Masiva (Bulk Upsert)
│   ├── core/
│   │   ├── config.py            # Pydantic Settings (TTLs, JWT 30 días, límites de seguridad)
│   │   ├── security.py          # Hashing PBKDF2, JWT HS256, detector de Villa y Guardias de Rol
│   │   └── audit.py             # Logger forense JSONL con rotación horaria y compresión .gz
│   ├── models/
│   │   ├── user.py              # Entidad User (RESIDENT, GUARD, ADMIN) y BlockedIPReport
│   │   ├── lot.py               # Entidad PropertyLot con llave compuesta (villa_id, lot_id)
│   │   └── access.py            # GuestPass y contratos HTTP de garita
│   ├── repositories/
│   │   ├── users_repo.py        # Repositorio de Usuarios + Radar Anti-Password Spraying por IP
│   │   ├── lots_repo.py         # Repositorio de Lotes sectorizado por Villa
│   │   └── access_repo.py       # Repositorio de Pases sectorizado por Villa
│   └── services/
│       ├── access_service.py    # Reglas de negocio de Garita, TTLs y validación de Placa/DNI
│       └── lots_service.py      # Orquestación de mantenimiento y autogeneración de cuentas
├── requests/
│   └── api.http                 # Flujo completo ejecutable en VS Code (Login -> Pase -> Garita -> Admin)
├── test/
│   └── test_db.py               # 9 pruebas de integración (Happy Path, Morosidad, Placa, Spraying, Multi-Villa)
├── pyproject.toml
└── README.md
```

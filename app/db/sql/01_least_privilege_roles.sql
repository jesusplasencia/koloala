-- =============================================================================
-- KOLOALA (lasvillas.club) - Principio de Menor Privilegio (Least Privilege DB)
-- Compatible con PostgreSQL 16 (Docker Local, Supabase y AWS RDS)
-- =============================================================================
-- ¿Por qué existe este script?
-- 1. El usuario 'postgres' (migration_database_url) es el único que ejecuta Alembic
--    para crear o modificar tablas (CREATE / ALTER / DROP TABLE).
-- 2. El contenedor de FastAPI en ECS Fargate se conecta como 'koloala_runtime'
--    (database_url), el cual TIENE PROHIBIDO crear/borrar tablas o hacer DELETE
--    sobre lotes, usuarios o pases. Si un atacante comprometiera el contenedor,
--    PostgreSQL bloqueará cualquier intento de destruir la base de datos.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'koloala_runtime') THEN
        CREATE ROLE koloala_runtime WITH LOGIN PASSWORD 'koloala_runtime_secret';
    END IF;
END
$$;

-- 1. Revocar permisos de creación de objetos (DDL) en el esquema público
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM koloala_runtime;

-- 2. Otorgar conexión y uso de esquema
GRANT CONNECT ON DATABASE koloala TO koloala_runtime;
GRANT USAGE ON SCHEMA public TO koloala_runtime;

-- 3. Permisos automáticos para las tablas que cree el usuario 'postgres' con Alembic:
--    Por defecto damos SELECT, INSERT, UPDATE (sin DELETE ni TRUNCATE ni DROP)
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO koloala_runtime;

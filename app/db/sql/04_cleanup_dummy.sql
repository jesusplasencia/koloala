-- =============================================================================
-- Limpieza del CSV de prueba (app/db/sql/dummy_padron.csv).
-- Ejecutar en el SQL Editor de Supabase como `postgres`.
-- Requiere la función koloala_delete_person (app/db/sql/03_delete_people.sql).
-- Solo toca celulares 900100xxx y lotes Z01..Z20: nadie más se ve afectado.
-- =============================================================================

-- 1) Ver qué se va a borrar
SELECT phone, full_name FROM people WHERE phone LIKE '900100%' ORDER BY phone;
SELECT lot_id FROM property_lots WHERE villa_id = 'VC3' AND lot_id ~ '^Z[0-9]{2}$' ORDER BY lot_id;

-- 2) Borrar las personas de prueba (y sus membresías, PINs e ingresos)
SELECT koloala_delete_person(phone) FROM people WHERE phone LIKE '900100%';

-- 3) Borrar los lotes de prueba (ya sin integrantes)
DELETE FROM lot_memberships WHERE villa_id = 'VC3' AND lot_id ~ '^Z[0-9]{2}$';
DELETE FROM property_lots   WHERE villa_id = 'VC3' AND lot_id ~ '^Z[0-9]{2}$';

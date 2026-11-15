-- =============================================================================
-- Helper (solo desarrollo / administración): borrar personas de forma segura.
-- Ejecutar en el SQL Editor de Supabase como `postgres`. La app (koloala_runtime)
-- nunca puede usar esta función: no tiene DELETE ni permiso de EXECUTE.
--
-- Protegidos (no se pueden borrar):
--   916220518  Jesús Plasencia (SUPER_ADMIN)
--   934253226  Administrador Villa Club 3
--   900000001  Vigilante de Prueba (garita ficticia)
-- =============================================================================

CREATE OR REPLACE FUNCTION koloala_delete_person(p_phone text)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_id uuid;
    v_name text;
BEGIN
    IF p_phone IN ('916220518', '934253226', '900000001') THEN
        RETURN 'PROTEGIDO, no se borró: ' || p_phone;
    END IF;

    SELECT id, full_name INTO v_id, v_name FROM people WHERE phone = p_phone;
    IF v_id IS NULL THEN
        RETURN 'No existe: ' || p_phone;
    END IF;

    -- Referencias que se limpian (se conserva el historial de visitas sin vínculo a la persona)
    UPDATE visit_passes    SET host_person_id = NULL     WHERE host_person_id = v_id;
    UPDATE lot_memberships SET added_by_person_id = NULL WHERE added_by_person_id = v_id;

    -- Registros que dependen de la persona
    DELETE FROM pin_requests    WHERE person_id = v_id;
    DELETE FROM entry_events    WHERE person_id = v_id;
    DELETE FROM lot_memberships WHERE person_id = v_id;
    DELETE FROM staff_roles     WHERE person_id = v_id;
    DELETE FROM people          WHERE id = v_id;

    RETURN 'Borrado: ' || p_phone || ' (' || coalesce(v_name, 'sin nombre') || ')';
END;
$$;

REVOKE EXECUTE ON FUNCTION koloala_delete_person(text) FROM PUBLIC;


-- -----------------------------------------------------------------------------
-- USO
-- -----------------------------------------------------------------------------

-- 1) Borrar una persona por celular:
--    SELECT koloala_delete_person('980180219');

-- 2) Borrar varias:
--    SELECT koloala_delete_person(p) FROM unnest(ARRAY['980180219', '955000111']) AS p;

-- 3) Ver primero quiénes NO tienen DNI (sin contar a los protegidos):
--    SELECT phone, full_name FROM people
--    WHERE document_id IS NULL AND phone NOT IN ('916220518', '934253226', '900000001');

-- 4) Borrar a todos los que no tienen DNI (sin contar a los protegidos):
--    SELECT koloala_delete_person(phone) FROM people
--    WHERE document_id IS NULL AND phone NOT IN ('916220518', '934253226', '900000001');

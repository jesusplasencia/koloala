BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001_initial_schema

CREATE TABLE people (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    phone VARCHAR(30) NOT NULL, 
    full_name VARCHAR(100), 
    document_id VARCHAR(20), 
    password_hash VARCHAR(255), 
    is_super_admin BOOLEAN DEFAULT false NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    token_version INTEGER DEFAULT '0' NOT NULL, 
    access_reset_at TIMESTAMP WITH TIME ZONE, 
    last_login_at TIMESTAMP WITH TIME ZONE, 
    last_login_ip VARCHAR(45), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_people_phone ON people (phone);

CREATE TABLE staff_roles (
    villa_id VARCHAR(15) NOT NULL, 
    person_id UUID NOT NULL, 
    role VARCHAR(10) NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (villa_id, person_id), 
    FOREIGN KEY(person_id) REFERENCES people (id)
);

CREATE TABLE property_lots (
    villa_id VARCHAR(15) NOT NULL, 
    lot_id VARCHAR(15) NOT NULL, 
    is_maintenance_up_to_date BOOLEAN DEFAULT true NOT NULL, 
    lot_type VARCHAR(20) DEFAULT 'LOT' NOT NULL, 
    display_name VARCHAR(100), 
    is_public BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (villa_id, lot_id)
);

CREATE TABLE lot_memberships (
    villa_id VARCHAR(15) NOT NULL, 
    lot_id VARCHAR(15) NOT NULL, 
    person_id UUID NOT NULL, 
    membership_role VARCHAR(20) DEFAULT 'ANCHOR' NOT NULL, 
    added_by_person_id UUID, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (villa_id, lot_id, person_id), 
    CONSTRAINT fk_lot_memberships_property_lot FOREIGN KEY(villa_id, lot_id) REFERENCES property_lots (villa_id, lot_id), 
    FOREIGN KEY(person_id) REFERENCES people (id), 
    FOREIGN KEY(added_by_person_id) REFERENCES people (id)
);

CREATE INDEX ix_lot_memberships_person_id ON lot_memberships (person_id);

CREATE TABLE ip_security_records (
    villa_id VARCHAR(15) NOT NULL, 
    ip_address VARCHAR(45) NOT NULL, 
    failed_attempts INTEGER DEFAULT '0' NOT NULL, 
    is_blocked BOOLEAN DEFAULT false NOT NULL, 
    attempted_usernames JSON NOT NULL, 
    known_successful_users JSON NOT NULL, 
    blocked_at TIMESTAMP WITH TIME ZONE, 
    last_user_agent VARCHAR(255), 
    PRIMARY KEY (villa_id, ip_address), 
    CONSTRAINT uq_villa_ip UNIQUE (villa_id, ip_address)
);

CREATE TABLE visit_passes (
    villa_id VARCHAR(15) NOT NULL, 
    token VARCHAR(64) NOT NULL, 
    short_code VARCHAR(10) NOT NULL, 
    origin VARCHAR(20) NOT NULL, 
    kind VARCHAR(15) DEFAULT 'GUEST' NOT NULL, 
    destination_lot_id VARCHAR(15) NOT NULL, 
    destination_type VARCHAR(20) NOT NULL, 
    destination_label VARCHAR(100) NOT NULL, 
    host_person_id UUID, 
    host_name VARCHAR(100), 
    host_role VARCHAR(20), 
    party_size_expected INTEGER DEFAULT '1' NOT NULL, 
    note VARCHAR(120), 
    vehicle_plate VARCHAR(20), 
    plate_hmac VARCHAR(64), 
    status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    valid_from TIMESTAMP WITH TIME ZONE NOT NULL, 
    valid_until TIMESTAMP WITH TIME ZONE NOT NULL, 
    checked_in_at TIMESTAMP WITH TIME ZONE, 
    checked_in_by_guard VARCHAR(30), 
    shadowed_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (villa_id, token), 
    CONSTRAINT uq_visit_passes_short_code UNIQUE (villa_id, short_code), 
    FOREIGN KEY(host_person_id) REFERENCES people (id)
);

CREATE INDEX ix_visit_passes_destination_lot_id ON visit_passes (destination_lot_id);

CREATE INDEX ix_visit_passes_plate_hmac ON visit_passes (plate_hmac);

CREATE INDEX ix_visit_passes_checked_in_at ON visit_passes (checked_in_at);

CREATE INDEX ix_visit_passes_villa_created ON visit_passes (villa_id, created_at);

CREATE TABLE pass_attendees (
    villa_id VARCHAR(15) NOT NULL, 
    token VARCHAR(64) NOT NULL, 
    attendee_no INTEGER NOT NULL, 
    first_names VARCHAR(80) NOT NULL, 
    last_names VARCHAR(80), 
    doc_type VARCHAR(15) DEFAULT 'NINGUNO' NOT NULL, 
    doc_number VARCHAR(20), 
    doc_hmac VARCHAR(64), 
    doc_last2 VARCHAR(2), 
    doc_source VARCHAR(10), 
    verification VARCHAR(15), 
    vouched_by_no INTEGER, 
    checked_in_at TIMESTAMP WITH TIME ZONE, 
    checked_in_by_guard VARCHAR(30), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    shadowed_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (villa_id, token, attendee_no), 
    CONSTRAINT fk_pass_attendees_visit_pass FOREIGN KEY(villa_id, token) REFERENCES visit_passes (villa_id, token)
);

CREATE INDEX ix_pass_attendees_doc_hmac ON pass_attendees (doc_hmac);

CREATE TABLE entry_events (
    id SERIAL NOT NULL, 
    villa_id VARCHAR(15) NOT NULL, 
    at TIMESTAMP WITH TIME ZONE NOT NULL, 
    person_id UUID NOT NULL, 
    person_name VARCHAR(100) NOT NULL, 
    lots JSON NOT NULL, 
    method VARCHAR(10) NOT NULL, 
    guard VARCHAR(30) NOT NULL, 
    vehicle_plate VARCHAR(20), 
    plate_hmac VARCHAR(64), 
    PRIMARY KEY (id), 
    FOREIGN KEY(person_id) REFERENCES people (id)
);

CREATE INDEX ix_entry_events_villa_id ON entry_events (villa_id);

CREATE INDEX ix_entry_events_at ON entry_events (at);

CREATE INDEX ix_entry_events_person_id ON entry_events (person_id);

CREATE INDEX ix_entry_events_plate_hmac ON entry_events (plate_hmac);

CREATE TABLE data_access_log (
    id SERIAL NOT NULL, 
    villa_id VARCHAR(15) NOT NULL, 
    actor VARCHAR(30) NOT NULL, 
    actor_role VARCHAR(20) NOT NULL, 
    action VARCHAR(40) NOT NULL, 
    filters JSON NOT NULL, 
    result_count INTEGER DEFAULT '0' NOT NULL, 
    client_ip VARCHAR(45), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_data_access_log_villa_id ON data_access_log (villa_id);

CREATE TABLE pin_requests (
    id SERIAL NOT NULL, 
    villa_id VARCHAR(15) NOT NULL, 
    person_id UUID NOT NULL, 
    client_ip VARCHAR(45), 
    ok BOOLEAN DEFAULT false NOT NULL, 
    requested_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(person_id) REFERENCES people (id)
);

CREATE INDEX ix_pin_requests_person_id ON pin_requests (person_id);

CREATE INDEX ix_pin_requests_requested_at ON pin_requests (requested_at);

DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'koloala_runtime') THEN
                GRANT SELECT, INSERT, UPDATE ON
                    people, staff_roles, property_lots, lot_memberships, ip_security_records,
                    visit_passes, pass_attendees
                TO koloala_runtime;
                GRANT SELECT, INSERT ON entry_events, data_access_log, pin_requests TO koloala_runtime;
                REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM koloala_runtime;
                REVOKE UPDATE ON entry_events, data_access_log, pin_requests FROM koloala_runtime;
                GRANT USAGE, SELECT ON SEQUENCE
                    entry_events_id_seq, data_access_log_id_seq, pin_requests_id_seq
                TO koloala_runtime;
            END IF;
        END
        $$;;

INSERT INTO alembic_version (version_num) VALUES ('0001_initial_schema') RETURNING alembic_version.version_num;

COMMIT;


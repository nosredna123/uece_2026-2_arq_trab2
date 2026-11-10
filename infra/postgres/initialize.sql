\set ON_ERROR_STOP on
\getenv event_database_url EVENT_DATABASE_URL
\getenv event_database_user EVENT_DATABASE_USER
\getenv event_database_password EVENT_DATABASE_PASSWORD
\getenv event_migration_user EVENT_MIGRATION_USER
\getenv event_migration_password EVENT_MIGRATION_PASSWORD
\getenv registration_database_url REGISTRATION_DATABASE_URL
\getenv registration_database_user REGISTRATION_DATABASE_USER
\getenv registration_database_password REGISTRATION_DATABASE_PASSWORD
\getenv registration_migration_user REGISTRATION_MIGRATION_USER
\getenv registration_migration_password REGISTRATION_MIGRATION_PASSWORD
\getenv attendance_database_url ATTENDANCE_DATABASE_URL
\getenv attendance_migration_database_url ATTENDANCE_MIGRATION_DATABASE_URL
\getenv reporting_database_url REPORTING_DATABASE_URL
\getenv reporting_migration_database_url REPORTING_MIGRATION_DATABASE_URL

CREATE FUNCTION pg_temp.url_decode(value text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $function$
DECLARE
    decoded bytea := ''::bytea;
    cursor_position integer := 1;
    current_character text;
BEGIN
    WHILE cursor_position <= char_length(value) LOOP
        current_character := substr(value, cursor_position, 1);
        IF current_character = '%' THEN
            IF cursor_position + 2 > char_length(value)
                OR substr(value, cursor_position + 1, 2) !~ '^[0-9A-Fa-f]{2}$'
            THEN
                RAISE EXCEPTION 'Invalid percent escape in database URL credential';
            END IF;
            decoded := decoded || decode(
                substr(value, cursor_position + 1, 2), 'hex'
            );
            cursor_position := cursor_position + 3;
        ELSE
            decoded := decoded || convert_to(current_character, 'UTF8');
            cursor_position := cursor_position + 1;
        END IF;
    END LOOP;
    RETURN convert_from(decoded, 'UTF8');
END
$function$;

SELECT pg_temp.url_decode(
    substring(:'attendance_database_url' FROM '^[^:]+://([^:]+):')
) AS attendance_database_user \gset
SELECT pg_temp.url_decode(
    substring(:'attendance_migration_database_url' FROM '^[^:]+://([^:]+):')
) AS attendance_migration_user \gset
SELECT pg_temp.url_decode(
    substring(:'reporting_database_url' FROM '^[^:]+://([^:]+):')
) AS reporting_database_user \gset
SELECT pg_temp.url_decode(
    substring(:'reporting_migration_database_url' FROM '^[^:]+://([^:]+):')
) AS reporting_migration_user \gset

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'event_database_user',
    :'event_database_password'
)\gexec
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'event_migration_user',
    :'event_migration_password'
)\gexec
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'registration_database_user',
    :'registration_database_password'
)\gexec
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'registration_migration_user',
    :'registration_migration_password'
)\gexec
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    pg_temp.url_decode(substring(:'attendance_database_url' FROM '^[^:]+://([^:]+):')),
    pg_temp.url_decode(substring(:'attendance_database_url' FROM '^[^:]+://[^:]+:(.*)@[^@]+$'))
)\gexec
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    pg_temp.url_decode(substring(:'attendance_migration_database_url' FROM '^[^:]+://([^:]+):')),
    pg_temp.url_decode(substring(:'attendance_migration_database_url' FROM '^[^:]+://[^:]+:(.*)@[^@]+$'))
)\gexec
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    pg_temp.url_decode(substring(:'reporting_database_url' FROM '^[^:]+://([^:]+):')),
    pg_temp.url_decode(substring(:'reporting_database_url' FROM '^[^:]+://[^:]+:(.*)@[^@]+$'))
)\gexec
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    pg_temp.url_decode(substring(:'reporting_migration_database_url' FROM '^[^:]+://([^:]+):')),
    pg_temp.url_decode(substring(:'reporting_migration_database_url' FROM '^[^:]+://[^:]+:(.*)@[^@]+$'))
)\gexec

SELECT format('CREATE DATABASE %I OWNER %I', 'event_management', :'event_migration_user')\gexec
SELECT format('CREATE DATABASE %I OWNER %I', 'registration', :'registration_migration_user')\gexec
SELECT format(
    'CREATE DATABASE %I OWNER %I',
    'attendance_certification',
    pg_temp.url_decode(substring(:'attendance_migration_database_url' FROM '^[^:]+://([^:]+):'))
)\gexec
SELECT format(
    'CREATE DATABASE %I OWNER %I',
    'reporting',
    pg_temp.url_decode(substring(:'reporting_migration_database_url' FROM '^[^:]+://([^:]+):'))
)\gexec

\connect event_management
REVOKE ALL ON DATABASE event_management FROM PUBLIC;
GRANT CONNECT ON DATABASE event_management TO event_runtime;
GRANT USAGE ON SCHEMA public TO event_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE event_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO event_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE event_migration IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO event_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE event_migration IN SCHEMA public
    GRANT USAGE ON TYPES TO event_runtime;

\connect registration
REVOKE ALL ON DATABASE registration FROM PUBLIC;
GRANT CONNECT ON DATABASE registration TO registration_runtime;
GRANT USAGE ON SCHEMA public TO registration_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE registration_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO registration_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE registration_migration IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO registration_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE registration_migration IN SCHEMA public
    GRANT USAGE ON TYPES TO registration_runtime;

\connect attendance_certification
REVOKE ALL ON DATABASE attendance_certification FROM PUBLIC;
GRANT CONNECT ON DATABASE attendance_certification TO
    :"attendance_database_user";
GRANT USAGE ON SCHEMA public TO :"attendance_database_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"attendance_migration_user"
    IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
    TO :"attendance_database_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"attendance_migration_user"
    IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES
    TO :"attendance_database_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"attendance_migration_user"
    IN SCHEMA public
    GRANT USAGE ON TYPES TO :"attendance_database_user";

\connect reporting
REVOKE ALL ON DATABASE reporting FROM PUBLIC;
GRANT CONNECT ON DATABASE reporting TO :"reporting_database_user";
GRANT USAGE ON SCHEMA public TO :"reporting_database_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"reporting_migration_user"
    IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
    TO :"reporting_database_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"reporting_migration_user"
    IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES
    TO :"reporting_database_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"reporting_migration_user"
    IN SCHEMA public
    GRANT USAGE ON TYPES TO :"reporting_database_user";

-- Administrator preparation after the dormant role foundation (#520).
-- No legacy deployment role or credential is altered; reconnect after applying.
DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(518099);
    ALTER ROLE aqs_api SET idle_in_transaction_session_timeout = '60s';
    ALTER ROLE aqs_api SET statement_timeout = '5min';
    ALTER ROLE aqs_worker SET idle_in_transaction_session_timeout = '2h';
    ALTER ROLE aqs_worker SET statement_timeout = '30min';
    ALTER ROLE aqs_bi SET idle_in_transaction_session_timeout = '5min';
    ALTER ROLE aqs_bi SET statement_timeout = '10s';
    ALTER ROLE aqs_migrator SET idle_in_transaction_session_timeout = '0';
    ALTER ROLE aqs_migrator SET statement_timeout = '0';
    ALTER ROLE aqs_owner SET idle_in_transaction_session_timeout = '0';
    ALTER ROLE aqs_owner SET statement_timeout = '0';
END $$;

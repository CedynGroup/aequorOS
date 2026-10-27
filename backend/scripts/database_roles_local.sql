-- Disposable local development only; production credentials come from Eric.
ALTER ROLE aqs_migrator PASSWORD 'aqs_migrator';
ALTER ROLE aqs_api PASSWORD 'aqs_api';
ALTER ROLE aqs_worker PASSWORD 'aqs_worker';
ALTER ROLE aqs_bi PASSWORD 'aqs_bi';
ALTER ROLE aqs_operator PASSWORD 'aqs_operator';
ALTER ROLE aqs_audit_verifier PASSWORD 'aqs_audit_verifier';
DO $$ BEGIN
    EXECUTE format('GRANT CREATE ON DATABASE %I TO aqs_owner', current_database());
    EXECUTE format('GRANT aqs_owner TO %I WITH ADMIN TRUE, INHERIT TRUE, SET TRUE', current_user);
END $$;

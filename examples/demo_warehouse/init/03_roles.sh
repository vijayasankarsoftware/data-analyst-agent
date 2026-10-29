#!/bin/bash
# This is what we ASK users to do on their own databases: create a dedicated read-only
# login for the agent. We can't enforce it on their side, so phase 2 also enforces
# read-only per session and validates every SQL statement before it runs.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE analyst_ro LOGIN PASSWORD '${ANALYST_RO_PASSWORD}';

    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO analyst_ro;
    GRANT USAGE ON SCHEMA public TO analyst_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_ro;

    -- Every transaction this role opens is read-only, and no query may run longer than 5s.
    ALTER ROLE analyst_ro SET default_transaction_read_only = on;
    ALTER ROLE analyst_ro SET statement_timeout = '5s';
EOSQL

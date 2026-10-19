"""PostgreSQL privilege, append-only and row-level-security policy (DECISIONS.md D-013, D-033).

Applied by the OWNER role after every `migrate` (post_migrate signal), so it converges on every
deploy and on every test database. The runtime APP role (NOSUPERUSER, NOBYPASSRLS, owns nothing):
  * gets SELECT, INSERT, UPDATE on ordinary tables; never TRUNCATE, REFERENCES, TRIGGER or DDL;
  * gets DELETE only on SAFEPAY_DELETE_ALLOWED_TABLES (e.g. django_session);
  * gets SELECT, INSERT only on SAFEPAY_APPEND_ONLY_TABLES (ledger, postings, audit, decisions,
    receipts), which additionally carry a trigger that raises on UPDATE/DELETE for every role;
  * gets SELECT only on SAFEPAY_READ_ONLY_TABLES (django_migrations).
Every table with a `tenant_id` column gets ENABLE + FORCE ROW LEVEL SECURITY and a policy that
matches `current_setting('app.tenant_id')`; without that setting a query returns zero rows.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import connections, transaction

FORBID_MUTATION_FN = """
CREATE OR REPLACE FUNCTION safepay_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'safepay: % on append-only table % is forbidden', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'insufficient_privilege';
END
$$
"""
APPEND_ONLY_TRIGGER = "safepay_append_only"
TENANT_POLICY = "safepay_tenant_isolation"
TENANT_EXPR = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _fetch_names(cursor: Any, sql: str) -> set[str]:
    cursor.execute(sql)
    return {str(row[0]) for row in cursor.fetchall()}


def apply_db_policy(using: str = "default") -> list[str]:
    """Converge grants, triggers and RLS. Returns declared append-only tables that do not exist yet."""
    conn = connections[using]
    q = conn.ops.quote_name
    app = q(settings.SAFEPAY_DB_APP_ROLE)
    append_only = set(settings.SAFEPAY_APPEND_ONLY_TABLES)
    read_only = set(settings.SAFEPAY_READ_ONLY_TABLES)
    delete_ok = set(settings.SAFEPAY_DELETE_ALLOWED_TABLES)
    rls_exempt = set(settings.SAFEPAY_RLS_EXEMPT_TABLES)
    with transaction.atomic(using=using), conn.cursor() as cur:
        tables = _fetch_names(cur, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tenant_tables = _fetch_names(
            cur,
            "SELECT c.table_name FROM information_schema.columns c "
            "JOIN pg_tables t ON t.tablename = c.table_name AND t.schemaname = c.table_schema "
            "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id'",
        )
        cur.execute("SELECT current_database()")
        database = q(str(cur.fetchone()[0]))

        cur.execute(f"REVOKE ALL ON DATABASE {database} FROM PUBLIC")
        cur.execute(f"GRANT CONNECT ON DATABASE {database} TO {app}")
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {app}")
        cur.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {app}")
        cur.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {app}")
        cur.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {app}")
        cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app}")
        cur.execute(FORBID_MUTATION_FN)

        for table in sorted(tables & read_only):
            cur.execute(f"REVOKE INSERT, UPDATE ON {q(table)} FROM {app}")
        for table in sorted(tables & append_only):
            cur.execute(f"REVOKE UPDATE ON {q(table)} FROM {app}")
            cur.execute(f"DROP TRIGGER IF EXISTS {APPEND_ONLY_TRIGGER} ON {q(table)}")
            cur.execute(
                f"CREATE TRIGGER {APPEND_ONLY_TRIGGER} BEFORE UPDATE OR DELETE ON {q(table)} "
                "FOR EACH ROW EXECUTE FUNCTION safepay_forbid_mutation()"
            )
        for table in sorted((tables & delete_ok) - append_only - read_only):
            cur.execute(f"GRANT DELETE ON {q(table)} TO {app}")
        for table in sorted(tenant_tables - rls_exempt):
            cur.execute(f"ALTER TABLE {q(table)} ENABLE ROW LEVEL SECURITY")
            cur.execute(f"ALTER TABLE {q(table)} FORCE ROW LEVEL SECURITY")
            cur.execute(f"DROP POLICY IF EXISTS {TENANT_POLICY} ON {q(table)}")
            cur.execute(
                f"CREATE POLICY {TENANT_POLICY} ON {q(table)} FOR ALL "
                f"USING ({TENANT_EXPR}) WITH CHECK ({TENANT_EXPR})"
            )
    return sorted(append_only - tables)

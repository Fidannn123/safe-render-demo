"""audit_event chain linkage enforced by PostgreSQL (human review: M1).

BEFORE INSERT: seq 1 must carry the genesis prev_hash (64 zeros); seq n > 1 must carry the hash of the
tenant's event n-1. Together with UNIQUE(tenant_id, seq) this rejects gaps, forks and re-linking.
The hash itself (SHA-256 over RFC 8785 JSON) is computed and re-verified in Python (apps.audit.chain).
audit_event is append-only via db_policy.py.
"""

from django.db import migrations

SQL = r"""
CREATE OR REPLACE FUNCTION audit_event_link_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE prev_h text;
BEGIN
  IF NEW.seq = 1 THEN
    IF NEW.prev_hash <> repeat('0', 64) THEN
      RAISE EXCEPTION 'safepay: audit seq 1 must link to genesis' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  ELSE
    SELECT hash INTO prev_h FROM audit_event WHERE tenant_id = NEW.tenant_id AND seq = NEW.seq - 1;
    IF prev_h IS NULL OR prev_h <> NEW.prev_hash THEN
      RAISE EXCEPTION 'safepay: audit seq % does not link to seq %', NEW.seq, NEW.seq - 1
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS audit_event_link_check ON audit_event;
CREATE TRIGGER audit_event_link_check BEFORE INSERT ON audit_event
  FOR EACH ROW EXECUTE FUNCTION audit_event_link_check();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS audit_event_link_check ON audit_event;
DROP FUNCTION IF EXISTS audit_event_link_check();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(SQL, REVERSE_SQL)]

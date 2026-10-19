"""Ledger invariants enforced by PostgreSQL (architecture.md section 2.4; human review: M1).

1. ledger_apply_posting (AFTER INSERT ON ledger_posting): the posting's account and journal entry belong
   to the posting's tenant, then upsert ledger_balance (balance += normal_sign * amount). The CHECK
   ledger_balance_non_negative_chk therefore rejects any posting that would overdraw Bank:available,
   Bank:reserved or Bank:in_transit, at INSERT time.
2. ledger_balance_guard: ledger_balance can only be written from inside that trigger
   (pg_trigger_depth() > 1); direct INSERT/UPDATE/DELETE by any role raises. Keys never change.
3. ledger_check_entry_balanced: DEFERRABLE INITIALLY DEFERRED constraint triggers on ledger_posting and
   ledger_journal_entry; at COMMIT every journal entry has >= 2 postings summing to zero per currency.
   The check binds app.tenant_id to the row's tenant while it runs so RLS cannot hide postings.
4. ledger_account_immutable: accounts are never updated or deleted.
(ledger_journal_entry and ledger_posting are append-only via db_policy.py.)
"""

from django.db import migrations

SQL = r"""
CREATE OR REPLACE FUNCTION ledger_apply_posting() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  acct_tenant uuid; acct_sign smallint; acct_nonneg boolean; entry_tenant uuid;
BEGIN
  SELECT tenant_id, normal_sign, non_negative INTO acct_tenant, acct_sign, acct_nonneg
    FROM ledger_account WHERE id = NEW.account_id;
  IF acct_tenant IS NULL OR acct_tenant <> NEW.tenant_id THEN
    RAISE EXCEPTION 'safepay: posting account % is not in tenant %', NEW.account_id, NEW.tenant_id
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  SELECT tenant_id INTO entry_tenant FROM ledger_journal_entry WHERE id = NEW.entry_id;
  IF entry_tenant IS NULL OR entry_tenant <> NEW.tenant_id THEN
    RAISE EXCEPTION 'safepay: journal entry % is not in tenant %', NEW.entry_id, NEW.tenant_id
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  -- UPDATE first: INSERT ... ON CONFLICT would CHECK the proposed (delta-only) row and fail spuriously.
  UPDATE ledger_balance SET balance = balance + acct_sign * NEW.amount_minor, updated_at = now()
    WHERE account_id = NEW.account_id AND currency = NEW.currency;
  IF NOT FOUND THEN
    INSERT INTO ledger_balance (tenant_id, account_id, currency, balance, non_negative, updated_at)
    VALUES (NEW.tenant_id, NEW.account_id, NEW.currency, acct_sign * NEW.amount_minor, acct_nonneg, now())
    ON CONFLICT (account_id, currency) DO NOTHING;
    IF NOT FOUND THEN  -- a concurrent transaction created the row first
      UPDATE ledger_balance SET balance = balance + acct_sign * NEW.amount_minor, updated_at = now()
        WHERE account_id = NEW.account_id AND currency = NEW.currency;
    END IF;
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS ledger_apply_posting ON ledger_posting;
CREATE TRIGGER ledger_apply_posting AFTER INSERT ON ledger_posting
  FOR EACH ROW EXECUTE FUNCTION ledger_apply_posting();

CREATE OR REPLACE FUNCTION ledger_balance_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF pg_trigger_depth() < 2 THEN
    RAISE EXCEPTION 'safepay: % on ledger_balance is only allowed from the posting trigger', TG_OP
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' AND (NEW.tenant_id, NEW.account_id, NEW.currency, NEW.non_negative)
       IS DISTINCT FROM (OLD.tenant_id, OLD.account_id, OLD.currency, OLD.non_negative) THEN
    RAISE EXCEPTION 'safepay: ledger_balance keys are immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'safepay: DELETE on ledger_balance is forbidden' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS ledger_balance_guard ON ledger_balance;
CREATE TRIGGER ledger_balance_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_balance
  FOR EACH ROW EXECUTE FUNCTION ledger_balance_guard();

CREATE OR REPLACE FUNCTION ledger_check_entry_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  eid uuid; prev text; n bigint; bad_ccy text; bad_sum numeric;
BEGIN
  IF TG_TABLE_NAME = 'ledger_posting' THEN eid := NEW.entry_id; ELSE eid := NEW.id; END IF;
  prev := COALESCE(current_setting('app.tenant_id', true), '');
  PERFORM set_config('app.tenant_id', NEW.tenant_id::text, true);
  SELECT count(*) INTO n FROM ledger_posting WHERE entry_id = eid;
  SELECT currency, sum(amount_minor) INTO bad_ccy, bad_sum FROM ledger_posting
    WHERE entry_id = eid GROUP BY currency HAVING sum(amount_minor) <> 0 LIMIT 1;
  PERFORM set_config('app.tenant_id', prev, true);
  IF n < 2 THEN
    RAISE EXCEPTION 'safepay: journal entry % has % postings (at least 2 required)', eid, n
      USING ERRCODE = 'check_violation';
  END IF;
  IF bad_ccy IS NOT NULL THEN
    RAISE EXCEPTION 'safepay: journal entry % does not balance in % (sum %)', eid, bad_ccy, bad_sum
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS ledger_posting_balanced ON ledger_posting;
CREATE CONSTRAINT TRIGGER ledger_posting_balanced AFTER INSERT ON ledger_posting
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_entry_balanced();
DROP TRIGGER IF EXISTS ledger_entry_balanced ON ledger_journal_entry;
CREATE CONSTRAINT TRIGGER ledger_entry_balanced AFTER INSERT ON ledger_journal_entry
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_entry_balanced();

CREATE OR REPLACE FUNCTION ledger_account_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'safepay: % on ledger_account is forbidden', TG_OP USING ERRCODE = 'insufficient_privilege';
END
$$;
DROP TRIGGER IF EXISTS ledger_account_immutable ON ledger_account;
CREATE TRIGGER ledger_account_immutable BEFORE UPDATE OR DELETE ON ledger_account
  FOR EACH ROW EXECUTE FUNCTION ledger_account_immutable();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS ledger_account_immutable ON ledger_account;
DROP TRIGGER IF EXISTS ledger_entry_balanced ON ledger_journal_entry;
DROP TRIGGER IF EXISTS ledger_posting_balanced ON ledger_posting;
DROP TRIGGER IF EXISTS ledger_balance_guard ON ledger_balance;
DROP TRIGGER IF EXISTS ledger_apply_posting ON ledger_posting;
DROP FUNCTION IF EXISTS ledger_account_immutable();
DROP FUNCTION IF EXISTS ledger_check_entry_balanced();
DROP FUNCTION IF EXISTS ledger_balance_guard();
DROP FUNCTION IF EXISTS ledger_apply_posting();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [migrations.RunSQL(SQL, REVERSE_SQL)]

"""CHECK (state IN <states of spec/state_machine.yaml>), generated from the frozen spec (human review: M1)."""

from pathlib import Path

import yaml
from django.conf import settings
from django.db import migrations

CONSTRAINT = "payments_state_in_spec_chk"


def add_check(apps, schema_editor):
    with (Path(settings.SAFEPAY_SPEC_DIR) / "state_machine.yaml").open(encoding="utf-8") as fh:
        states = sorted(yaml.safe_load(fh)["states"])
    for s in states:
        if not s.isalpha() or not s.isupper():
            raise ValueError(f"unexpected state name {s!r}")
    values = ", ".join(f"'{s}'" for s in states)
    schema_editor.execute(f"ALTER TABLE payments_payment DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    schema_editor.execute(f"ALTER TABLE payments_payment ADD CONSTRAINT {CONSTRAINT} CHECK (state IN ({values}))")


def drop_check(apps, schema_editor):
    schema_editor.execute(f"ALTER TABLE payments_payment DROP CONSTRAINT IF EXISTS {CONSTRAINT}")


class Migration(migrations.Migration):
    dependencies = [("payments", "0001_initial")]

    operations = [migrations.RunPython(add_check, drop_check)]

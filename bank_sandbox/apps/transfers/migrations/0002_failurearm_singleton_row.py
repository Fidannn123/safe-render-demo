from __future__ import annotations

from typing import Any

from django.db import migrations


def create_singleton(apps: Any, schema_editor: Any) -> None:
    FailureArm = apps.get_model("transfers", "FailureArm")  # noqa: N806
    FailureArm.objects.get_or_create(id=1)


class Migration(migrations.Migration):
    dependencies = [("transfers", "0001_initial")]

    operations = [migrations.RunPython(create_singleton, migrations.RunPython.noop)]

from __future__ import annotations

from django.urls import path

from safepay_core.api import api
from safepay_core.health import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("api/v1/", api.urls),
]

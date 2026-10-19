from __future__ import annotations

from django.urls import path

from safepay_intel.api import api
from safepay_intel.health import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("v1/", api.urls),
]

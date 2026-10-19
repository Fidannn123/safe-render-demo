from __future__ import annotations

from django.urls import path

from safepay_bank.api import admin_api, api
from safepay_bank.health import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("v1/", api.urls),
    path("admin/", admin_api.urls),
]

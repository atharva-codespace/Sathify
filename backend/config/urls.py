"""
Sathify root URL configuration.

Every module mounts its own ``urls.py`` under ``/api/v1/<module>/`` so that
route ownership matches module ownership across the team. Module routes are
commented out until that module is built, keeping the URLConf importable at
every stage of the build.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def health_check(_request):
    """Liveness probe.

    Also used by an external uptime pinger to keep the Render free instance
    awake — see docs/free-tier-constraints.md.
    """
    return JsonResponse({"status": "ok", "service": "sathify-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health-check"),
    # --- API schema (the contract the Flutter client is generated against) ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

# --- Module routes ----------------------------------------------------------
# Uncomment each line as the module lands. Keeping them listed here documents
# the intended API surface and prevents two people choosing the same prefix.
api_v1 = [
    path("auth/", include("apps.accounts.urls")),                   # Module 1
    path("societies/", include("apps.societies.urls")),             # Module 2
    path("workers/", include("apps.workers.urls")),                 # Module 3
    path("hiring/", include("apps.hiring.urls")),                   # Module 4
    path("bookings/", include("apps.bookings.urls")),               # Module 5
    path("scheduling/", include("apps.scheduling.urls")),           # Module 6
    path("attendance/", include("apps.attendance.urls")),           # Module 7
    path("payments/", include("apps.payments.urls")),               # Module 8
    path("ratings/", include("apps.ratings.urls")),                 # Module 9
    path("notifications/", include("apps.notifications.urls")),     # Module 10
    path("admin-tools/", include("apps.administration.urls")),      # Module 11
    path("ai/", include("apps.ai_services.urls")),                  # Module 12
    path("console/", include("apps.console.urls")),                 # Module 14
]

urlpatterns += [path("api/v1/", include((api_v1, "api"), namespace="v1"))]

# Serve uploaded media from the local filesystem in development only. In
# production these live in Supabase Storage and are reached via signed URLs.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

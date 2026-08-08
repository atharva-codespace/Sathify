"""Module 7 — Attendance & Gate Verification: routes (mounted at /api/v1/attendance/)."""

from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    # --- 7.1 Gate pass ------------------------------------------------------
    path("my-pass/", views.MyGatePassView.as_view(), name="my-pass"),
    path("my-pass/rotate/", views.RotateGatePassView.as_view(), name="rotate-pass"),

    # --- 7.2 / 7.4 Roster, scanning, sync -----------------------------------
    path("roster/", views.GateRosterView.as_view(), name="roster"),
    path("scan/", views.ScanView.as_view(), name="scan"),
    path("sync/", views.AttendanceSyncView.as_view(), name="sync"),

    # --- 7.2 / 7.5 / 7.6 Events ---------------------------------------------
    path("events/", views.AttendanceEventListCreateView.as_view(), name="event-list"),
    path("events/<uuid:pk>/face/", views.FaceCheckView.as_view(), name="face-check"),
    path(
        "events/<uuid:pk>/resolve/",
        views.ResolveEventView.as_view(),
        name="resolve-event",
    ),

    # --- 13.3 Tier 2: worker self check-in ----------------------------------
    path("self-checkin/", views.SelfCheckInView.as_view(), name="self-checkin"),
    # --- 13.3 tier 2.5: no guard, and no smartphone either ------------------
    path(
        "resident-scan/",
        views.ResidentScanView.as_view(),
        name="resident-scan",
    ),

    # --- 7.5 / 13.3 Tier 3: register digitisation ---------------------------
    path("registers/", views.RegisterScanListCreateView.as_view(), name="register-list"),
]

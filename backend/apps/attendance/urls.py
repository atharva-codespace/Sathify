"""Module 7 — Attendance & Gate Verification: routes (mounted at /api/v1/attendance/)."""

from django.urls import path

from . import sessions_api, views

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

    # --- 7.7 Work sessions (the household's side of the day) ----------------
    # Literal segments first: `sessions/<uuid:pk>/` would otherwise swallow
    # `sessions/today/` as a session id and 404 the worker's home screen.
    path("sessions/today/", sessions_api.TodayScreenView.as_view(), name="session-today"),
    path("sessions/start/", sessions_api.StartSessionView.as_view(), name="session-start"),
    path("sessions/<uuid:pk>/stop/", sessions_api.StopSessionView.as_view(), name="session-stop"),
    path(
        "sessions/<uuid:pk>/request-overtime/",
        sessions_api.RequestOvertimeView.as_view(),
        name="session-request-ot",
    ),
    path(
        "sessions/<uuid:pk>/approve-overtime/",
        sessions_api.ApproveOvertimeView.as_view(),
        name="session-approve-ot",
    ),
    path(
        "sessions/<uuid:pk>/confirm/",
        sessions_api.ConfirmSessionView.as_view(),
        name="session-confirm",
    ),
    path("sessions/", sessions_api.MySessionsView.as_view(), name="session-list"),
]

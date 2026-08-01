"""Module 11 — Admin, Reporting & Complaints: routes (mounted at /api/v1/admin-tools/)."""

from django.urls import path

from . import views

app_name = "administration"

urlpatterns = [
    # --- 11.1 Directory -----------------------------------------------------
    path(
        "directory/workers/",
        views.WorkerDirectoryView.as_view(),
        name="directory-workers",
    ),
    path(
        "directory/residents/",
        views.ResidentDirectoryView.as_view(),
        name="directory-residents",
    ),

    # --- 11.2 Reports -------------------------------------------------------
    # The file variants come first: `reports/<kind>/` would otherwise swallow
    # `reports/attendance/csv/` as a report named "attendance" with a stray
    # segment, and Django would never reach the export route.
    path("reports/<str:kind>/csv/", views.ReportCsvView.as_view(), name="report-csv"),
    path("reports/<str:kind>/pdf/", views.ReportPdfView.as_view(), name="report-pdf"),
    path("reports/<str:kind>/", views.ReportView.as_view(), name="report"),

    # --- 11.3 Complaints ----------------------------------------------------
    # Literal segments before the `<int:pk>` routes, so "escalate" is never
    # matched as an id.
    path(
        "complaints/escalate/",
        views.EscalateComplaintsView.as_view(),
        name="complaint-escalate",
    ),
    path(
        "complaints/<int:pk>/updates/",
        views.AddComplaintUpdateView.as_view(),
        name="complaint-update",
    ),
    path(
        "complaints/<int:pk>/start/",
        views.StartComplaintView.as_view(),
        name="complaint-start",
    ),
    path(
        "complaints/<int:pk>/close/",
        views.CloseComplaintView.as_view(),
        name="complaint-close",
    ),
    path(
        "complaints/<int:pk>/withdraw/",
        views.WithdrawComplaintView.as_view(),
        name="complaint-withdraw",
    ),
    path(
        "complaints/<int:pk>/",
        views.ComplaintDetailView.as_view(),
        name="complaint-detail",
    ),
    path("complaints/", views.ComplaintListCreateView.as_view(), name="complaint-list"),

    # --- 11.4 Analytics -----------------------------------------------------
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("unmet-demand/", views.UnmetDemandListView.as_view(), name="unmet-demand"),
]

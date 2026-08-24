"""Module 14 — Platform Operations Console: routes (mounted at /api/v1/console/)."""

from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    # --- 14.8 The console app shell -----------------------------------------
    # Mounted under the API prefix so the console is same-origin with the
    # endpoints it calls, which keeps CORS out of the picture entirely.
    path("app/", views.ConsoleAppView.as_view(), name="app"),

    # --- 14.1 Overview ------------------------------------------------------
    path("overview/", views.OverviewView.as_view(), name="overview"),
    path("billing-integrity/", views.BillingIntegrityView.as_view(), name="billing-integrity"),

    # --- 14.2 Transactions --------------------------------------------------
    # The literal segment comes first: `transactions/<receipt_number>/` would
    # otherwise swallow `transactions/reconciliation/` as a receipt named
    # "reconciliation", and the route would never be reached.
    path(
        "transactions/reconciliation/",
        views.ReconciliationView.as_view(),
        name="reconciliation",
    ),
    path(
        "transactions/<str:receipt_number>/",
        views.TransactionDetailView.as_view(),
        name="transaction-detail",
    ),
    path("transactions/", views.TransactionListView.as_view(), name="transactions"),
    path("invoices/", views.InvoiceListView.as_view(), name="invoices"),

    # --- 14.3 Activity ------------------------------------------------------
    path("activity/sessions/", views.SessionActivityView.as_view(), name="activity-sessions"),
    path("activity/access-log/", views.AccessLogView.as_view(), name="activity-access-log"),
    path(
        "activity/impersonations/",
        views.ImpersonationLogView.as_view(),
        name="activity-impersonations",
    ),

    # --- 14.4 Societies -----------------------------------------------------
    path("societies/<int:pk>/suspend/", views.SuspendSocietyView.as_view(), name="society-suspend"),
    path("societies/<int:pk>/tier/", views.ChangeTierView.as_view(), name="society-tier"),
    path("societies/<int:pk>/", views.SocietyDetailView.as_view(), name="society-detail"),
    path("societies/", views.SocietyListView.as_view(), name="societies"),

    # --- 14.5 Users ---------------------------------------------------------
    path("users/<int:pk>/reveal/", views.RevealContactView.as_view(), name="user-reveal"),
    path("users/", views.UserSearchView.as_view(), name="users"),

    # --- 14.7 Reports (Module 11.5) -----------------------------------------
    # Literal segments before `<uuid:pk>`, so "run" is never read as a job id.
    path("reports/run/", views.RunReportSweepView.as_view(), name="report-run"),
    path("reports/new/", views.CreateReportJobView.as_view(), name="report-create"),
    path(
        "reports/<uuid:pk>/retry/",
        views.RetryReportJobView.as_view(),
        name="report-retry",
    ),
    path(
        "reports/<uuid:pk>/<str:fmt>/",
        views.ReportJobDownloadView.as_view(),
        name="report-download",
    ),
    path("reports/", views.ReportJobListView.as_view(), name="reports"),

    # --- 14.6 Impersonation -------------------------------------------------
    path("impersonation/<int:pk>/end/", views.EndImpersonationView.as_view(), name="impersonation-end"),
    path("impersonation/", views.StartImpersonationView.as_view(), name="impersonation-start"),
]

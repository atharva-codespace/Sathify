"""Module 4 — Discovery & Hiring: routes (mounted at /api/v1/hiring/)."""

from django.urls import path

from . import views

app_name = "hiring"

urlpatterns = [
    # --- 4.1 / 4.2 Discovery ------------------------------------------------
    path("workers/", views.WorkerSearchView.as_view(), name="worker-search"),
    path("workers/<int:pk>/", views.WorkerDetailView.as_view(), name="worker-detail"),

    # --- 4.4 Hire requests --------------------------------------------------
    path("requests/", views.HireRequestListCreateView.as_view(), name="request-list"),
    path("requests/<int:pk>/", views.HireRequestDetailView.as_view(), name="request-detail"),
    path(
        "requests/<int:pk>/respond/",
        views.HireRequestRespondView.as_view(),
        name="request-respond",
    ),
    path(
        "requests/<int:pk>/withdraw/",
        views.HireRequestWithdrawView.as_view(),
        name="request-withdraw",
    ),

    # --- 4.5 Engagement lifecycle -------------------------------------------
    path("engagements/", views.EngagementListView.as_view(), name="engagement-list"),
    path(
        "engagements/<int:pk>/",
        views.EngagementDetailView.as_view(),
        name="engagement-detail",
    ),
    path(
        "engagements/<int:pk>/transition/",
        views.EngagementTransitionView.as_view(),
        name="engagement-transition",
    ),
]

"""Module 9 — Ratings, Reviews & Trust Score: routes (mounted at /api/v1/ratings/)."""

from django.urls import path

from . import views

app_name = "ratings"

urlpatterns = [
    # --- 9.1 Rating ---------------------------------------------------------
    # Literal segments before the list route, so they are never mistaken for it.
    path("pending/", views.PendingRatingsView.as_view(), name="pending"),
    path(
        "workers/<int:worker_id>/",
        views.WorkerRatingsView.as_view(),
        name="worker-ratings",
    ),

    # --- 9.3 Trust scores ---------------------------------------------------
    path("trust/me/", views.MyTrustScoreView.as_view(), name="my-trust"),
    path(
        "trust/workers/<int:worker_id>/",
        views.WorkerTrustScoreView.as_view(),
        name="worker-trust",
    ),
    path("trust/history/", views.TrustHistoryView.as_view(), name="trust-history"),

    # --- 9.4 Flagged reviews ------------------------------------------------
    path("flags/", views.ReviewFlagListView.as_view(), name="flag-list"),
    path("flags/<int:pk>/resolve/", views.ResolveFlagView.as_view(), name="flag-resolve"),

    # --- 9.1 Submit / list --------------------------------------------------
    path("", views.RatingListCreateView.as_view(), name="rating-list"),
]

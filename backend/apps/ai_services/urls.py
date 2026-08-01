"""Module 12 — AI Layer: routes (mounted at /api/v1/ai/)."""

from django.urls import path

from . import views

app_name = "ai_services"

urlpatterns = [
    path("status/", views.AiStatusView.as_view(), name="status"),
    path("chat/", views.ChatView.as_view(), name="chat"),
    path(
        "complaints/classify/",
        views.ClassifyComplaintView.as_view(),
        name="classify-complaint",
    ),
    path(
        "reviews/<int:worker_id>/summary/",
        views.ReviewSummaryView.as_view(),
        name="review-summary",
    ),
]

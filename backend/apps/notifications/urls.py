"""Module 10 — Notifications: routes (mounted at /api/v1/notifications/)."""

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    # Literal segments before the "<int:pk>/" route.
    path("unread-count/", views.UnreadCountView.as_view(), name="unread-count"),
    path("read-all/", views.MarkAllReadView.as_view(), name="read-all"),
    path("device/", views.DeviceTokenView.as_view(), name="device"),
    path("preferences/", views.PreferencesView.as_view(), name="preferences"),
    path("deliver-due/", views.DeliverDueView.as_view(), name="deliver-due"),

    path("", views.NotificationListView.as_view(), name="notification-list"),
    path("<int:pk>/read/", views.MarkReadView.as_view(), name="mark-read"),
]

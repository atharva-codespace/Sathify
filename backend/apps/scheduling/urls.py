"""Module 6 — Scheduling & Task Management: routes (mounted at /api/v1/scheduling/)."""

from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    # --- 6.1 Calendar -------------------------------------------------------
    path("me/today/", views.MyTodayView.as_view(), name="my-today"),
    path("me/agenda/", views.MyAgendaView.as_view(), name="my-agenda"),
    path(
        "workers/<int:worker_id>/agenda/",
        views.WorkerAgendaView.as_view(),
        name="worker-agenda",
    ),
    path("society/agenda/", views.SocietyAgendaView.as_view(), name="society-agenda"),

    # --- 6.2 Task timing ----------------------------------------------------
    path(
        "timing/<int:engagement_id>/",
        views.TaskTimingView.as_view(),
        name="task-timing",
    ),

    # --- 6.3 Conflict detection ---------------------------------------------
    path("conflicts/check/", views.ConflictCheckView.as_view(), name="conflict-check"),

    # --- 6.4 Reminders ------------------------------------------------------
    path("reminders/due/", views.DueRemindersView.as_view(), name="reminders-due"),
    path(
        "reminders/<int:pk>/delivered/",
        views.ReminderDeliveredView.as_view(),
        name="reminder-delivered",
    ),
]

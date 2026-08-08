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

    # --- 6.6 Task completion -------------------------------------------------
    path(
        "visits/complete/",
        views.MarkTaskCompleteView.as_view(),
        name="mark-task-complete",
    ),

    # --- 6.5 Urgent leave ("chutti") ----------------------------------------
    path("leave/", views.LeaveListCreateView.as_view(), name="leave-list"),
    path(
        "leave/<int:pk>/response/",
        views.LeaveResponseView.as_view(),
        name="leave-response",
    ),
    path(
        "leave/<int:pk>/candidates/",
        views.ReplacementCandidatesView.as_view(),
        name="leave-candidates",
    ),
    path(
        "leave/<int:pk>/replacement/",
        views.AssignReplacementView.as_view(),
        name="leave-replacement",
    ),
    path(
        "leave/<int:pk>/withdraw/",
        views.WithdrawLeaveView.as_view(),
        name="leave-withdraw",
    ),
]

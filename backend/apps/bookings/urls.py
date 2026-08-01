"""Module 5 — One-Day Service Booking: routes (mounted at /api/v1/bookings/)."""

from django.urls import path

from . import views

app_name = "bookings"

urlpatterns = [
    # --- 5.1 Catalogue ------------------------------------------------------
    path("categories/", views.ServiceCategoryListView.as_view(), name="category-list"),

    # --- 5.3 Availability & matching ----------------------------------------
    # Declared before the "<int:pk>/" routes so "availability" and "match" are
    # never mistaken for a booking id.
    path("availability/", views.MyAvailabilityView.as_view(), name="my-availability"),
    path(
        "availability/<int:worker_id>/",
        views.WorkerAvailabilityView.as_view(),
        name="worker-availability",
    ),
    path("match/", views.BookingMatchView.as_view(), name="match"),

    # --- 5.2 Bookings -------------------------------------------------------
    path("", views.BookingListCreateView.as_view(), name="booking-list"),
    path("<int:pk>/", views.BookingDetailView.as_view(), name="booking-detail"),

    # --- 5.4 Confirmation & cancellation ------------------------------------
    path("<int:pk>/respond/", views.BookingRespondView.as_view(), name="booking-respond"),
    path(
        "<int:pk>/cancellation-quote/",
        views.CancellationQuoteView.as_view(),
        name="cancellation-quote",
    ),
    path("<int:pk>/cancel/", views.BookingCancelView.as_view(), name="booking-cancel"),
    path(
        "<int:pk>/complete/",
        views.BookingCompleteView.as_view(),
        name="booking-complete",
    ),
]

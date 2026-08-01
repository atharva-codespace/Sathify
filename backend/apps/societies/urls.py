"""Module 2 — Society & Resident Onboarding: routes (mounted at /api/v1/societies/)."""

from django.urls import path

from . import views

app_name = "societies"

urlpatterns = [
    # --- 2.1 Society registration & lookup ----------------------------------
    # Public: a prospective resident must pick a society before they have an
    # account, so this one endpoint is deliberately unauthenticated.
    path("public/", views.PublicSocietyListView.as_view(), name="public-list"),
    path("register/", views.SocietyRegistrationView.as_view(), name="register"),
    path("me/", views.MySocietyView.as_view(), name="my-society"),

    # --- 2.5 Configuration --------------------------------------------------
    path("me/config/", views.SocietyConfigurationView.as_view(), name="config"),
    path("gates/", views.GateListCreateView.as_view(), name="gate-list"),

    # --- 2.2 Tower & flat mapping -------------------------------------------
    path("towers/", views.TowerListCreateView.as_view(), name="tower-list"),
    path("towers/bulk-flats/", views.BulkFlatCreateView.as_view(), name="bulk-flats"),
    path("flats/", views.FlatListCreateView.as_view(), name="flat-list"),

    # --- 2.3 / 2.4 Residents ------------------------------------------------
    path("residents/", views.ResidentProfileCreateView.as_view(), name="resident-create"),
    path("residents/me/", views.MyResidentProfileView.as_view(), name="resident-me"),
    path("residents/all/", views.ResidentListView.as_view(), name="resident-list"),
    path("residents/pending/", views.PendingResidentListView.as_view(), name="resident-pending"),
    path("residents/set-primary/", views.SetPrimaryResidentView.as_view(), name="set-primary"),
    path("residents/<int:pk>/decide/", views.ResidentDecisionView.as_view(), name="resident-decide"),
]

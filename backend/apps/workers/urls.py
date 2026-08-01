"""Module 3 — Worker Onboarding & KYC: routes (mounted at /api/v1/workers/)."""

from django.urls import path

from . import views

app_name = "workers"

urlpatterns = [
    path("service-types/", views.ServiceTypeListView.as_view(), name="service-types"),

    # --- 3.1 Profile --------------------------------------------------------
    path("profile/", views.MyWorkerProfileView.as_view(), name="my-profile"),

    # --- 3.2 / 3.3 / 3.4 KYC ------------------------------------------------
    path("kyc/", views.KycUploadView.as_view(), name="kyc-upload"),
    path("kyc/mine/", views.MyKycListView.as_view(), name="kyc-list"),
    path("kyc/<int:pk>/", views.MyKycDetailView.as_view(), name="kyc-detail"),
    path("kyc/<int:pk>/confirm/", views.KycConfirmView.as_view(), name="kyc-confirm"),

    # --- 3.6 Consent --------------------------------------------------------
    path("consents/", views.ConsentListCreateView.as_view(), name="consent-list"),
    path(
        "consents/<int:pk>/withdraw/",
        views.ConsentWithdrawView.as_view(),
        name="consent-withdraw",
    ),

    # --- 3.5 Admin review ---------------------------------------------------
    path("review/pending/", views.PendingWorkerListView.as_view(), name="review-pending"),
    path("review/<int:pk>/", views.WorkerReviewDetailView.as_view(), name="review-detail"),
    path(
        "review/<int:pk>/decide/",
        views.WorkerDecisionView.as_view(),
        name="review-decide",
    ),
]

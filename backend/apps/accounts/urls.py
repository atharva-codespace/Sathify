"""Module 1 — Identity & Access Management: routes (mounted at /api/v1/auth/)."""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from . import views

app_name = "accounts"

urlpatterns = [
    # --- 1.1 Registration ---------------------------------------------------
    path("register/resident/", views.ResidentRegistrationView.as_view(), name="register-resident"),
    path("register/worker/", views.WorkerRegistrationView.as_view(), name="register-worker"),
    path("register/admin/", views.SocietyAdminRegistrationView.as_view(), name="register-admin"),
    # Guards and administrators are created by an administrator, never self-registered.
    path("staff/", views.StaffCreationView.as_view(), name="staff-create"),

    # --- 1.2 JWT ------------------------------------------------------------
    path("login/", views.SathifyTokenObtainPairView.as_view(), name="login"),
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("verify/", TokenVerifyView.as_view(), name="token-verify"),
    path("logout/", views.LogoutView.as_view(), name="logout"),

    # --- Profile ------------------------------------------------------------
    path("me/", views.MeView.as_view(), name="me"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password-change"),

    # --- 1.4 OTP ------------------------------------------------------------
    path("otp/request/", views.OtpRequestView.as_view(), name="otp-request"),
    path("otp/verify/", views.OtpVerifyView.as_view(), name="otp-verify"),

    # --- 1.5 Sessions -------------------------------------------------------
    path("sessions/", views.DeviceSessionListView.as_view(), name="session-list"),
    path("sessions/<int:pk>/", views.DeviceSessionRevokeView.as_view(), name="session-revoke"),
]

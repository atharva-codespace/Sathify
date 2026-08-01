"""
Module 1.3 — RBAC permission classes and society isolation.

These tests exercise the permission classes directly against throwaway views
rather than through real module endpoints, so they keep passing as Modules 2-12
land and cannot be broken by an unrelated view's changes.

The society-isolation tests are the important ones: a permission class answers
"may this user call this endpoint?", never "which rows may they see?". Both
checks are required, and the second is the one that leaks data when omitted.
"""

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.accounts.models import DeviceSession, Role
from apps.accounts.permissions import (
    IsApproved,
    IsApprovedGuard,
    IsGateStaff,
    IsGuard,
    IsResident,
    IsSocietyAdmin,
    IsWorker,
)
from apps.core.mixins import SocietyScopedQuerysetMixin
from apps.societies.models import Society, SocietyStatus

pytestmark = pytest.mark.django_db

factory = APIRequestFactory()


def _view_requiring(permission_class):
    """Build a minimal view guarded by ``permission_class``."""

    class _ProtectedView(APIView):
        permission_classes = [permission_class]

        def get(self, request):
            return Response({"ok": True})

    return _ProtectedView.as_view()


def _call(view, user):
    request = factory.get("/test/")
    force_authenticate(request, user=user)
    return view(request)


class TestSingleRolePermissions:
    """Each role class must admit exactly its own role and refuse the rest."""

    @pytest.mark.parametrize(
        ("permission_class", "allowed_fixture"),
        [
            (IsResident, "resident_user"),
            (IsWorker, "worker_user"),
            (IsGuard, "guard_user"),
            (IsSocietyAdmin, "admin_user"),
        ],
    )
    def test_matching_role_is_allowed(self, request, permission_class, allowed_fixture):
        user = request.getfixturevalue(allowed_fixture)
        assert _call(_view_requiring(permission_class), user).status_code == 200

    @pytest.mark.parametrize(
        ("permission_class", "denied_fixtures"),
        [
            (IsResident, ["worker_user", "guard_user", "admin_user"]),
            (IsWorker, ["resident_user", "guard_user", "admin_user"]),
            (IsGuard, ["resident_user", "worker_user", "admin_user"]),
            (IsSocietyAdmin, ["resident_user", "worker_user", "guard_user"]),
        ],
    )
    def test_other_roles_are_denied(self, request, permission_class, denied_fixtures):
        view = _view_requiring(permission_class)
        for fixture in denied_fixtures:
            user = request.getfixturevalue(fixture)
            assert _call(view, user).status_code == 403, f"{fixture} should be denied"

    def test_anonymous_is_denied(self, resident_user):
        response = _view_requiring(IsResident)(factory.get("/test/"))
        assert response.status_code in (401, 403)


class TestApprovalGate:
    def test_approved_user_is_allowed(self, resident_user):
        assert _call(_view_requiring(IsApproved), resident_user).status_code == 200

    def test_unapproved_user_is_denied(self, django_user_model, society):
        """An unapproved worker must not be hireable or admittable (SRS 3.2)."""
        pending = django_user_model.objects.create_user(
            phone_number="9700000001", password="test-pass-12345",
            role=Role.WORKER, society=society, is_approved=False,
        )
        assert _call(_view_requiring(IsApproved), pending).status_code == 403

    def test_composed_role_and_approval_requires_both(self, django_user_model, society):
        unapproved_guard = django_user_model.objects.create_user(
            phone_number="9700000002", password="test-pass-12345",
            role=Role.GUARD, society=society, is_approved=False,
        )
        assert _call(_view_requiring(IsApprovedGuard), unapproved_guard).status_code == 403


class TestComposedPermissions:
    def test_gate_staff_admits_guards_and_admins_only(
        self, guard_user, admin_user, resident_user, worker_user
    ):
        view = _view_requiring(IsGateStaff)
        assert _call(view, guard_user).status_code == 200
        assert _call(view, admin_user).status_code == 200
        assert _call(view, resident_user).status_code == 403
        assert _call(view, worker_user).status_code == 403


class TestSocietyIsolation:
    """A guard or admin at one society must never read another's rows.

    This is enforced on the queryset, not the permission — hence testing the
    mixin directly.
    """

    @pytest.fixture
    def other_society(self, db):
        return Society.objects.create(
            name="Riverside Towers", address_line="MG Road", city="Bengaluru",
            state="Karnataka", pincode="560001", status=SocietyStatus.ACTIVE,
        )

    @pytest.fixture
    def foreign_guard(self, db, django_user_model, other_society):
        return django_user_model.objects.create_user(
            phone_number="9700000003", password="test-pass-12345",
            role=Role.GUARD, society=other_society, is_approved=True,
        )

    def _scoped_view_for(self, user):
        """A minimal view whose queryset is society-scoped for ``user``.

        The mixin calls ``super().get_queryset()``, so it needs a base class
        supplying the unfiltered queryset — that is what ``_UnscopedBase`` is.
        """

        class _UnscopedBase:
            def get_queryset(self):
                return DeviceSession.objects.all()

        class _ScopedView(SocietyScopedQuerysetMixin, _UnscopedBase):
            # DeviceSession reaches its society indirectly, via the user.
            society_lookup = "user__society"

            def __init__(self, request):
                self.request = request

        request = factory.get("/test/")
        request.user = user
        return _ScopedView(request)

    def test_user_sees_only_their_own_societys_rows(
        self, guard_user, foreign_guard, resident_user
    ):
        DeviceSession.objects.create(user=guard_user, device_id="home-device")
        DeviceSession.objects.create(user=foreign_guard, device_id="foreign-device")

        visible = self._scoped_view_for(guard_user).get_queryset()

        assert visible.count() == 1
        assert visible.first().device_id == "home-device"

    def test_foreign_society_rows_are_invisible(self, guard_user, foreign_guard):
        DeviceSession.objects.create(user=foreign_guard, device_id="foreign-device")

        visible = self._scoped_view_for(guard_user).get_queryset()

        assert not visible.filter(device_id="foreign-device").exists()

    def test_superuser_sees_across_societies(
        self, django_user_model, guard_user, foreign_guard
    ):
        """Platform staff operate across societies by definition."""
        superuser = django_user_model.objects.create_superuser(
            phone_number="9700000009", password="test-pass-12345"
        )
        DeviceSession.objects.create(user=guard_user, device_id="a")
        DeviceSession.objects.create(user=foreign_guard, device_id="b")

        assert self._scoped_view_for(superuser).get_queryset().count() == 2

    def test_user_without_a_society_sees_nothing(self, django_user_model, guard_user):
        """Fails closed: returning everything here would be a cross-tenant leak."""
        DeviceSession.objects.create(user=guard_user, device_id="a")
        societyless = django_user_model.objects.create_user(
            phone_number="9700000004", password="test-pass-12345",
            role=Role.SOCIETY_ADMIN, society=None,
        )

        assert self._scoped_view_for(societyless).get_queryset().count() == 0


class TestSessionRevocationScope:
    """Module 1.5 — the lost-or-stolen-device path, and its limits."""

    def test_user_can_revoke_their_own_session(self, resident_user, authenticated_client):
        from django.urls import reverse

        session = DeviceSession.objects.create(user=resident_user, device_id="mine")
        response = authenticated_client(resident_user).delete(
            reverse("v1:accounts:session-revoke", args=[session.pk])
        )
        assert response.status_code == 204
        session.refresh_from_db()
        assert session.is_revoked is True

    def test_admin_can_revoke_a_session_in_their_own_society(
        self, admin_user, worker_user, authenticated_client
    ):
        """Must work without the user signing in first — the phone is gone."""
        from django.urls import reverse

        session = DeviceSession.objects.create(user=worker_user, device_id="lost-phone")
        response = authenticated_client(admin_user).delete(
            reverse("v1:accounts:session-revoke", args=[session.pk])
        )
        assert response.status_code == 204
        session.refresh_from_db()
        assert session.is_revoked is True

    def test_admin_cannot_revoke_a_session_in_another_society(
        self, admin_user, authenticated_client, django_user_model
    ):
        from django.urls import reverse

        other_society = Society.objects.create(
            name="Far Away Homes", address_line="Q", city="Chennai",
            state="TN", pincode="600001", status=SocietyStatus.ACTIVE,
        )
        foreign_user = django_user_model.objects.create_user(
            phone_number="9700000005", password="test-pass-12345",
            role=Role.WORKER, society=other_society,
        )
        session = DeviceSession.objects.create(user=foreign_user, device_id="foreign")

        response = authenticated_client(admin_user).delete(
            reverse("v1:accounts:session-revoke", args=[session.pk])
        )
        assert response.status_code == 403
        session.refresh_from_db()
        assert session.is_revoked is False

    def test_resident_cannot_revoke_another_users_session(
        self, resident_user, worker_user, authenticated_client
    ):
        from django.urls import reverse

        session = DeviceSession.objects.create(user=worker_user, device_id="not-yours")
        response = authenticated_client(resident_user).delete(
            reverse("v1:accounts:session-revoke", args=[session.pk])
        )
        assert response.status_code == 403

    def test_guard_login_revokes_other_guard_sessions(self, api_client, guard_user):
        """A gate terminal holds one session; two would make the entry log ambiguous."""
        DeviceSession.objects.create(user=guard_user, device_id="old-terminal")

        api_client.post(
            "/api/v1/auth/login/",
            {
                "phone_number": guard_user.phone_number,
                "password": "test-pass-12345",
                "device": {"device_id": "new-terminal"},
            },
            format="json",
        )

        assert DeviceSession.objects.get(device_id="old-terminal").is_revoked is True
        assert DeviceSession.objects.get(device_id="new-terminal").is_revoked is False

    def test_resident_login_does_not_revoke_other_devices(self, api_client, resident_user):
        """Only guards are single-session; residents legitimately use two devices."""
        DeviceSession.objects.create(user=resident_user, device_id="tablet")

        api_client.post(
            "/api/v1/auth/login/",
            {
                "phone_number": resident_user.phone_number,
                "password": "test-pass-12345",
                "device": {"device_id": "phone"},
            },
            format="json",
        )

        assert DeviceSession.objects.get(device_id="tablet").is_revoked is False

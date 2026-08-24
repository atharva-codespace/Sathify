"""
The platform operator role, and the one audited way it reads across societies.

This codebase's central promise is that no request sees another society's rows.
The console breaks that promise deliberately, so the tests here are less about
"does the feature work" and more about "does the exception stay an exception":
that it has a name, that it cannot be held by accident, and that using it leaves
a record.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import (
    ImpersonationGrant,
    PlatformAccessLog,
    Role,
    SuperadminLevel,
    SuperadminProfile,
    User,
)
from apps.accounts.permissions import (
    CanImpersonate,
    CanRefund,
    CanSettleManually,
    IsSocietyAdmin,
    IsSuperadmin,
)
from apps.core.platform import PII_MODEL_LABELS, record_platform_access

pytestmark = pytest.mark.django_db


@pytest.fixture
def superadmin(db):
    return User.objects.create_superuser(phone_number="9800000099", password="x-12345")


@pytest.fixture
def support(superadmin):
    SuperadminProfile.objects.create(user=superadmin, level=SuperadminLevel.SUPPORT)
    superadmin.refresh_from_db()
    return superadmin


@pytest.fixture
def finance(db):
    user = User.objects.create_superuser(phone_number="9800000098", password="x-12345")
    SuperadminProfile.objects.create(user=user, level=SuperadminLevel.FINANCE)
    user.refresh_from_db()
    return user


class _Request:
    def __init__(self, user):
        self.user = user


class TestTheRoleIsNotSocietyAdmin:
    def test_createsuperuser_no_longer_produces_an_admin_of_no_society(self, superadmin):
        """The bug this role fixes.

        The old default made every `createsuperuser` a SOCIETY_ADMIN whose
        society was null — authorised by every permission class, and then shown
        nothing by every queryset. Authorised-but-blind is the more confusing of
        the two possible failures.
        """
        assert superadmin.role == Role.SUPERADMIN
        assert superadmin.is_superadmin is True
        assert superadmin.is_society_admin is False

    def test_a_platform_operator_belongs_to_no_society(self, superadmin):
        assert superadmin.society_id is None

    def test_the_database_refuses_a_superadmin_with_a_society(self, superadmin, society):
        """Otherwise every scoped queryset would return that one society and look fine."""
        superadmin.society = society
        with pytest.raises(IntegrityError), transaction.atomic():
            superadmin.save()

    def test_society_roles_are_unaffected(self, admin_user, society):
        assert admin_user.role == Role.SOCIETY_ADMIN
        assert admin_user.society_id == society.id
        assert admin_user.is_superadmin is False

    def test_an_unknown_role_is_still_refused(self, society):
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user(
                phone_number="9800000097", password="x-12345", role="", society=society
            )


class TestCapabilitiesFollowTheLevel:
    def test_support_cannot_touch_money(self, support):
        profile = support.superadmin_profile
        assert profile.may_refund is False
        assert profile.may_settle_manually is False

    def test_finance_can(self, finance):
        profile = finance.superadmin_profile
        assert profile.may_refund is True
        assert profile.may_settle_manually is True

    def test_a_capability_cannot_be_ticked_on_independently(self, support):
        """Granting Support and then quietly setting may_refund must not stick."""
        profile = support.superadmin_profile
        profile.may_refund = True
        profile.save()
        profile.refresh_from_db()
        assert profile.may_refund is False

    def test_nobody_holds_both_capabilities_and_impersonation(self, support, finance):
        """Separated so one compromised console account cannot do both.

        Support can alter a society's operational records; Finance can move
        money out of them. Neither can do the other's job.
        """
        assert CanImpersonate().has_permission(_Request(support), None) is True
        assert CanRefund().has_permission(_Request(support), None) is False

        assert CanImpersonate().has_permission(_Request(finance), None) is False
        assert CanRefund().has_permission(_Request(finance), None) is True


class TestPermissions:
    def test_a_superadmin_without_a_profile_is_denied_every_capability(self, superadmin):
        """Absence of a grant is a denial — the only safe direction here."""
        assert superadmin.superadmin_level is None
        request = _Request(superadmin)
        assert CanRefund().has_permission(request, None) is False
        assert CanSettleManually().has_permission(request, None) is False
        assert CanImpersonate().has_permission(request, None) is False

    def test_the_role_check_still_denies_a_society_admin(self, admin_user, superadmin):
        assert IsSuperadmin().has_permission(_Request(admin_user), None) is False
        assert IsSuperadmin().has_permission(_Request(superadmin), None) is True

    def test_a_superadmin_is_not_a_society_admin_by_the_permission_classes(self, superadmin):
        assert IsSocietyAdmin().has_permission(_Request(superadmin), None) is False

    def test_a_resident_is_denied(self, resident_user):
        assert IsSuperadmin().has_permission(_Request(resident_user), None) is False
        assert CanRefund().has_permission(_Request(resident_user), None) is False


class TestImpersonation:
    def test_a_grant_without_a_reason_is_refused_by_the_database(
        self, support, admin_user, society
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            ImpersonationGrant.objects.create(
                superadmin=support, target=admin_user, society=society, reason=""
            )

    def test_a_grant_expires_on_its_own(self, support, admin_user, society):
        """An operator who forgets to close a session loses it anyway."""
        grant = ImpersonationGrant.objects.create(
            superadmin=support, target=admin_user, society=society,
            reason="resident reports two extra sessions on INV-4417-08",
        )
        assert grant.is_live is True
        expected = grant.started_at + timezone.timedelta(minutes=ImpersonationGrant.DEFAULT_MINUTES)
        assert grant.expires_at == expected

        grant.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        grant.save()
        assert grant.is_live is False

    def test_ending_is_idempotent(self, support, admin_user, society):
        grant = ImpersonationGrant.objects.create(
            superadmin=support, target=admin_user, society=society, reason="checking a charge",
        )
        assert grant.end() is True
        assert grant.end() is False
        assert grant.is_live is False


class TestPlatformAccessLog:
    def test_reading_a_person_is_logged(self, support, society):
        row = record_platform_access(
            user=support,
            model_label="societies.Resident",
            society=society,
            reason="invoice query #221",
            row_count=4,
            ip_address="10.2.0.9",
        )
        assert row is not None
        assert row.society_id == society.id
        assert row.reason == "invoice query #221"
        assert PlatformAccessLog.objects.count() == 1

    def test_reading_money_is_not(self, support, society):
        """Payments are about a transaction, not about an identifiable person."""
        assert record_platform_access(
            user=support, model_label="payments.Payment", society=society, row_count=1200
        ) is None
        assert PlatformAccessLog.objects.count() == 0

    def test_the_pii_list_covers_the_models_that_name_people(self):
        assert "accounts.User" in PII_MODEL_LABELS
        assert "workers.WorkerProfile" in PII_MODEL_LABELS
        assert "attendance.WorkSession" in PII_MODEL_LABELS

    def test_a_society_can_read_its_own_access_log(self, support, society):
        """Being watchable is the price of holding the bypass (PRD §9.4d)."""
        record_platform_access(
            user=support, model_label="accounts.User", society=society, row_count=1
        )
        assert society.platform_access_logs.count() == 1

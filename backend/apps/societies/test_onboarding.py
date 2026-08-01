"""
Module 2 — Society & Resident Onboarding tests.

Three properties matter most here and are covered deliberately:

* A society cannot activate itself — verification is a platform decision.
* An administrator cannot read, approve or modify anything in another society.
* A flat has at most one primary account holder, enforced in the database.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Role
from apps.societies.models import (
    Flat,
    Gate,
    Resident,
    ResidentRelationship,
    Society,
    SocietyStatus,
    Tower,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def tower(society):
    return Tower.objects.create(society=society, name="A", floors=4)


@pytest.fixture
def flat(tower):
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def other_society(db):
    return Society.objects.create(
        name="Riverside Towers", address_line="MG Road", city="Bengaluru",
        state="Karnataka", pincode="560001", status=SocietyStatus.ACTIVE,
    )


@pytest.fixture
def other_admin(db, django_user_model, other_society):
    return django_user_model.objects.create_user(
        phone_number="9600000001", password="test-pass-12345",
        role=Role.SOCIETY_ADMIN, society=other_society, is_approved=True,
    )


# ---------------------------------------------------------------------------
# 2.1 Society registration
# ---------------------------------------------------------------------------


class TestSocietyRegistration:
    def _payload(self, **overrides):
        payload = {
            "name": "Sunrise Residency",
            "address_line": "Kharadi Road",
            "city": "Pune",
            "state": "Maharashtra",
            "pincode": "411014",
            "total_towers": 2,
            "total_flats": 96,
        }
        payload.update(overrides)
        return payload

    @pytest.fixture
    def unattached_admin(self, db, django_user_model):
        """A society admin who has registered but has no society yet."""
        return django_user_model.objects.create_user(
            phone_number="9600000009", password="test-pass-12345",
            role=Role.SOCIETY_ADMIN, society=None, is_approved=False,
        )

    def test_admin_can_register_a_society(self, unattached_admin, authenticated_client):
        response = authenticated_client(unattached_admin).post(
            reverse("v1:societies:register"), self._payload(), format="json"
        )
        assert response.status_code == 201
        assert response.data["requires_verification"] is True

    def test_new_society_is_pending_not_active(self, unattached_admin, authenticated_client):
        """A society must not be able to activate itself."""
        authenticated_client(unattached_admin).post(
            reverse("v1:societies:register"), self._payload(), format="json"
        )
        assert Society.objects.get(name="Sunrise Residency").status == SocietyStatus.PENDING

    def test_registering_admin_is_attached_to_the_society(
        self, unattached_admin, authenticated_client
    ):
        authenticated_client(unattached_admin).post(
            reverse("v1:societies:register"), self._payload(), format="json"
        )
        unattached_admin.refresh_from_db()
        assert unattached_admin.society is not None
        assert unattached_admin.society.name == "Sunrise Residency"

    def test_a_default_gate_is_created(self, unattached_admin, authenticated_client):
        """Guards need somewhere to be assigned from day one."""
        authenticated_client(unattached_admin).post(
            reverse("v1:societies:register"), self._payload(), format="json"
        )
        society = Society.objects.get(name="Sunrise Residency")
        assert Gate.objects.filter(society=society, name="Main Gate").exists()

    def test_status_cannot_be_forced_through_the_payload(
        self, unattached_admin, authenticated_client
    ):
        authenticated_client(unattached_admin).post(
            reverse("v1:societies:register"),
            self._payload(status=SocietyStatus.ACTIVE),
            format="json",
        )
        assert Society.objects.get(name="Sunrise Residency").status == SocietyStatus.PENDING

    def test_duplicate_name_at_same_pincode_is_rejected(
        self, unattached_admin, authenticated_client, society
    ):
        response = authenticated_client(unattached_admin).post(
            reverse("v1:societies:register"),
            self._payload(name=society.name, pincode=society.pincode),
            format="json",
        )
        assert response.status_code == 400

    def test_admin_already_in_a_society_cannot_register_another(
        self, admin_user, authenticated_client
    ):
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:register"), self._payload(), format="json"
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("fixture", ["resident_user", "worker_user", "guard_user"])
    def test_non_admins_cannot_register_a_society(
        self, request, fixture, authenticated_client
    ):
        user = request.getfixturevalue(fixture)
        response = authenticated_client(user).post(
            reverse("v1:societies:register"), self._payload(), format="json"
        )
        assert response.status_code == 403


class TestSocietyActivation:
    def test_activation_approves_pending_administrators(
        self, db, django_user_model
    ):
        """A verified society with nobody able to operate in it is useless."""
        society = Society.objects.create(
            name="Pending Park", address_line="A", city="Pune",
            state="MH", pincode="411099", status=SocietyStatus.PENDING,
        )
        admin = django_user_model.objects.create_user(
            phone_number="9600000002", password="test-pass-12345",
            role=Role.SOCIETY_ADMIN, society=society, is_approved=False,
        )

        society.activate()

        admin.refresh_from_db()
        assert society.status == SocietyStatus.ACTIVE
        assert admin.is_approved is True

    def test_activation_is_idempotent(self, society):
        society.activate()
        first = society.verified_at
        society.activate()
        assert society.verified_at == first


class TestPublicSocietyList:
    def test_is_reachable_without_authentication(self, api_client, society):
        """A prospective resident must pick a society before they have an account."""
        response = api_client.get(reverse("v1:societies:public-list"))
        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_only_active_societies_are_listed(self, api_client, society):
        Society.objects.create(
            name="Hidden Heights", address_line="B", city="Pune",
            state="MH", pincode="411098", status=SocietyStatus.PENDING,
        )
        response = api_client.get(reverse("v1:societies:public-list"))
        names = [row["name"] for row in response.data["results"]]
        assert "Hidden Heights" not in names

    def test_exposes_only_identifying_fields(self, api_client, society):
        """No resident counts, operating rules or internal configuration."""
        response = api_client.get(reverse("v1:societies:public-list"))
        assert set(response.data["results"][0]) == {
            "id", "name", "city", "state", "pincode"
        }


# ---------------------------------------------------------------------------
# 2.2 Tower & flat mapping
# ---------------------------------------------------------------------------


class TestTowerAndFlatMapping:
    def test_admin_can_create_a_tower(self, admin_user, authenticated_client):
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:tower-list"), {"name": "B", "floors": 6}, format="json"
        )
        assert response.status_code == 201
        assert Tower.objects.get(name="B").society_id == admin_user.society_id

    def test_resident_can_read_towers_but_not_create(
        self, resident_user, authenticated_client, tower
    ):
        client = authenticated_client(resident_user)
        assert client.get(reverse("v1:societies:tower-list")).status_code == 200
        assert client.post(
            reverse("v1:societies:tower-list"), {"name": "C", "floors": 2}, format="json"
        ).status_code == 403

    def test_towers_from_other_societies_are_invisible(
        self, admin_user, authenticated_client, other_society
    ):
        Tower.objects.create(society=other_society, name="Foreign", floors=3)
        response = authenticated_client(admin_user).get(reverse("v1:societies:tower-list"))
        names = [row["name"] for row in response.data["results"]]
        assert "Foreign" not in names

    def test_bulk_flat_generation(self, admin_user, authenticated_client, tower):
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:bulk-flats"),
            {"tower": tower.id, "floors": 4, "flats_per_floor": 2},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["created"] == 8
        assert Flat.objects.filter(tower=tower, number="101").exists()
        assert Flat.objects.filter(tower=tower, number="402").exists()

    def test_bulk_generation_skips_existing_flats(
        self, admin_user, authenticated_client, tower
    ):
        Flat.objects.create(tower=tower, number="101", floor=1)
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:bulk-flats"),
            {"tower": tower.id, "floors": 1, "flats_per_floor": 2},
            format="json",
        )
        assert response.data["created"] == 1
        assert response.data["skipped"] == 1

    def test_cannot_bulk_generate_into_another_societys_tower(
        self, admin_user, authenticated_client, other_society
    ):
        foreign_tower = Tower.objects.create(society=other_society, name="X", floors=2)
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:bulk-flats"),
            {"tower": foreign_tower.id, "floors": 2, "flats_per_floor": 2},
            format="json",
        )
        assert response.status_code == 400
        assert Flat.objects.filter(tower=foreign_tower).count() == 0

    def test_flat_numbers_are_unique_within_a_tower(self, tower):
        from django.db import IntegrityError

        Flat.objects.create(tower=tower, number="101", floor=1)
        with pytest.raises(IntegrityError):
            Flat.objects.create(tower=tower, number="101", floor=1)

    def test_vacant_filter_excludes_occupied_flats(
        self, admin_user, authenticated_client, tower, flat, resident_user
    ):
        Flat.objects.create(tower=tower, number="302", floor=3)
        Resident.objects.create(user=resident_user, flat=flat, is_primary=True)

        response = authenticated_client(admin_user).get(
            reverse("v1:societies:flat-list"), {"vacant": "true"}
        )
        numbers = [row["number"] for row in response.data["results"]]
        assert numbers == ["302"]


# ---------------------------------------------------------------------------
# 2.3 Resident registration & approval
# ---------------------------------------------------------------------------


class TestResidentProfileCreation:
    def test_resident_can_claim_a_flat(self, resident_user, authenticated_client, flat):
        response = authenticated_client(resident_user).post(
            reverse("v1:societies:resident-create"),
            {"flat": flat.id, "relationship": ResidentRelationship.TENANT},
            format="json",
        )
        assert response.status_code == 201
        assert Resident.objects.filter(user=resident_user, flat=flat).exists()

    def test_unapproved_resident_can_still_claim_a_flat(
        self, db, django_user_model, society, authenticated_client, flat
    ):
        """Otherwise onboarding deadlocks: approval needs something to review."""
        pending = django_user_model.objects.create_user(
            phone_number="9600000003", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=False,
        )
        response = authenticated_client(pending).post(
            reverse("v1:societies:resident-create"), {"flat": flat.id}, format="json"
        )
        assert response.status_code == 201

    def test_first_resident_in_a_flat_becomes_primary(
        self, resident_user, authenticated_client, flat
    ):
        authenticated_client(resident_user).post(
            reverse("v1:societies:resident-create"), {"flat": flat.id}, format="json"
        )
        assert Resident.objects.get(user=resident_user).is_primary is True

    def test_second_resident_in_a_flat_is_not_primary(
        self, db, django_user_model, society, resident_user, authenticated_client, flat
    ):
        Resident.objects.create(user=resident_user, flat=flat, is_primary=True)
        second = django_user_model.objects.create_user(
            phone_number="9600000004", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=True,
        )

        authenticated_client(second).post(
            reverse("v1:societies:resident-create"), {"flat": flat.id}, format="json"
        )
        assert Resident.objects.get(user=second).is_primary is False

    def test_cannot_claim_a_flat_in_another_society(
        self, resident_user, authenticated_client, other_society
    ):
        foreign_tower = Tower.objects.create(society=other_society, name="Z", floors=1)
        foreign_flat = Flat.objects.create(tower=foreign_tower, number="101", floor=1)

        response = authenticated_client(resident_user).post(
            reverse("v1:societies:resident-create"), {"flat": foreign_flat.id}, format="json"
        )
        assert response.status_code == 400
        assert not Resident.objects.filter(user=resident_user).exists()

    def test_cannot_claim_two_flats(self, resident_user, authenticated_client, tower, flat):
        second_flat = Flat.objects.create(tower=tower, number="302", floor=3)
        client = authenticated_client(resident_user)

        client.post(reverse("v1:societies:resident-create"), {"flat": flat.id}, format="json")
        response = client.post(
            reverse("v1:societies:resident-create"), {"flat": second_flat.id}, format="json"
        )
        assert response.status_code == 400

    def test_a_worker_cannot_claim_a_flat(self, worker_user, authenticated_client, flat):
        response = authenticated_client(worker_user).post(
            reverse("v1:societies:resident-create"), {"flat": flat.id}, format="json"
        )
        assert response.status_code == 403


class TestResidentApprovalQueue:
    @pytest.fixture
    def pending_resident(self, db, django_user_model, society, flat):
        user = django_user_model.objects.create_user(
            phone_number="9600000005", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=False,
        )
        return Resident.objects.create(user=user, flat=flat, is_primary=True)

    def test_queue_lists_only_unapproved_residents(
        self, admin_user, authenticated_client, pending_resident, resident_user, tower
    ):
        approved_flat = Flat.objects.create(tower=tower, number="401", floor=4)
        Resident.objects.create(user=resident_user, flat=approved_flat, is_primary=True)

        response = authenticated_client(admin_user).get(
            reverse("v1:societies:resident-pending")
        )
        ids = [row["id"] for row in response.data["results"]]
        assert ids == [pending_resident.id]

    def test_queue_is_scoped_to_the_admins_society(
        self, other_admin, authenticated_client, pending_resident
    ):
        response = authenticated_client(other_admin).get(
            reverse("v1:societies:resident-pending")
        )
        assert response.data["count"] == 0

    def test_approving_grants_platform_access(
        self, admin_user, authenticated_client, pending_resident
    ):
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:resident-decide", args=[pending_resident.id]),
            {"approve": True},
            format="json",
        )
        assert response.status_code == 200
        pending_resident.user.refresh_from_db()
        assert pending_resident.user.is_approved is True

    def test_rejection_requires_a_reason(
        self, admin_user, authenticated_client, pending_resident
    ):
        """The resident needs to know what to correct."""
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:resident-decide", args=[pending_resident.id]),
            {"approve": False},
            format="json",
        )
        assert response.status_code == 400

    def test_rejection_records_the_reason_and_keeps_the_account(
        self, admin_user, authenticated_client, pending_resident
    ):
        authenticated_client(admin_user).post(
            reverse("v1:societies:resident-decide", args=[pending_resident.id]),
            {"approve": False, "rejection_reason": "Proof of residence is unreadable."},
            format="json",
        )
        pending_resident.refresh_from_db()
        pending_resident.user.refresh_from_db()

        assert pending_resident.rejection_reason == "Proof of residence is unreadable."
        assert pending_resident.user.is_approved is False
        # The account survives so the resident can correct and resubmit.
        assert pending_resident.user.pk is not None

    def test_decision_records_the_reviewer(
        self, admin_user, authenticated_client, pending_resident
    ):
        authenticated_client(admin_user).post(
            reverse("v1:societies:resident-decide", args=[pending_resident.id]),
            {"approve": True},
            format="json",
        )
        pending_resident.refresh_from_db()
        assert pending_resident.reviewed_by_id == admin_user.id
        assert pending_resident.reviewed_at is not None

    def test_admin_cannot_decide_for_another_society(
        self, other_admin, authenticated_client, pending_resident
    ):
        response = authenticated_client(other_admin).post(
            reverse("v1:societies:resident-decide", args=[pending_resident.id]),
            {"approve": True},
            format="json",
        )
        assert response.status_code == 403
        pending_resident.user.refresh_from_db()
        assert pending_resident.user.is_approved is False

    @pytest.mark.parametrize("fixture", ["resident_user", "worker_user", "guard_user"])
    def test_non_admins_cannot_approve(
        self, request, fixture, authenticated_client, pending_resident
    ):
        user = request.getfixturevalue(fixture)
        response = authenticated_client(user).post(
            reverse("v1:societies:resident-decide", args=[pending_resident.id]),
            {"approve": True},
            format="json",
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 2.4 Multi-resident-per-flat
# ---------------------------------------------------------------------------


class TestMultiResidentPerFlat:
    def test_database_forbids_two_primaries_in_one_flat(
        self, db, django_user_model, society, resident_user, flat
    ):
        """Enforced by constraint, not just serializer — a race would slip past."""
        from django.db import IntegrityError

        Resident.objects.create(user=resident_user, flat=flat, is_primary=True)
        second = django_user_model.objects.create_user(
            phone_number="9600000006", password="test-pass-12345",
            role=Role.RESIDENT, society=society,
        )
        with pytest.raises(IntegrityError):
            Resident.objects.create(user=second, flat=flat, is_primary=True)

    def test_admin_can_reassign_the_primary_holder(
        self, db, django_user_model, society, resident_user, admin_user,
        authenticated_client, flat,
    ):
        first = Resident.objects.create(user=resident_user, flat=flat, is_primary=True)
        other_user = django_user_model.objects.create_user(
            phone_number="9600000007", password="test-pass-12345",
            role=Role.RESIDENT, society=society,
        )
        second = Resident.objects.create(user=other_user, flat=flat, is_primary=False)

        response = authenticated_client(admin_user).post(
            reverse("v1:societies:set-primary"), {"resident_id": second.id}, format="json"
        )
        assert response.status_code == 200

        first.refresh_from_db()
        second.refresh_from_db()
        assert second.is_primary is True
        assert first.is_primary is False

    def test_household_lists_the_other_members(
        self, db, django_user_model, society, resident_user, flat
    ):
        first = Resident.objects.create(user=resident_user, flat=flat, is_primary=True)
        other_user = django_user_model.objects.create_user(
            phone_number="9600000008", password="test-pass-12345",
            role=Role.RESIDENT, society=society,
        )
        second = Resident.objects.create(user=other_user, flat=flat)

        assert list(first.household) == [second]

    def test_cannot_reassign_primary_in_another_society(
        self, other_admin, authenticated_client, resident_user, flat
    ):
        resident = Resident.objects.create(user=resident_user, flat=flat, is_primary=True)
        response = authenticated_client(other_admin).post(
            reverse("v1:societies:set-primary"), {"resident_id": resident.id}, format="json"
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 2.5 Society configuration
# ---------------------------------------------------------------------------


class TestSocietyConfiguration:
    def test_admin_can_update_operating_rules(self, admin_user, authenticated_client):
        response = authenticated_client(admin_user).patch(
            reverse("v1:societies:config"),
            {"booking_notice_hours": 24, "gate_count": 3},
            format="json",
        )
        assert response.status_code == 200

        admin_user.society.refresh_from_db()
        assert admin_user.society.booking_notice_hours == 24
        assert admin_user.society.gate_count == 3

    def test_status_is_not_writable_through_configuration(
        self, admin_user, authenticated_client
    ):
        """A society must never be able to verify itself."""
        authenticated_client(admin_user).patch(
            reverse("v1:societies:config"),
            {"status": SocietyStatus.SUSPENDED},
            format="json",
        )
        admin_user.society.refresh_from_db()
        assert admin_user.society.status == SocietyStatus.ACTIVE

    @pytest.mark.parametrize("fixture", ["resident_user", "worker_user", "guard_user"])
    def test_non_admins_cannot_change_configuration(
        self, request, fixture, authenticated_client
    ):
        user = request.getfixturevalue(fixture)
        response = authenticated_client(user).patch(
            reverse("v1:societies:config"), {"gate_count": 9}, format="json"
        )
        assert response.status_code == 403

    def test_admin_can_add_a_gate(self, admin_user, authenticated_client):
        response = authenticated_client(admin_user).post(
            reverse("v1:societies:gate-list"), {"name": "Service Gate"}, format="json"
        )
        assert response.status_code == 201
        assert Gate.objects.get(name="Service Gate").society_id == admin_user.society_id

    def test_my_society_returns_the_callers_society(
        self, resident_user, authenticated_client, society
    ):
        response = authenticated_client(resident_user).get(reverse("v1:societies:my-society"))
        assert response.status_code == 200
        assert response.data["id"] == society.id

"""Module 2 — Society & Resident Onboarding: serializers."""

from django.db import transaction
from rest_framework import serializers

from .models import (
    Flat,
    Gate,
    Resident,
    ResidentRelationship,
    Society,
    SocietyStatus,
    Tower,
)


# ---------------------------------------------------------------------------
# 2.1 Society registration
# ---------------------------------------------------------------------------


class PublicSocietySerializer(serializers.ModelSerializer):
    """Minimal, unauthenticated view of an active society.

    Powers the society picker on the registration screen, so it is deliberately
    narrow: enough to identify the right society, nothing about its residents,
    configuration or operations.
    """

    class Meta:
        model = Society
        fields = ["id", "name", "city", "state", "pincode"]
        read_only_fields = fields


class SocietySerializer(serializers.ModelSerializer):
    """Full society record, for members and administrators."""

    mapped_flat_count = serializers.IntegerField(read_only=True)
    tower_count = serializers.IntegerField(source="towers.count", read_only=True)

    class Meta:
        model = Society
        fields = [
            "id", "name", "registration_number",
            "address_line", "city", "state", "pincode", "latitude", "longitude",
            "total_towers", "total_flats", "gate_count",
            "booking_notice_hours", "guard_shift_hours", "allow_resident_self_checkin",
            "status", "verified_at", "rejection_reason",
            "mapped_flat_count", "tower_count", "created_at",
        ]
        # Status is changed only by platform verification, never by a PATCH from
        # the society's own administrator — otherwise a society could activate
        # itself and bypass verification entirely.
        read_only_fields = ["id", "status", "verified_at", "rejection_reason", "created_at"]


class SocietyRegistrationSerializer(serializers.ModelSerializer):
    """Module 2.1 — an administrator registers their society.

    Lands in PENDING. The registering administrator is attached to it, but
    neither becomes usable until platform verification activates the society.
    """

    class Meta:
        model = Society
        fields = [
            "id", "name", "registration_number",
            "address_line", "city", "state", "pincode", "latitude", "longitude",
            "total_towers", "total_flats", "gate_count",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if Society.objects.filter(
            name__iexact=attrs["name"], pincode=attrs["pincode"]
        ).exists():
            raise serializers.ValidationError(
                {"name": "A society with this name is already registered at this pincode. "
                         "If this is your society, ask its administrator to add you as staff."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        society = Society.objects.create(status=SocietyStatus.PENDING, **validated_data)

        # Bind the registering administrator to the society they just created.
        admin = self.context["request"].user
        admin.society = society
        admin.save(update_fields=["society", "updated_at"])

        # Create the default gate so guards have somewhere to be assigned from
        # day one; administrators can rename or add more in configuration.
        Gate.objects.create(society=society, name="Main Gate")
        return society


class SocietyConfigurationSerializer(serializers.ModelSerializer):
    """Module 2.5 — the settings an administrator may change post-approval."""

    class Meta:
        model = Society
        fields = [
            "gate_count", "booking_notice_hours", "guard_shift_hours",
            "allow_resident_self_checkin", "total_towers", "total_flats",
            "latitude", "longitude",
        ]


# ---------------------------------------------------------------------------
# 2.2 Tower & flat mapping
# ---------------------------------------------------------------------------


class FlatSerializer(serializers.ModelSerializer):
    tower_name = serializers.CharField(source="tower.name", read_only=True)
    label = serializers.SerializerMethodField()
    is_occupied = serializers.SerializerMethodField()

    class Meta:
        model = Flat
        fields = ["id", "tower", "tower_name", "number", "floor", "label", "is_occupied"]
        read_only_fields = ["id", "tower_name", "label", "is_occupied"]

    def get_label(self, obj) -> str:
        return f"{obj.tower.name}-{obj.number}"

    def get_is_occupied(self, obj) -> bool:
        return obj.residents.exists()


class TowerSerializer(serializers.ModelSerializer):
    flat_count = serializers.IntegerField(source="flats.count", read_only=True)

    class Meta:
        model = Tower
        fields = ["id", "society", "name", "floors", "flat_count"]
        read_only_fields = ["id", "society", "flat_count"]


class TowerWithFlatsSerializer(TowerSerializer):
    flats = FlatSerializer(many=True, read_only=True)

    class Meta(TowerSerializer.Meta):
        fields = TowerSerializer.Meta.fields + ["flats"]


class BulkFlatCreateSerializer(serializers.Serializer):
    """Generates a tower's flats in one call.

    Mapping a 20-floor tower by hand is 80+ requests and the single most tedious
    part of onboarding a society, so it is worth a dedicated endpoint.
    """

    tower = serializers.PrimaryKeyRelatedField(queryset=Tower.objects.all())
    floors = serializers.IntegerField(min_value=1, max_value=100)
    flats_per_floor = serializers.IntegerField(min_value=1, max_value=20)
    numbering_start = serializers.IntegerField(
        default=1, help_text="First flat number on each floor, e.g. 1 -> 101, 102."
    )

    def validate_tower(self, tower):
        request = self.context["request"]
        if tower.society_id != request.user.society_id:
            raise serializers.ValidationError("That tower belongs to another society.")
        return tower

    @transaction.atomic
    def create(self, validated_data):
        tower = validated_data["tower"]
        floors = validated_data["floors"]
        per_floor = validated_data["flats_per_floor"]
        start = validated_data["numbering_start"]

        existing = set(tower.flats.values_list("number", flat=True))
        new_flats = []
        for floor in range(1, floors + 1):
            for unit in range(per_floor):
                number = f"{floor}{str(start + unit).zfill(2)}"
                if number not in existing:
                    new_flats.append(Flat(tower=tower, number=number, floor=floor))

        Flat.objects.bulk_create(new_flats)

        tower.floors = floors
        tower.save(update_fields=["floors", "updated_at"])
        return {"created": len(new_flats), "skipped": floors * per_floor - len(new_flats)}


# ---------------------------------------------------------------------------
# 2.5 Gates
# ---------------------------------------------------------------------------


class GateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Gate
        fields = ["id", "society", "name", "is_active"]
        read_only_fields = ["id", "society"]


# ---------------------------------------------------------------------------
# 2.3 / 2.4 Residents
# ---------------------------------------------------------------------------


class ResidentSerializer(serializers.ModelSerializer):
    """Read view. Used by the resident themselves and by administrators."""

    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    is_approved = serializers.BooleanField(source="user.is_approved", read_only=True)
    flat_label = serializers.SerializerMethodField()
    household_size = serializers.SerializerMethodField()

    class Meta:
        model = Resident
        fields = [
            "id", "user", "full_name", "phone_number", "is_approved",
            "flat", "flat_label", "relationship", "is_primary",
            "proof_document", "move_in_date",
            "reviewed_at", "rejection_reason", "household_size", "created_at",
        ]
        read_only_fields = [
            "id", "user", "is_primary", "reviewed_at", "rejection_reason", "created_at",
        ]

    def get_flat_label(self, obj) -> str:
        return f"{obj.flat.tower.name}-{obj.flat.number}"

    def get_household_size(self, obj) -> int:
        return obj.flat.residents.count()


class ResidentProfileCreateSerializer(serializers.ModelSerializer):
    """Module 2.3 — a registered resident claims their flat.

    Runs AFTER account registration, so the resident is authenticated but not
    yet approved. Becoming primary is automatic only when the flat has no
    primary yet (Module 2.4).
    """

    class Meta:
        model = Resident
        fields = ["flat", "relationship", "proof_document", "move_in_date"]

    def validate_flat(self, flat):
        user = self.context["request"].user

        # A resident may only claim a flat inside the society they registered
        # against; anything else is a cross-tenant write.
        if flat.tower.society_id != user.society_id:
            raise serializers.ValidationError("That flat belongs to another society.")
        return flat

    def validate(self, attrs):
        user = self.context["request"].user
        if Resident.objects.filter(user=user).exists():
            raise serializers.ValidationError(
                "You already have a resident profile. Contact your administrator to change flats."
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        flat = validated_data["flat"]

        # First resident in a flat becomes its primary account holder; later
        # ones join the household and an administrator can reassign primacy.
        has_primary = Resident.objects.filter(flat=flat, is_primary=True).exists()

        return Resident.objects.create(
            user=user, is_primary=not has_primary, **validated_data
        )


class ResidentApprovalSerializer(serializers.Serializer):
    """Administrator decision on a pending resident (Module 2.3)."""

    approve = serializers.BooleanField()
    rejection_reason = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )

    def validate(self, attrs):
        if not attrs["approve"] and not attrs.get("rejection_reason"):
            raise serializers.ValidationError(
                {"rejection_reason": "Give a reason so the resident knows what to correct."}
            )
        return attrs


class SetPrimaryResidentSerializer(serializers.Serializer):
    """Module 2.4 — hand primary account holder status to another household member."""

    resident_id = serializers.IntegerField()

    def validate_resident_id(self, value):
        resident = Resident.objects.filter(pk=value).select_related("flat__tower").first()
        if resident is None:
            raise serializers.ValidationError("No such resident.")

        request = self.context["request"]
        if resident.flat.tower.society_id != request.user.society_id:
            raise serializers.ValidationError("That resident belongs to another society.")
        self.context["resident"] = resident
        return value

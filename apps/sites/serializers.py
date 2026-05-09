"""
apps/sites/serializers.py

Read serializers return full detail.
Write serializers accept input — org/scope_node/created_by are set by the view/service.
"""

from rest_framework import serializers

from .models import Client, SiteProfile, SiteCommercial, SiteRoleRequirement


# ─── Client ───────────────────────────────────────────────────────────────────

class ClientSerializer(serializers.ModelSerializer):
    """Read serializer — used for list, retrieve, and create/update responses."""
    class Meta:
        model = Client
        fields = [
            'id', 'org', 'name', 'code', 'contact_name', 'contact_email',
            'contact_phone', 'industry', 'billing_address', 'gst_number',
            'scope_node', 'created_by', 'owner_sales_user',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ClientWriteSerializer(serializers.ModelSerializer):
    """Write serializer — scope_node/org/created_by injected by view."""
    class Meta:
        model = Client
        fields = [
            'org', 'name', 'code', 'contact_name', 'contact_email',
            'contact_phone', 'industry', 'billing_address', 'gst_number',
            'owner_sales_user', 'is_active',
        ]
        extra_kwargs = {
            'org': {'required': False, 'allow_null': True},
        }
        validators = []


# ─── Site ─────────────────────────────────────────────────────────────────────

class SiteProfileSerializer(serializers.ModelSerializer):
    """Read serializer."""
    class Meta:
        model = SiteProfile
        fields = [
            'id', 'org', 'client', 'scope_node', 'name', 'code',
            'address', 'city', 'state', 'pincode',
            'latitude', 'longitude', 'geofence_radius_meters',
            'shift_type', 'contact_person', 'contact_phone', 'contact_email',
            'created_by', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class SiteProfileWriteSerializer(serializers.ModelSerializer):
    """Write serializer — client must be in actor scope (validated in view)."""
    class Meta:
        model = SiteProfile
        fields = [
            'client', 'name', 'code', 'address', 'city', 'state', 'pincode',
            'latitude', 'longitude', 'geofence_radius_meters',
            'shift_type', 'contact_person', 'contact_phone', 'contact_email',
            'is_active',
        ]


# ─── Site Commercial ──────────────────────────────────────────────────────────

class SiteCommercialSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteCommercial
        fields = [
            'id', 'site', 'billing_rate', 'approved_budget_min', 'approved_budget_max',
            'effective_from', 'effective_to', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


# ─── Site Role Requirement ────────────────────────────────────────────────────

class SiteRoleRequirementSerializer(serializers.ModelSerializer):
    """Read serializer."""
    class Meta:
        model = SiteRoleRequirement
        fields = [
            'id', 'site', 'job_role', 'approved_headcount',
            'billing_type', 'billing_rate', 'wage_min', 'wage_max',
            'shift_hours', 'wage_category',
            'effective_from', 'effective_to', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class SiteRoleRequirementWriteSerializer(serializers.ModelSerializer):
    """Write serializer with field-level validation."""

    class Meta:
        model = SiteRoleRequirement
        fields = [
            'site', 'job_role', 'approved_headcount',
            'billing_type', 'billing_rate', 'wage_min', 'wage_max',
            'shift_hours', 'wage_category',
            'effective_from', 'effective_to', 'is_active',
        ]

    def validate_approved_headcount(self, value):
        if value < 1:
            raise serializers.ValidationError("approved_headcount must be at least 1.")
        return value

    def validate(self, data):
        wage_min = data.get('wage_min')
        wage_max = data.get('wage_max')
        if wage_min is not None and wage_max is not None:
            if wage_min > wage_max:
                raise serializers.ValidationError(
                    {'wage_min': 'wage_min cannot be greater than wage_max.'}
                )

        effective_from = data.get('effective_from')
        effective_to = data.get('effective_to')
        if effective_from and effective_to:
            if effective_to < effective_from:
                raise serializers.ValidationError(
                    {'effective_to': 'effective_to cannot be before effective_from.'}
                )

        return data

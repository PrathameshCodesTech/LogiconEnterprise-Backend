"""
apps/sites/serializers.py

Read serializers return full detail.
Write serializers accept input — org/scope_node/created_by are set by the view/service.
"""

from rest_framework import serializers

from apps.core.models import Department
from apps.wages.models import LocationArea
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
    location_area_name = serializers.SerializerMethodField()
    location_area_type = serializers.SerializerMethodField()

    class Meta:
        model = SiteProfile
        fields = [
            'id', 'org', 'client', 'scope_node', 'name', 'code',
            'location_area', 'location_area_name', 'location_area_type',
            'address', 'city', 'state', 'pincode',
            'latitude', 'longitude', 'geofence_radius_meters',
            'shift_type', 'contact_person', 'contact_phone', 'contact_email',
            'created_by', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_location_area_name(self, obj):
        return obj.location_area.name if obj.location_area_id else None

    def get_location_area_type(self, obj):
        return obj.location_area.area_type if obj.location_area_id else None


class SiteProfileWriteSerializer(serializers.ModelSerializer):
    """Write serializer — client must be in actor scope (validated in view)."""
    location_area = serializers.PrimaryKeyRelatedField(
        queryset=LocationArea.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = SiteProfile
        fields = [
            'client', 'name', 'code', 'location_area',
            'address', 'city', 'state', 'pincode',
            'latitude', 'longitude', 'geofence_radius_meters',
            'shift_type', 'contact_person', 'contact_phone', 'contact_email',
            'is_active',
        ]

    def validate_location_area(self, value):
        if value is not None and not value.is_active:
            raise serializers.ValidationError("This location area is inactive and cannot be assigned.")
        return value


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
    department_name = serializers.CharField(source='department.name', read_only=True, default=None)
    department_code = serializers.CharField(source='department.code', read_only=True, default=None)
    site_name = serializers.CharField(source='site.name', read_only=True, default=None)
    job_role_name = serializers.CharField(source='job_role.name', read_only=True, default=None)
    job_role_code = serializers.CharField(source='job_role.code', read_only=True, default=None)
    wage_category_name = serializers.CharField(source='wage_category.name', read_only=True, default=None)
    wage_category_code = serializers.CharField(source='wage_category.code', read_only=True, default=None)
    location_area_name = serializers.CharField(source='site.location_area.name', read_only=True, default=None)

    class Meta:
        model = SiteRoleRequirement
        fields = [
            'id', 'site', 'site_name', 'department', 'department_name', 'department_code',
            'job_role', 'job_role_name', 'job_role_code', 'approved_headcount',
            'billing_type', 'billing_rate', 'wage_min', 'wage_max',
            'shift_hours', 'wage_category', 'wage_category_name', 'wage_category_code',
            'location_area_name',
            'effective_from', 'effective_to', 'is_active',
            'wage_rate',
            'wage_rate_monthly_snapshot', 'wage_rate_daily_snapshot',
            'wage_rate_effective_from_snapshot', 'wage_rate_source_snapshot',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'department_name', 'department_code', 'site_name',
            'job_role_name', 'job_role_code',
            'wage_category_name', 'wage_category_code', 'location_area_name',
            'wage_rate',
            'wage_rate_monthly_snapshot', 'wage_rate_daily_snapshot',
            'wage_rate_effective_from_snapshot', 'wage_rate_source_snapshot',
            'created_at', 'updated_at',
        ]


class SiteRoleRequirementWriteSerializer(serializers.ModelSerializer):
    """Write serializer with field-level validation."""
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = SiteRoleRequirement
        fields = [
            'site', 'department', 'job_role', 'approved_headcount',
            'billing_type', 'billing_rate', 'wage_min', 'wage_max',
            'shift_hours', 'wage_category',
            'effective_from', 'effective_to', 'is_active',
        ]
        # Disable auto-generated unique constraint validators so partial PATCH
        # (which omits fields like is_active used in condition) doesn't KeyError.
        validators = []

    def validate_approved_headcount(self, value):
        if value < 1:
            raise serializers.ValidationError("approved_headcount must be at least 1.")
        return value

    def validate(self, data):
        instance = self.instance

        site = data.get('site', getattr(instance, 'site', None))
        department = data.get('department', getattr(instance, 'department', None))

        if department is not None and site is not None:
            if department.org_id != site.org_id:
                raise serializers.ValidationError(
                    {'department': 'Department must belong to the same organization as the site.'}
                )
            # Department must be org-level, same-client, or same-site
            if department.site_id is not None and department.site_id != site.pk:
                raise serializers.ValidationError(
                    {'department': 'Department is scoped to a different site.'}
                )
            if (
                department.site_id is None
                and department.client_id is not None
                and department.client_id != site.client_id
            ):
                raise serializers.ValidationError(
                    {'department': 'Department belongs to a different client.'}
                )

        wage_min = data.get('wage_min', getattr(instance, 'wage_min', None))
        wage_max = data.get('wage_max', getattr(instance, 'wage_max', None))
        if wage_min is not None and wage_max is not None and wage_min > wage_max:
            raise serializers.ValidationError(
                {'wage_min': 'wage_min cannot be greater than wage_max.'}
            )

        effective_from = data.get('effective_from', getattr(instance, 'effective_from', None))
        effective_to = data.get('effective_to', getattr(instance, 'effective_to', None))
        if effective_from and effective_to and effective_to < effective_from:
            raise serializers.ValidationError(
                {'effective_to': 'effective_to cannot be before effective_from.'}
            )

        return data

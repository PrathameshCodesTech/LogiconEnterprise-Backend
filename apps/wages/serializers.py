"""
apps/wages/serializers.py

Read/write serializer pairs for LocationArea, WageCategory, MinimumWageRate.
"""

from rest_framework import serializers
from .models import LocationArea, WageCategory, MinimumWageRate


# ─── Location Area ────────────────────────────────────────────────────────────

class LocationAreaSerializer(serializers.ModelSerializer):
    area_type_display = serializers.CharField(source='get_area_type_display', read_only=True)
    parent_name = serializers.CharField(source='parent.name', read_only=True, default=None)

    class Meta:
        model = LocationArea
        fields = [
            'id', 'name', 'code', 'area_type', 'area_type_display',
            'parent', 'parent_name', 'state_name', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at', 'area_type_display', 'parent_name']


class LocationAreaWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = LocationArea
        fields = ['name', 'code', 'area_type', 'parent', 'state_name', 'is_active']

    def validate(self, data):
        parent = data.get('parent', getattr(self.instance, 'parent', None))
        code = data.get('code', getattr(self.instance, 'code', None))
        qs = LocationArea.objects.filter(parent=parent, code=code)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {'code': 'A location with this code already exists under the same parent.'}
            )
        return data


# ─── Wage Category ────────────────────────────────────────────────────────────

class WageCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = WageCategory
        fields = ['id', 'name', 'code', 'description']


class WageCategoryWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = WageCategory
        fields = ['name', 'code', 'description']

    def validate_code(self, value):
        qs = WageCategory.objects.filter(code=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A wage category with this code already exists.')
        return value


# ─── Minimum Wage Rate ────────────────────────────────────────────────────────

class MinimumWageRateSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True, default=None)
    role_name = serializers.CharField(source='role.name', read_only=True, default=None)
    role_code = serializers.CharField(source='role.code', read_only=True, default=None)
    wage_category_name = serializers.CharField(source='wage_category.name', read_only=True)

    class Meta:
        model = MinimumWageRate
        fields = [
            'id', 'org', 'location', 'location_name',
            'state', 'city', 'wage_category', 'wage_category_name',
            'role', 'role_name', 'role_code',
            'monthly_wage', 'daily_wage',
            'effective_from', 'effective_to',
            'source_note', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class MinimumWageRateWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MinimumWageRate
        fields = [
            'org', 'location', 'state', 'city',
            'wage_category', 'role',
            'monthly_wage', 'daily_wage',
            'effective_from', 'effective_to',
            'source_note', 'is_active',
        ]

    def validate_monthly_wage(self, value):
        if value <= 0:
            raise serializers.ValidationError('monthly_wage must be positive.')
        return value

    def validate_daily_wage(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError('daily_wage must be positive if provided.')
        return value

    def validate(self, data):
        instance = self.instance

        eff_from = data.get('effective_from', getattr(instance, 'effective_from', None))
        eff_to = data.get('effective_to', getattr(instance, 'effective_to', None))
        if eff_from and eff_to and eff_to < eff_from:
            raise serializers.ValidationError(
                {'effective_to': 'effective_to cannot be before effective_from.'}
            )

        # Duplicate active rate guard
        org = data.get('org', getattr(instance, 'org', None))
        location = data.get('location', getattr(instance, 'location', None))
        state = data.get('state', getattr(instance, 'state', ''))
        city = data.get('city', getattr(instance, 'city', None))
        wage_category = data.get('wage_category', getattr(instance, 'wage_category', None))
        role = data.get('role', getattr(instance, 'role', None))
        is_active = data.get('is_active', getattr(instance, 'is_active', True))

        if is_active and eff_from:
            qs = MinimumWageRate.objects.filter(
                org=org,
                wage_category=wage_category,
                role=role,
                effective_from=eff_from,
                is_active=True,
            )
            if location:
                qs = qs.filter(location=location)
            else:
                qs = qs.filter(location__isnull=True, state=state or '', city=city or '')
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    'An active wage rate already exists for this '
                    'org / location / category / role / effective_from combination.'
                )

        return data

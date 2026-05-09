from rest_framework import serializers
from .models import WageCategory, MinimumWageRate


class WageCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = WageCategory
        fields = ['id', 'name', 'code', 'description']


class MinimumWageRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MinimumWageRate
        fields = [
            'id', 'state', 'city', 'wage_category', 'monthly_wage', 'daily_wage',
            'effective_from', 'effective_to', 'source_note', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

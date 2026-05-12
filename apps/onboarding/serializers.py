"""
apps/onboarding/serializers.py
"""

from rest_framework import serializers

from .models import ClientOnboardingRequest


class ClientOnboardingRequestSerializer(serializers.ModelSerializer):
    requested_by_username = serializers.CharField(
        source='requested_by.username', read_only=True,
    )
    client_name = serializers.CharField(source='client.name', read_only=True)

    class Meta:
        model = ClientOnboardingRequest
        fields = [
            'id', 'org', 'client', 'client_name',
            'requested_by', 'requested_by_username',
            'status', 'onboarding_type', 'expected_site_count',
            'summary', 'operations_notes', 'hr_notes', 'finance_notes',
            'submitted_at', 'approved_at', 'rejected_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'org', 'client', 'client_name',
            'requested_by', 'requested_by_username',
            'status', 'submitted_at', 'approved_at', 'rejected_at',
            'created_at', 'updated_at',
        ]


class ClientOnboardingRequestWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingRequest
        fields = [
            'id', 'org', 'client', 'onboarding_type',
            'expected_site_count', 'summary',
            'operations_notes', 'hr_notes', 'finance_notes',
        ]
        read_only_fields = ['id', 'org']

    def create(self, validated_data):
        validated_data['requested_by'] = self.context['request'].user
        return super().create(validated_data)

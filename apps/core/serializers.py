from rest_framework import serializers
from .models import Organization, ScopeNode


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['id', 'name', 'code', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class ScopeNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScopeNode
        fields = [
            'id', 'org', 'parent', 'name', 'code', 'node_type',
            'path', 'depth', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

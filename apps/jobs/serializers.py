from rest_framework import serializers
from .models import JobRole


class JobRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobRole
        fields = ['id', 'org', 'name', 'code', 'description', 'skill_category', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

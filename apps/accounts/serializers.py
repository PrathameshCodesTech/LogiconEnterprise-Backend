"""
apps/accounts/serializers.py

User serializers for list, create, and update operations.
"""

from rest_framework import serializers

from apps.core.models import Department
from .models import User


class UserListSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True, default=None)
    department_code = serializers.CharField(source='department.code', read_only=True, default=None)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'phone_number', 'phone_normalized', 'employee_code',
            'user_type', 'org', 'department', 'department_name', 'department_code',
            'is_active', 'is_invited',
            'last_invited_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={'input_type': 'password'},
    )
    employee_code = serializers.CharField(required=False, allow_blank=True)
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = User
        fields = [
            'username', 'email', 'first_name', 'last_name',
            'phone_number', 'employee_code', 'user_type',
            'org', 'department', 'is_active', 'is_invited', 'password',
        ]
        validators = []

    def validate_user_type(self, value):
        allowed = {'internal', 'client', 'field'}
        if value not in allowed:
            raise serializers.ValidationError(
                f"user_type must be one of: {', '.join(sorted(allowed))}."
            )
        return value

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = User
        fields = [
            'email', 'first_name', 'last_name',
            'phone_number', 'employee_code', 'user_type',
            'department', 'is_active', 'is_invited',
        ]
        validators = []

    def validate_user_type(self, value):
        allowed = {'internal', 'client', 'field'}
        if value not in allowed:
            raise serializers.ValidationError(
                f"user_type must be one of: {', '.join(sorted(allowed))}."
            )
        return value

from rest_framework import serializers
from .models import Employee, SiteDeployment


class EmployeeSerializer(serializers.ModelSerializer):
    full_name = serializers.ReadOnlyField()

    class Meta:
        model = Employee
        fields = [
            'id', 'org', 'candidate', 'user', 'employee_code',
            'first_name', 'middle_name', 'last_name', 'full_name',
            'phone', 'phone_normalized', 'email',
            'job_role', 'status', 'joined_on', 'exited_on',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['phone_normalized', 'created_at', 'updated_at']


class SiteDeploymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteDeployment
        fields = [
            'id', 'org', 'employee', 'site', 'job_role',
            'mrf_line_item', 'hiring_application',
            'status', 'start_date', 'end_date',
            'shift_hours', 'billing_type', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

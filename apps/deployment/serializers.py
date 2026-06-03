from datetime import date
from decimal import Decimal

from rest_framework import serializers

from .models import DeploymentHistory, Employee, SiteDeployment


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
    employee_full_name = serializers.CharField(
        source='employee.full_name', read_only=True, default=None,
    )
    employee_code = serializers.CharField(
        source='employee.employee_code', read_only=True, default=None,
    )
    site_name = serializers.CharField(source='site.name', read_only=True, default=None)
    job_role_name = serializers.CharField(source='job_role.name', read_only=True, default=None)

    class Meta:
        model = SiteDeployment
        fields = [
            'id', 'org', 'employee', 'employee_full_name', 'employee_code',
            'site', 'site_name',
            'job_role', 'job_role_name',
            'mrf_line_item', 'hiring_application',
            'status', 'start_date', 'end_date',
            'shift_hours', 'billing_type', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


# ─── Conversion ───────────────────────────────────────────────────────────────

class HiringDeploymentConversionSerializer(serializers.Serializer):
    """Input payload for POST convert-to-deployment."""

    DEPLOYMENT_STATUS_CHOICES = [c[0] for c in SiteDeployment.STATUS_CHOICES]
    BILLING_TYPE_CHOICES = [c[0] for c in SiteDeployment.BILLING_TYPE_CHOICES]

    employee_code = serializers.CharField(required=False, allow_blank=True, default=None)
    joined_on = serializers.DateField(required=False, allow_null=True, default=None)
    deployment_start_date = serializers.DateField(required=False, allow_null=True, default=None)
    deployment_status = serializers.ChoiceField(
        choices=DEPLOYMENT_STATUS_CHOICES, default='planned',
    )
    shift_hours = serializers.DecimalField(
        max_digits=4, decimal_places=1, required=False, allow_null=True, default=None,
        min_value=Decimal('0'),
    )
    billing_type = serializers.ChoiceField(
        choices=BILLING_TYPE_CHOICES, required=False, allow_null=True, default=None,
    )
    allow_existing_employee = serializers.BooleanField(required=False, default=False)

    def validate_employee_code(self, value):
        if value == '':
            return None
        return value

    def validate_shift_hours(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError('shift_hours must be positive.')
        return value


class HiringDeploymentConversionResultSerializer(serializers.Serializer):
    """Response shape for a successful conversion."""
    application = serializers.SerializerMethodField()
    employee = serializers.SerializerMethodField()
    deployment = serializers.SerializerMethodField()
    created_employee = serializers.BooleanField()
    created_deployment = serializers.BooleanField()

    def get_application(self, obj):
        from apps.hiring.serializers import HiringApplicationReadSerializer
        return HiringApplicationReadSerializer(
            obj['application'], context=self.context,
        ).data

    def get_employee(self, obj):
        return EmployeeSerializer(obj['employee'], context=self.context).data

    def get_deployment(self, obj):
        return SiteDeploymentSerializer(obj['deployment'], context=self.context).data


# ─── Lifecycle action input serializers ───────────────────────────────────────

class DeploymentNoteSerializer(serializers.Serializer):
    """Used by activate / cancel actions."""
    note = serializers.CharField(required=False, allow_blank=True, default='')


class DeploymentCompleteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default='')
    end_date = serializers.DateField(required=False, allow_null=True, default=None)


class DeploymentTransferSerializer(serializers.Serializer):
    site = serializers.IntegerField()
    job_role = serializers.IntegerField(required=False, allow_null=True, default=None)
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    activate_new = serializers.BooleanField(required=False, default=False)
    note = serializers.CharField(required=False, allow_blank=True, default='')


class EmployeeNoteSerializer(serializers.Serializer):
    """Used by employee suspend / reactivate."""
    note = serializers.CharField(required=False, allow_blank=True, default='')


class EmployeeExitSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default='')
    exited_on = serializers.DateField(required=False, allow_null=True, default=None)


# ─── DeploymentHistory (read) ─────────────────────────────────────────────────

class DeploymentHistorySerializer(serializers.ModelSerializer):
    employee_full_name = serializers.CharField(
        source='employee.full_name', read_only=True, default=None,
    )
    employee_code = serializers.CharField(
        source='employee.employee_code', read_only=True, default=None,
    )
    from_site_name = serializers.CharField(
        source='from_site.name', read_only=True, default=None,
    )
    to_site_name = serializers.CharField(
        source='to_site.name', read_only=True, default=None,
    )
    from_job_role_name = serializers.CharField(
        source='from_job_role.name', read_only=True, default=None,
    )
    to_job_role_name = serializers.CharField(
        source='to_job_role.name', read_only=True, default=None,
    )
    actor_username = serializers.CharField(
        source='actor.username', read_only=True, default=None,
    )

    class Meta:
        model = DeploymentHistory
        fields = [
            'id', 'org', 'employee', 'employee_full_name', 'employee_code',
            'deployment',
            'action_type',
            'from_status', 'to_status',
            'from_site', 'from_site_name', 'to_site', 'to_site_name',
            'from_job_role', 'from_job_role_name',
            'to_job_role', 'to_job_role_name',
            'actor', 'actor_username',
            'note', 'metadata',
            'created_at',
        ]
        read_only_fields = fields

"""
apps/budgets/serializers.py
"""

from decimal import Decimal

from django.db.models import Q, Sum
from rest_framework import serializers

from apps.sites.models import Client, SiteProfile

from .models import BudgetPlan


def _decimal_str(val):
    if val is None:
        return '0.00'
    return str(val.quantize(Decimal('0.01')))


class BudgetPlanSerializer(serializers.ModelSerializer):
    """Read serializer — full detail including denormalized name fields."""
    client_name = serializers.CharField(source='client.name', read_only=True, default=None)
    site_name = serializers.CharField(source='site.name', read_only=True, default=None)
    department_name = serializers.CharField(source='department.name', read_only=True, default=None)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)
    updated_by_username = serializers.CharField(source='updated_by.username', read_only=True, default=None)

    reserved_amount = serializers.SerializerMethodField()
    committed_amount = serializers.SerializerMethodField()
    available_amount = serializers.SerializerMethodField()

    class Meta:
        model = BudgetPlan
        fields = [
            'id', 'org', 'name', 'code',
            'budget_nature', 'budget_type',
            'client', 'client_name',
            'site', 'site_name',
            'department', 'department_name',
            'period_start', 'period_end',
            'amount', 'currency',
            'reserved_amount', 'committed_amount', 'available_amount',
            'status', 'notes', 'is_active',
            'created_by', 'created_by_username',
            'updated_by', 'updated_by_username',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'created_at', 'updated_at',
            'client_name', 'site_name', 'department_name',
            'created_by_username', 'updated_by_username',
            'reserved_amount', 'committed_amount', 'available_amount',
        ]

    def get_reserved_amount(self, obj):
        from .models import BudgetReservation
        result = BudgetReservation.objects.filter(
            budget_plan=obj, status='reserved',
        ).aggregate(total=Sum('amount'))['total']
        return _decimal_str(result)

    def get_committed_amount(self, obj):
        from .models import BudgetReservation
        result = BudgetReservation.objects.filter(
            budget_plan=obj, status='committed',
        ).aggregate(total=Sum('amount'))['total']
        return _decimal_str(result)

    def get_available_amount(self, obj):
        from .models import BudgetReservation
        used = BudgetReservation.objects.filter(
            budget_plan=obj, status__in=('reserved', 'committed'),
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        return _decimal_str(obj.amount - used)


class BudgetPlanWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetPlan
        fields = [
            'org', 'name', 'code',
            'budget_nature', 'budget_type',
            'client', 'site', 'department',
            'period_start', 'period_end',
            'amount', 'currency',
            'status', 'notes', 'is_active',
        ]
        validators = []
        extra_kwargs = {
            'org': {'required': False, 'allow_null': True},
        }

    def validate(self, data):
        instance = self.instance

        # Resolve fields with fallback to existing instance values
        def get(field):
            return data.get(field, getattr(instance, field, None) if instance else None)

        org = self.context.get('actor_org') or get('org')
        budget_nature = get('budget_nature')
        client = get('client')
        site = get('site')
        department = get('department')
        period_start = get('period_start')
        period_end = get('period_end')
        amount = get('amount')

        errors = {}

        # amount must be positive
        if amount is not None and amount <= 0:
            errors['amount'] = 'Amount must be greater than zero.'

        # period_end >= period_start
        if period_start and period_end and period_end < period_start:
            errors['period_end'] = 'period_end must be on or after period_start.'

        # org FK consistency
        if org:
            if client and client.org_id != org.pk:
                errors['client'] = 'Client does not belong to this org.'
            if site and site.org_id != org.pk:
                errors['site'] = 'Site does not belong to this org.'
            if department and department.org_id != org.pk:
                errors['department'] = 'Department does not belong to this org.'

        # site.client must match client if both provided
        if site and client and site.client_id != client.pk:
            errors['site'] = 'Site does not belong to the selected client.'

        # auto-fill client from site when client not explicitly provided
        if site and not client and not errors.get('site'):
            data['client'] = site.client
            client = site.client

        if budget_nature == 'billable':
            if not client:
                errors['client'] = 'Billable budget requires a client.'

        elif budget_nature == 'non_billable':
            if client or site:
                errors_list = []
                if client:
                    errors['client'] = 'Non-billable budget must not have a client.'
                if site:
                    errors['site'] = 'Non-billable budget must not have a site.'
            if not department:
                errors['department'] = 'Non-billable budget requires a department.'

        if errors:
            raise serializers.ValidationError(errors)

        return data

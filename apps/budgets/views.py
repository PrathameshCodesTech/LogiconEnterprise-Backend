"""
apps/budgets/views.py
"""

from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_budget_plans_for_user
from apps.access.viewsets import ActionCapabilityMixin, ReadAfterWriteMixin

from .models import BudgetPlan
from .serializers import BudgetPlanSerializer, BudgetPlanWriteSerializer


class BudgetPlanViewSet(ReadAfterWriteMixin, ActionCapabilityMixin, viewsets.ModelViewSet):
    """
    CRUD for BudgetPlan.

    Capability gates:
      list/retrieve   -> budget.read
      create          -> budget.create
      update          -> budget.update
      destroy         -> budget.delete  (soft-deactivate)
    """
    read_serializer_class = BudgetPlanSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['org', 'budget_nature', 'budget_type', 'client', 'site', 'department', 'status', 'is_active']
    search_fields = ['name', 'code', 'client__name', 'site__name', 'department__name']
    ordering_fields = ['created_at', 'name', 'code', 'period_start', 'amount']
    ordering = ['-created_at']

    action_required_capabilities = {
        'list':           'budget.read',
        'retrieve':       'budget.read',
        'create':         'budget.create',
        'update':         'budget.update',
        'partial_update': 'budget.update',
        'destroy':        'budget.delete',
    }

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        actor = self.request.user
        if not actor.is_superuser and hasattr(actor, 'org') and actor.org:
            ctx['actor_org'] = actor.org
        return ctx

    def get_queryset(self):
        qs = BudgetPlan.objects.select_related(
            'org', 'client', 'site', 'department', 'created_by', 'updated_by'
        ).order_by('-created_at')
        if self.request.user.is_superuser:
            return qs
        return filter_budget_plans_for_user(qs.filter(org=self.request.user.org), self.request.user)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return BudgetPlanWriteSerializer
        return BudgetPlanSerializer

    def perform_create(self, serializer):
        actor = self.request.user
        org = serializer.validated_data.get('org') if actor.is_superuser else actor.org
        if not org:
            raise serializers.ValidationError({'org': 'org is required.'})
        budget = serializer.save(org=org, created_by=actor, updated_by=actor)
        return budget

    def perform_update(self, serializer):
        actor = self.request.user
        kwargs = {'updated_by': actor}
        if not actor.is_superuser:
            kwargs['org'] = actor.org
        serializer.save(**kwargs)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.status = 'inactive'
        instance.save(update_fields=['is_active', 'status'])

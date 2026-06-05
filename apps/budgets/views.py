"""
apps/budgets/views.py
"""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_budget_plans_for_user
from apps.access.viewsets import ActionCapabilityMixin, ReadAfterWriteMixin

from .models import BudgetPlan
from .serializers import BudgetPlanSerializer, BudgetPlanWriteSerializer
from .services import get_budget_plan_totals


def _money(value):
    return f'{value:.2f}'


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
        'client_commercials': 'budget.read',
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

    @action(detail=True, methods=['get'], url_path='client-commercials')
    def client_commercials(self, request, pk=None):
        """
        Client-safe commercial view for an approved budget.

        This intentionally does not expose internal sales/proposal workflow fields
        such as internal approval status, sales remarks, or override metadata.
        """
        budget = self.get_object()
        proposal = budget.source_proposal_version
        totals = get_budget_plan_totals(budget)

        proposal_payload = None
        budget_lines = []
        breakup_lines = []

        if (
            proposal is not None
            and proposal.client_approval_status == 'approved'
            and proposal.is_final_approved_version
        ):
            proposal_payload = {
                'id': proposal.pk,
                'lead': proposal.lead_id,
                'version_number': proposal.version_number,
                'grand_total': str(proposal.grand_total),
                'subtotal_amount': str(proposal.subtotal_amount),
                'management_fee_amount': str(proposal.management_fee_amount),
                'gst_amount': str(proposal.gst_amount),
                'manpower_total': proposal.manpower_total,
                'management_fee_percent': (
                    str(proposal.management_fee_percent)
                    if proposal.management_fee_percent is not None else None
                ),
                'gst_applicable': proposal.gst_applicable,
                'client_approval_status': proposal.client_approval_status,
                'client_approved_at': proposal.client_approved_at,
                'validity_days': proposal.validity_days,
            }
            budget_lines = [
                {
                    'id': line.pk,
                    'site': line.site_id,
                    'site_name': line.site.site_name if line.site_id else None,
                    'role_requirement': line.role_requirement_id,
                    'service_category': line.service_category,
                    'job_role': line.job_role_id,
                    'job_role_name': line.job_role.name if line.job_role_id else None,
                    'description': line.description,
                    'manpower_count': line.manpower_count,
                    'unit_cost': str(line.unit_cost),
                    'total_cost': str(line.total_cost),
                    'sort_order': line.sort_order,
                }
                for line in proposal.budget_lines.select_related('site', 'job_role').order_by('sort_order', 'pk')
            ]
            breakup_lines = [
                {
                    'id': line.pk,
                    'site': line.site_id,
                    'site_name': line.site.site_name if line.site_id else None,
                    'role_requirement': line.role_requirement_id,
                    'job_role': line.job_role_id,
                    'job_role_name': line.job_role.name if line.job_role_id else None,
                    'component_name': line.component_name,
                    'component_type': line.component_type,
                    'percentage': str(line.percentage) if line.percentage is not None else None,
                    'amount': str(line.amount),
                    'sort_order': line.sort_order,
                }
                for line in proposal.breakup_lines.select_related('site', 'job_role').order_by('sort_order', 'pk')
            ]

        return Response({
            'budget': {
                'id': budget.pk,
                'name': budget.name,
                'code': budget.code,
                'budget_nature': budget.budget_nature,
                'budget_type': budget.budget_type,
                'client': budget.client_id,
                'client_name': budget.client.name if budget.client_id else None,
                'site': budget.site_id,
                'site_name': budget.site.name if budget.site_id else None,
                'period_start': budget.period_start,
                'period_end': budget.period_end,
                'amount': str(budget.amount),
                'reserved_amount': _money(totals['reserved_amount']),
                'committed_amount': _money(totals['committed_amount']),
                'available_amount': _money(totals['available_amount']),
                'currency': budget.currency,
                'status': budget.status,
                'is_active': budget.is_active,
                'source_type': budget.source_type,
                'source_sales_lead': budget.source_sales_lead_id,
                'source_proposal_version': budget.source_proposal_version_id,
            },
            'proposal': proposal_payload,
            'budget_lines': budget_lines,
            'breakup_lines': breakup_lines,
        })

"""
apps/mrf/views.py

Authenticated management endpoints for ManpowerRequest and MRFLineItem.

Capability map:
  ManpowerRequest   mrf.read / mrf.create / mrf.update / mrf.delete
  MRFLineItem       mrf.read (list/retrieve) / mrf.update (create/update/delete)

Approval workflow (mrf.approve / mrf.reject) is deferred to Phase 4F+.
"""

from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_mrf_line_items_for_user, filter_mrfs_for_user
from apps.access.viewsets import ScopedModelViewSet

from .models import ManpowerRequest, MRFLineItem
from .serializers import (
    ManpowerRequestSerializer,
    ManpowerRequestWriteSerializer,
    MRFLineItemSerializer,
    MRFLineItemWriteSerializer,
    MRFSupportRequirementSerializer,
    MRFSupportRequirementWriteSerializer,
)


# ─── ManpowerRequest ViewSet ──────────────────────────────────────────────────

class ManpowerRequestViewSet(ScopedModelViewSet):
    """
    Full CRUD for MRF (Manpower Requisition Forms).

    DELETE performs a hard delete (ManpowerRequest has no is_active field).
    Approval/rejection transitions are deferred to Phase 4F+ workflow endpoints.
    """
    queryset = ManpowerRequest.objects.select_related(
        'org', 'site', 'requested_by',
        'requesting_department', 'required_department',
        'site__scope_node', 'site__client__scope_node',
        'budget_plan', 'support_requirement',
    ).prefetch_related(
        'line_items__job_role',
        'line_items__budget_plan',
        'workflow_instances__steps__assigned_user',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_mrfs_for_user

    filterset_fields = [
        'org', 'site', 'mrf_type', 'status', 'billing_type',
        'requested_by', 'requested_by_type', 'client_visible',
        'requesting_department', 'required_department',
    ]
    search_fields = [
        'department', 'reason',
        'requesting_department__name', 'requesting_department__code',
        'required_department__name', 'required_department__code',
    ]

    action_required_capabilities = {
        'list':           'mrf.read',
        'retrieve':       'mrf.read',
        'create':         'mrf.create',
        'update':         'mrf.update',
        'partial_update': 'mrf.update',
        'destroy':        'mrf.delete',
        'readiness':      'mrf.read',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ManpowerRequestWriteSerializer
        return ManpowerRequestSerializer

    def _check_site_scope(self, site):
        """Raise PermissionDenied if actor cannot access the given site."""
        user = self.request.user
        if user.is_superuser:
            return
        from apps.access.querysets import filter_sites_for_user
        from apps.sites.models import SiteProfile
        if not filter_sites_for_user(SiteProfile.objects.filter(pk=site.pk), user).exists():
            raise PermissionDenied("You do not have access to this site.")

    def perform_create(self, serializer):
        from apps.access.capabilities import is_client_facing_user
        site = serializer.validated_data['site']
        self._check_site_scope(site)
        extra = {}
        is_client_actor = is_client_facing_user(self.request.user)
        if not is_client_actor and serializer.validated_data.get('requesting_department') is None:
            extra['requesting_department'] = getattr(self.request.user, 'department', None)
        if is_client_actor:
            extra['requested_by_type'] = 'client'
            extra['requesting_department'] = None
            extra['required_department'] = None
        serializer.save(org=site.org, requested_by=self.request.user, **extra)

    def perform_update(self, serializer):
        from apps.access.capabilities import is_client_facing_user
        site = serializer.validated_data.get('site', serializer.instance.site)
        self._check_site_scope(site)
        extra = {}
        if is_client_facing_user(self.request.user):
            extra['requested_by_type'] = 'client'
        serializer.save(**extra)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        out = ManpowerRequestSerializer(serializer.instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        # Re-fetch so nested writes (support_requirement) are visible in response.
        fresh = self.get_queryset().get(pk=serializer.instance.pk)
        out = ManpowerRequestSerializer(fresh, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['get'], url_path='readiness')
    def readiness(self, request, pk=None):
        from apps.mrf.services import check_mrf_readiness
        mrf = self.get_object()
        result = check_mrf_readiness(mrf)
        return Response(result)


# ─── MRFLineItem ViewSet ──────────────────────────────────────────────────────

class MRFLineItemViewSet(ScopedModelViewSet):
    """
    Full CRUD for MRF line items.

    create/update/delete require mrf.update (line items are sub-resources of MRF).
    DELETE performs a hard delete (MRFLineItem has no is_active field).
    """
    queryset = MRFLineItem.objects.select_related(
        'mrf', 'job_role', 'site_role_requirement', 'wage_category',
        'mrf__site', 'mrf__site__scope_node', 'mrf__site__client__scope_node',
        'budget_plan',
    ).order_by('mrf_id', 'id')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_mrf_line_items_for_user

    filterset_fields = ['mrf', 'job_role', 'site_role_requirement', 'wage_category']

    action_required_capabilities = {
        'list':           'mrf.read',
        'retrieve':       'mrf.read',
        'create':         'mrf.update',
        'update':         'mrf.update',
        'partial_update': 'mrf.update',
        'destroy':        'mrf.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return MRFLineItemWriteSerializer
        return MRFLineItemSerializer

    def _check_mrf_scope(self, mrf):
        """Raise PermissionDenied if actor cannot access the parent MRF's site."""
        user = self.request.user
        if user.is_superuser:
            return
        if not filter_mrfs_for_user(
            ManpowerRequest.objects.filter(pk=mrf.pk), user
        ).exists():
            raise PermissionDenied("You do not have access to this MRF.")

    def _apply_commercial_extras(self, serializer, extra, srr, mrf):
        """Populate master snapshots and override audit fields into extra."""
        from apps.mrf.services import build_line_item_commercial_snapshot
        from django.utils import timezone

        if srr:
            snapshot = build_line_item_commercial_snapshot(srr)
            extra['master_wage_min_snapshot'] = snapshot['wage_min']
            extra['master_wage_max_snapshot'] = snapshot['wage_max']
            extra['master_billing_rate_snapshot'] = snapshot['billing_rate']
            extra['master_shift_hours_snapshot'] = snapshot['shift_hours']
            if serializer.validated_data.get('wage_min_requested') is None:
                extra['min_wage_snapshot'] = snapshot['wage_min']
            if serializer.validated_data.get('billing_rate_snapshot') is None and snapshot['billing_rate'] is not None:
                extra['billing_rate_snapshot'] = snapshot['billing_rate']

        override_result = getattr(serializer, '_override_result', None)
        if override_result and override_result.get('in_payload'):
            if override_result['detected']:
                extra['commercial_override_enabled'] = True
                extra['commercial_overridden_by'] = override_result['user']
                extra['commercial_overridden_at'] = timezone.now()
            else:
                extra['commercial_override_enabled'] = False
                extra['commercial_overridden_by'] = None
                extra['commercial_overridden_at'] = None

    def perform_create(self, serializer):
        mrf = serializer.validated_data['mrf']
        self._check_mrf_scope(mrf)
        srr = serializer.validated_data.get('site_role_requirement')
        extra = {}
        self._apply_commercial_extras(serializer, extra, srr, mrf)
        # Auto-inherit MRF budget when not explicitly provided
        if serializer.validated_data.get('budget_plan') is None and mrf.budget_plan_id:
            extra['budget_plan'] = mrf.budget_plan
        serializer.save(**extra)

    def perform_update(self, serializer):
        mrf = serializer.validated_data.get('mrf', serializer.instance.mrf)
        self._check_mrf_scope(mrf)
        srr = serializer.validated_data.get(
            'site_role_requirement', serializer.instance.site_role_requirement
        )
        extra = {}
        self._apply_commercial_extras(serializer, extra, srr, mrf)
        serializer.save(**extra)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        out = MRFLineItemSerializer(serializer.instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        out = MRFLineItemSerializer(serializer.instance, context={'request': request})
        return Response(out.data)

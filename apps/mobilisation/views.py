"""
apps/mobilisation/views.py

CRUD for MobilisationSetupRequest and nested proposed setup records.
"""

from rest_framework import status, viewsets, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.permissions import HasCapability
from apps.access.scope import actor_can_access_scope
from apps.access.viewsets import ActionCapabilityMixin, ReadAfterWriteMixin, ScopedModelViewSet

from .models import (
    MobilisationSetupRequest,
    MobilisationProposedDepartment,
    MobilisationProposedDepartmentRole,
    MobilisationProposedUser,
)
from .querysets import filter_mobilisation_requests_for_user
from .serializers import (
    MobilisationSetupRequestSerializer,
    MobilisationSetupRequestWriteSerializer,
    ProposedDepartmentSerializer,
    ProposedDepartmentWriteSerializer,
    ProposedDepartmentRoleSerializer,
    ProposedDepartmentRoleWriteSerializer,
    MobilisationProposedUserSerializer,
    MobilisationProposedUserWriteSerializer,
)


_EDITABLE_STATUSES = ('draft', 'operations_setup', 'rejected')


# ─── Main request viewset ─────────────────────────────────────────────────────

class MobilisationSetupRequestViewSet(ScopedModelViewSet):
    """
    CRUD for mobilisation setup requests.

    Capability map:
      list/retrieve  → mobilisation.read
      create         → mobilisation.create
      update/partial → mobilisation.update
      destroy        → mobilisation.delete
      readiness      → mobilisation.read
      finalize_directly → mobilisation.finalize
    """
    queryset = MobilisationSetupRequest.objects.select_related(
        'org', 'client', 'requested_by', 'client__scope_node',
        'assigned_operations_owner', 'setup_completed_by',
        'finalized_by', 'budget_plan',
    ).prefetch_related(
        'workflow_instances__steps__assigned_user',
        'proposed_departments',
        'proposed_department_roles__site_role_requirement',
        'proposed_users__access_role',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_mobilisation_requests_for_user

    filterset_fields = [
        'org', 'client', 'status', 'mobilisation_type',
        'requested_by', 'assigned_operations_owner',
    ]
    search_fields = ['summary', 'client__name']

    action_required_capabilities = {
        'list':              'mobilisation.read',
        'retrieve':          'mobilisation.read',
        'create':            'mobilisation.create',
        'update':            'mobilisation.update',
        'partial_update':    'mobilisation.update',
        'destroy':           'mobilisation.delete',
        'readiness':         'mobilisation.read',
        'sales_context':     'mobilisation.read',
        'setup_suggestions': 'mobilisation.read',
        'setup_builder': 'mobilisation.read',
        'apply_setup_builder_template': 'mobilisation.update',
        'apply_setup_suggestions': 'mobilisation.update',
        'assign_operations_owner': 'mobilisation.update',
        'mark_setup_completed':    'mobilisation.update',
        'finalize_directly': 'mobilisation.finalize',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return MobilisationSetupRequestWriteSerializer
        return MobilisationSetupRequestSerializer

    def get_required_capability(self):
        if self.action == 'setup_builder':
            if self.request.method.lower() == 'get':
                return 'mobilisation.read'
            return 'mobilisation.update'
        return super().get_required_capability()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        user = self.request.user
        if hasattr(user, 'org'):
            ctx['actor_org'] = user.org
        return ctx

    @action(detail=True, methods=['get'], url_path='readiness')
    def readiness(self, request, pk=None):
        """GET /api/mobilisation/setup-requests/{id}/readiness/"""
        from .services import check_mobilisation_readiness
        obj = self.get_object()
        ok, errors, warnings = check_mobilisation_readiness(obj)
        return Response({'ok': ok, 'errors': errors, 'warnings': warnings})

    @action(detail=True, methods=['get'], url_path='sales-context')
    def sales_context(self, request, pk=None):
        """
        GET /api/mobilisation/setup-requests/{id}/sales-context/

        Returns comprehensive sales context for a mobilisation request,
        including source lead, proposal, sites, roles, budget lines, and readiness.
        """
        from .services import get_mobilisation_sales_context
        obj = self.get_object()
        context_data = get_mobilisation_sales_context(obj)
        return Response(context_data)

    @action(detail=True, methods=['get'], url_path='setup-suggestions')
    def setup_suggestions(self, request, pk=None):
        """GET /api/mobilisation/setup-requests/{id}/setup-suggestions/"""
        from .services import get_mobilisation_setup_suggestions

        obj = self.get_object()
        return Response(get_mobilisation_setup_suggestions(obj))

    @action(detail=True, methods=['get', 'put'], url_path='setup-builder')
    def setup_builder(self, request, pk=None):
        """
        GET/PUT /api/mobilisation/setup-requests/{id}/setup-builder/

        GET returns editable departments/users plus SRR-to-department mappings.
        PUT saves the full builder payload.
        """
        from .services import (
            get_mobilisation_setup_builder,
            save_mobilisation_setup_builder,
        )

        obj = self.get_object()
        if request.method.lower() == 'get':
            return Response(get_mobilisation_setup_builder(obj))
        try:
            data = save_mobilisation_setup_builder(obj, request.data, actor=request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data)

    @action(detail=True, methods=['post'], url_path='setup-builder/apply-template')
    def apply_setup_builder_template(self, request, pk=None):
        """POST /api/mobilisation/setup-requests/{id}/setup-builder/apply-template/"""
        from .services import apply_mobilisation_setup_builder_template

        obj = self.get_object()
        strategy = request.data.get('setup_strategy') or request.data.get('strategy') or 'role_grouped'
        try:
            data = apply_mobilisation_setup_builder_template(
                obj,
                strategy=strategy,
                actor=request.user,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data)

    @action(detail=True, methods=['post'], url_path='apply-setup-suggestions')
    def apply_setup_suggestions(self, request, pk=None):
        """POST /api/mobilisation/setup-requests/{id}/apply-setup-suggestions/"""
        from .services import apply_mobilisation_setup_suggestions

        obj = self.get_object()
        try:
            result = apply_mobilisation_setup_suggestions(obj, actor=request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=True, methods=['post'], url_path='assign-operations-owner')
    def assign_operations_owner(self, request, pk=None):
        """POST /api/mobilisation/setup-requests/{id}/assign-operations-owner/"""
        from apps.accounts.models import User
        from .services import assign_operations_owner

        obj = self.get_object()
        owner_id = request.data.get('operations_owner') or request.data.get('assigned_operations_owner')
        if not owner_id:
            return Response(
                {'detail': 'operations_owner is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            owner = User.objects.get(pk=owner_id)
            assign_operations_owner(obj, owner, actor=request.user)
        except User.DoesNotExist:
            return Response(
                {'detail': 'Operations owner not found.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        obj.refresh_from_db()
        out = MobilisationSetupRequestSerializer(obj, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['post'], url_path='mark-setup-completed')
    def mark_setup_completed(self, request, pk=None):
        """POST /api/mobilisation/setup-requests/{id}/mark-setup-completed/"""
        from .services import mark_mobilisation_setup_completed

        obj = self.get_object()
        try:
            _, warnings = mark_mobilisation_setup_completed(obj, request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        obj.refresh_from_db()
        out = MobilisationSetupRequestSerializer(obj, context={'request': request})
        data = dict(out.data)
        data['warnings'] = warnings
        return Response(data)

    @action(detail=True, methods=['post'], url_path='finalize-directly')
    def finalize_directly(self, request, pk=None):
        """Finalize without workflow when mobilisation_requires_approval=False."""
        from .services import finalize_mobilisation_directly
        from .exceptions import MobilisationFinalizationError
        obj = self.get_object()
        override = request.data.get('override_approval_required') is True
        try:
            finalize_mobilisation_directly(
                obj,
                request.user,
                override_approval_required=override,
            )
        except (ValueError, MobilisationFinalizationError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        obj.refresh_from_db()
        out = MobilisationSetupRequestSerializer(obj, context={'request': request})
        return Response(out.data)

    def _check_client_scope(self, client):
        if client is None:
            return
        user = self.request.user
        if user.is_superuser:
            return
        if not client.scope_node:
            raise PermissionDenied("Client has no scope node configured.")
        if not actor_can_access_scope(user, client.scope_node):
            raise PermissionDenied("You do not have access to this client.")

    def _get_org_for_create(self, client):
        if client is not None:
            return client.org
        user = self.request.user
        if hasattr(user, 'org') and user.org is not None:
            return user.org
        raise ValidationError(
            "Cannot determine organization for mobilisation. "
            "Ensure your account is assigned to an organization."
        )

    def perform_create(self, serializer):
        from .services import assign_operations_owner

        client = serializer.validated_data.get('client')
        self._check_client_scope(client)
        org = self._get_org_for_create(client)
        obj = serializer.save(org=org, requested_by=self.request.user)
        owner = serializer.validated_data.get('assigned_operations_owner')
        if owner is not None:
            assign_operations_owner(obj, owner, actor=self.request.user)

    def perform_update(self, serializer):
        from .services import assign_operations_owner

        client = serializer.validated_data.get('client', serializer.instance.client)
        self._check_client_scope(client)
        if client is not None:
            obj = serializer.save(org=client.org)
        else:
            obj = serializer.save()
        owner = serializer.validated_data.get('assigned_operations_owner')
        if owner is not None:
            assign_operations_owner(obj, owner, actor=self.request.user)


# ─── Nested proposed-setup viewset base ──────────────────────────────────────

class ProposedSetupViewSetMixin(
    ReadAfterWriteMixin,
    ActionCapabilityMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Base for nested proposed-setup viewsets.
    Requires `request_pk` URL kwarg pointing to the parent MobilisationSetupRequest.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    action_required_capabilities = {
        'list':           'mobilisation.read',
        'retrieve':       'mobilisation.read',
        'create':         'mobilisation.create',
        'update':         'mobilisation.update',
        'partial_update': 'mobilisation.update',
        'destroy':        'mobilisation.delete',
    }

    def _get_mobilisation_request(self):
        from django.shortcuts import get_object_or_404
        user = self.request.user
        qs = filter_mobilisation_requests_for_user(
            MobilisationSetupRequest.objects.all(), user,
        )
        return get_object_or_404(qs, pk=self.kwargs['request_pk'])

    def _check_editable(self, mobilisation_request):
        if mobilisation_request.status not in _EDITABLE_STATUSES:
            raise PermissionDenied(
                f"Proposed setup records cannot be modified when the mobilisation request "
                f"status is '{mobilisation_request.status}'. "
                f"Edits are only allowed in: {', '.join(_EDITABLE_STATUSES)}."
            )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if 'request_pk' in self.kwargs:
            try:
                ctx['mobilisation_request'] = self._get_mobilisation_request()
            except Exception:
                pass
        return ctx

    def perform_create(self, serializer):
        req = self._get_mobilisation_request()
        self._check_editable(req)
        serializer.save(request=req)

    def perform_update(self, serializer):
        req = self._get_mobilisation_request()
        self._check_editable(req)
        if getattr(serializer.instance, 'is_locked', False):
            raise PermissionDenied("Locked proposed setup records cannot be modified.")
        serializer.save()

    def perform_destroy(self, instance):
        req = self._get_mobilisation_request()
        self._check_editable(req)
        if getattr(instance, 'is_locked', False):
            raise PermissionDenied("Locked proposed setup records cannot be deleted.")
        instance.delete()


# ─── Proposed-department viewset ──────────────────────────────────────────────

class ProposedDepartmentViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = ProposedDepartmentSerializer

    def get_queryset(self):
        req = self._get_mobilisation_request()
        return MobilisationProposedDepartment.objects.filter(
            request=req,
        ).select_related('real_site').order_by('-created_at')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposedDepartmentWriteSerializer
        return ProposedDepartmentSerializer


# ─── Proposed-user viewset ────────────────────────────────────────────────────

class ProposedDepartmentRoleViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = ProposedDepartmentRoleSerializer

    def get_queryset(self):
        req = self._get_mobilisation_request()
        return MobilisationProposedDepartmentRole.objects.filter(
            request=req,
        ).select_related(
            'proposed_department',
            'real_site',
            'job_role',
            'site_role_requirement__site',
            'site_role_requirement__job_role',
            'site_role_requirement__wage_category',
        ).order_by('real_site__name', 'sort_order', 'id')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposedDepartmentRoleWriteSerializer
        return ProposedDepartmentRoleSerializer


class _ScopedProposedSetupMixin(ActionCapabilityMixin, viewsets.GenericViewSet):
    """List/retrieve proposed setup rows across mobilisation requests in user scope."""

    permission_classes = [IsAuthenticated, HasCapability]
    action_required_capabilities = {
        'list': 'mobilisation.read',
        'retrieve': 'mobilisation.read',
    }

    def _accessible_request_ids(self):
        return filter_mobilisation_requests_for_user(
            MobilisationSetupRequest.objects.only('id'), self.request.user,
        ).values_list('id', flat=True)

    def _filter_by_request_param(self, qs):
        request_id = (
            self.request.query_params.get('setup_request')
            or self.request.query_params.get('request')
        )
        if request_id:
            qs = qs.filter(request_id=request_id)
        return qs


class ScopedProposedDepartmentViewSet(
    _ScopedProposedSetupMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    """
  GET /api/mobilisation/proposed-departments/
  Optional query: ?setup_request=<id> or ?request=<id>
    """

    def get_queryset(self):
        qs = MobilisationProposedDepartment.objects.filter(
            request_id__in=self._accessible_request_ids(),
        ).select_related('real_site', 'request').order_by('-created_at')
        return self._filter_by_request_param(qs)

    def get_serializer_class(self):
        return ProposedDepartmentSerializer


class ScopedProposedUserViewSet(
    _ScopedProposedSetupMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    """
  GET /api/mobilisation/proposed-users/
  Optional query: ?setup_request=<id> or ?request=<id>
    """

    def get_queryset(self):
        qs = MobilisationProposedUser.objects.filter(
            request_id__in=self._accessible_request_ids(),
        ).select_related('access_role', 'real_site', 'request', 'created_user').order_by('-created_at')
        return self._filter_by_request_param(qs)

    def get_serializer_class(self):
        return MobilisationProposedUserSerializer


class ProposedUserViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = MobilisationProposedUserSerializer

    action_required_capabilities = {
        **ProposedSetupViewSetMixin.action_required_capabilities,
        'resend_invite': 'mobilisation.update',
    }

    def get_queryset(self):
        req = self._get_mobilisation_request()
        return MobilisationProposedUser.objects.filter(request=req).select_related(
            'access_role', 'real_site', 'created_user',
        ).order_by('-created_at')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return MobilisationProposedUserWriteSerializer
        return MobilisationProposedUserSerializer

    @action(detail=True, methods=['post'], url_path='resend-invite')
    def resend_invite(self, request, request_pk=None, pk=None):
        """POST /{id}/resend-invite/ - re-send the invite email."""
        from .services import resend_mobilisation_proposed_user_invite
        from rest_framework.exceptions import ValidationError as DRFValidationError

        proposed_user = self.get_object()
        try:
            resend_mobilisation_proposed_user_invite(proposed_user, actor=request.user)
        except ValueError as exc:
            raise DRFValidationError({'detail': str(exc)})
        except Exception as exc:
            raise DRFValidationError({'detail': f'Invite email failed: {exc}'})
        proposed_user.refresh_from_db()
        return Response(self.get_serializer(proposed_user).data)

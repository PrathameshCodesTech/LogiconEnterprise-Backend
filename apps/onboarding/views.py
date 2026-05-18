"""
apps/onboarding/views.py

CRUD for ClientOnboardingRequest and nested proposed setup records.
"""

from rest_framework import viewsets, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_onboarding_requests_for_user
from apps.access.scope import actor_can_access_scope
from apps.access.viewsets import ActionCapabilityMixin, ReadAfterWriteMixin, ScopedModelViewSet

from .models import (
    ClientOnboardingRequest,
    ClientOnboardingProposedBudget,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingProposedUser,
)
from .serializers import (
    ClientOnboardingRequestSerializer,
    ClientOnboardingRequestWriteSerializer,
    ProposedBudgetSerializer,
    ProposedBudgetWriteSerializer,
    ProposedSiteSerializer,
    ProposedSiteWriteSerializer,
    ProposedDepartmentSerializer,
    ProposedDepartmentWriteSerializer,
    ProposedSRRSerializer,
    ProposedSRRWriteSerializer,
    ClientOnboardingProposedUserSerializer,
    ClientOnboardingProposedUserWriteSerializer,
)


# Statuses that allow editing proposed setup records.
# draft: not yet submitted; rejected: resubmission path.
_EDITABLE_STATUSES = ('draft', 'rejected')


# ─── Main request viewset ─────────────────────────────────────────────────────

class ClientOnboardingRequestViewSet(ScopedModelViewSet):
    """
    CRUD for client onboarding requests.

    Capability map:
      list/retrieve  → client_onboarding.read
      create         → client_onboarding.create
      update/partial → client_onboarding.update
      destroy        → client_onboarding.delete
      readiness      → client_onboarding.read
    """
    queryset = ClientOnboardingRequest.objects.select_related(
        'org', 'client', 'requested_by', 'client__scope_node',
        'created_client', 'finalized_by', 'budget_plan',
    ).prefetch_related(
        'workflow_instances__steps__assigned_user',
        'proposed_sites',
        'proposed_departments',
        'proposed_role_requirements',
        'proposed_budgets',
        'proposed_users__access_role',
        'proposed_users__proposed_site',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_onboarding_requests_for_user

    filterset_fields = ['org', 'client', 'status', 'onboarding_type', 'requested_by']
    search_fields = ['summary', 'client__name', 'proposed_client_name']

    action_required_capabilities = {
        'list':           'client_onboarding.read',
        'retrieve':       'client_onboarding.read',
        'create':         'client_onboarding.create',
        'update':         'client_onboarding.update',
        'partial_update': 'client_onboarding.update',
        'destroy':        'client_onboarding.delete',
        'readiness':      'client_onboarding.read',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ClientOnboardingRequestWriteSerializer
        return ClientOnboardingRequestSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        # Provide actor_org to write serializer for proposed_client_code uniqueness check
        user = self.request.user
        if hasattr(user, 'org'):
            ctx['actor_org'] = user.org
        return ctx

    @action(detail=True, methods=['get'], url_path='readiness')
    def readiness(self, request, pk=None):
        """GET /api/onboarding/client-requests/{id}/readiness/"""
        from .services import check_onboarding_readiness
        obj = self.get_object()
        ok, errors, warnings = check_onboarding_readiness(obj)
        return Response({'ok': ok, 'errors': errors, 'warnings': warnings})

    def _check_client_scope(self, client):
        if client is None:
            return  # new_client type — no real client to scope-check
        user = self.request.user
        if user.is_superuser:
            return
        if not client.scope_node:
            raise PermissionDenied("Client has no scope node configured.")
        if not actor_can_access_scope(user, client.scope_node):
            raise PermissionDenied("You do not have access to this client.")

    def _get_org_for_create(self, client):
        """Derive org: from client if given, otherwise from the actor's own org."""
        if client is not None:
            return client.org
        user = self.request.user
        if hasattr(user, 'org') and user.org is not None:
            return user.org
        raise ValidationError(
            "Cannot determine organization for new_client onboarding. "
            "Ensure your account is assigned to an organization."
        )

    def perform_create(self, serializer):
        client = serializer.validated_data.get('client')
        self._check_client_scope(client)
        org = self._get_org_for_create(client)
        serializer.save(org=org, requested_by=self.request.user)

    def perform_update(self, serializer):
        client = serializer.validated_data.get('client', serializer.instance.client)
        self._check_client_scope(client)
        if client is not None:
            serializer.save(org=client.org)
        else:
            serializer.save()


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
    Base for the three nested proposed-setup viewsets.
    Requires `request_pk` URL kwarg pointing to the parent ClientOnboardingRequest.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    action_required_capabilities = {
        'list':           'client_onboarding.read',
        'retrieve':       'client_onboarding.read',
        'create':         'client_onboarding.create',
        'update':         'client_onboarding.update',
        'partial_update': 'client_onboarding.update',
        'destroy':        'client_onboarding.delete',
    }

    def _get_onboarding_request(self):
        from django.shortcuts import get_object_or_404
        user = self.request.user
        # Build a queryset the user can see (scope-filtered)
        qs = filter_onboarding_requests_for_user(
            ClientOnboardingRequest.objects.all(), user,
        )
        return get_object_or_404(qs, pk=self.kwargs['request_pk'])

    def _check_editable(self, onboarding_request):
        if onboarding_request.status not in _EDITABLE_STATUSES:
            raise PermissionDenied(
                f"Proposed setup records cannot be modified when the onboarding request "
                f"status is '{onboarding_request.status}'. "
                f"Edits are only allowed in: {', '.join(_EDITABLE_STATUSES)}."
            )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if 'request_pk' in self.kwargs:
            try:
                ctx['onboarding_request'] = self._get_onboarding_request()
            except Exception:
                pass
        return ctx

    def perform_create(self, serializer):
        req = self._get_onboarding_request()
        self._check_editable(req)
        serializer.save(request=req)

    def perform_update(self, serializer):
        req = self._get_onboarding_request()
        self._check_editable(req)
        serializer.save()

    def perform_destroy(self, instance):
        req = self._get_onboarding_request()
        self._check_editable(req)
        instance.delete()


# ─── Proposed-site viewset ────────────────────────────────────────────────────

class ProposedSiteViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = ProposedSiteSerializer

    def get_queryset(self):
        req = self._get_onboarding_request()
        return ClientOnboardingProposedSite.objects.filter(request=req).order_by('-created_at')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposedSiteWriteSerializer
        return ProposedSiteSerializer


# ─── Proposed-department viewset ──────────────────────────────────────────────

class ProposedDepartmentViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = ProposedDepartmentSerializer

    def get_queryset(self):
        req = self._get_onboarding_request()
        return ClientOnboardingProposedDepartment.objects.filter(request=req).order_by('-created_at')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposedDepartmentWriteSerializer
        return ProposedDepartmentSerializer


# ─── Proposed-SRR viewset ─────────────────────────────────────────────────────

class ProposedSiteRoleRequirementViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = ProposedSRRSerializer

    def get_queryset(self):
        req = self._get_onboarding_request()
        return ClientOnboardingProposedSiteRoleRequirement.objects.filter(
            request=req,
        ).order_by('-created_at')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposedSRRWriteSerializer
        return ProposedSRRSerializer


# ─── Proposed-budget viewset ──────────────────────────────────────────────────

class ProposedBudgetViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = ProposedBudgetSerializer

    def get_queryset(self):
        req = self._get_onboarding_request()
        return ClientOnboardingProposedBudget.objects.filter(request=req).order_by('-created_at')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposedBudgetWriteSerializer
        return ProposedBudgetSerializer


# ─── Proposed-user viewset ────────────────────────────────────────────────────

class ProposedUserViewSet(ProposedSetupViewSetMixin):
    read_serializer_class = ClientOnboardingProposedUserSerializer

    action_required_capabilities = {
        **ProposedSetupViewSetMixin.action_required_capabilities,
        'resend_invite': 'client_onboarding.update',
    }

    def get_queryset(self):
        req = self._get_onboarding_request()
        return ClientOnboardingProposedUser.objects.filter(request=req).select_related(
            'access_role', 'proposed_site', 'created_user',
        ).order_by('-created_at')

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ClientOnboardingProposedUserWriteSerializer
        return ClientOnboardingProposedUserSerializer

    @action(detail=True, methods=['post'], url_path='resend-invite')
    def resend_invite(self, request, request_pk=None, pk=None):
        """POST /{id}/resend-invite/ - re-send the invite email."""
        from .services import resend_onboarding_proposed_user_invite
        from rest_framework.exceptions import ValidationError as DRFValidationError

        proposed_user = self.get_object()
        try:
            resend_onboarding_proposed_user_invite(proposed_user, actor=request.user)
        except ValueError as exc:
            raise DRFValidationError({'detail': str(exc)})
        except Exception as exc:
            raise DRFValidationError({'detail': f'Invite email failed: {exc}'})
        proposed_user.refresh_from_db()
        return Response(self.get_serializer(proposed_user).data)

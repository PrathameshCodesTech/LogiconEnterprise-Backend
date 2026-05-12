"""
apps/onboarding/views.py

CRUD for ClientOnboardingRequest.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_onboarding_requests_for_user
from apps.access.scope import actor_can_access_scope
from apps.access.viewsets import ScopedModelViewSet

from .models import ClientOnboardingRequest
from .serializers import (
    ClientOnboardingRequestSerializer,
    ClientOnboardingRequestWriteSerializer,
)


class ClientOnboardingRequestViewSet(ScopedModelViewSet):
    """
    CRUD for client onboarding requests.

    Capability map:
      list/retrieve  → client_onboarding.read
      create         → client_onboarding.create
      update/partial → client_onboarding.update
      destroy        → client_onboarding.delete
    """
    queryset = ClientOnboardingRequest.objects.select_related(
        'org', 'client', 'requested_by',
        'client__scope_node',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_onboarding_requests_for_user

    filterset_fields = ['org', 'client', 'status', 'onboarding_type', 'requested_by']
    search_fields = ['summary', 'client__name']

    action_required_capabilities = {
        'list':           'client_onboarding.read',
        'retrieve':       'client_onboarding.read',
        'create':         'client_onboarding.create',
        'update':         'client_onboarding.update',
        'partial_update': 'client_onboarding.update',
        'destroy':        'client_onboarding.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ClientOnboardingRequestWriteSerializer
        return ClientOnboardingRequestSerializer

    def _check_client_scope(self, client):
        user = self.request.user
        if user.is_superuser:
            return
        if not client.scope_node:
            raise PermissionDenied("Client has no scope node configured.")
        if not actor_can_access_scope(user, client.scope_node):
            raise PermissionDenied("You do not have access to this client.")

    def perform_create(self, serializer):
        client = serializer.validated_data['client']
        self._check_client_scope(client)
        serializer.save(org=client.org, requested_by=self.request.user)

    def perform_update(self, serializer):
        client = serializer.validated_data.get('client', serializer.instance.client)
        self._check_client_scope(client)
        serializer.save(org=client.org)

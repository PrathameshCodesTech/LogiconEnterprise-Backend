"""
apps/access/views.py

Access control ViewSets: roles, permissions, role assignments, scope assignments.
"""

from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from apps.access.permissions import HasCapability
from apps.access.querysets import _scope_q
from apps.access.scope import actor_can_access_scope, get_accessible_scope_paths
from apps.access.viewsets import ActionCapabilityMixin
from apps.audit.services import log_audit

from .models import AccessRole, Permission, UserRoleAssignment, UserScopeAssignment
from .serializers import (
    AccessRoleSerializer,
    PermissionSerializer,
    UserRoleAssignmentSerializer,
    UserScopeAssignmentSerializer,
)


class AccessRoleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AccessRole.objects.filter(is_active=True)
    serializer_class = AccessRoleSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['org', 'is_active']
    search_fields = ['name', 'code']


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['action', 'resource']
    search_fields = ['resource', 'action']


class UserRoleAssignmentViewSet(ActionCapabilityMixin, viewsets.ModelViewSet):
    """
    CRUD for UserRoleAssignment.

    Capability map:
      list/retrieve → role.read
      create/update/partial_update/destroy → role.update
    """
    queryset = UserRoleAssignment.objects.select_related(
        'user', 'role', 'scope_node',
    ).order_by('user__username', 'role__code')
    serializer_class = UserRoleAssignmentSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['user', 'role', 'scope_node']

    action_required_capabilities = {
        'list':           'role.read',
        'retrieve':       'role.read',
        'create':         'role.update',
        'update':         'role.update',
        'partial_update': 'role.update',
        'destroy':        'role.update',
    }

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user
        if user.is_superuser:
            return qs
        paths = get_accessible_scope_paths(user)
        if not paths:
            return qs.none()
        return qs.filter(_scope_q('scope_node__path', paths)).distinct()

    def perform_create(self, serializer):
        actor = self.request.user
        scope_node = serializer.validated_data['scope_node']
        if not actor.is_superuser and not actor_can_access_scope(actor, scope_node):
            raise PermissionDenied(
                "You cannot assign roles outside your accessible scope."
            )
        assignment = serializer.save()
        log_audit(actor, 'role_assignment.create', assignment, request=self.request)

    def perform_update(self, serializer):
        assignment = serializer.save()
        log_audit(self.request.user, 'role_assignment.update', assignment, request=self.request)

    def perform_destroy(self, instance):
        log_audit(self.request.user, 'role_assignment.delete', instance, request=self.request)
        instance.delete()


class UserScopeAssignmentViewSet(ActionCapabilityMixin, viewsets.ModelViewSet):
    """
    CRUD for UserScopeAssignment (informational — does not grant access by itself).
    """
    queryset = UserScopeAssignment.objects.select_related(
        'user', 'scope_node',
    ).order_by('user__username')
    serializer_class = UserScopeAssignmentSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['user', 'scope_node', 'assignment_type']

    action_required_capabilities = {
        'list':           'role.read',
        'retrieve':       'role.read',
        'create':         'role.update',
        'update':         'role.update',
        'partial_update': 'role.update',
        'destroy':        'role.update',
    }

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user
        if user.is_superuser:
            return qs
        paths = get_accessible_scope_paths(user)
        if not paths:
            return qs.none()
        return qs.filter(_scope_q('scope_node__path', paths)).distinct()

    def perform_create(self, serializer):
        actor = self.request.user
        scope_node = serializer.validated_data['scope_node']
        if not actor.is_superuser and not actor_can_access_scope(actor, scope_node):
            raise PermissionDenied(
                "You cannot create scope assignments outside your accessible scope."
            )
        assignment = serializer.save()
        log_audit(actor, 'scope_assignment.create', assignment, request=self.request)

    def perform_destroy(self, instance):
        log_audit(self.request.user, 'scope_assignment.delete', instance, request=self.request)
        instance.delete()

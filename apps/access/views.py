"""
apps/access/views.py

Access control ViewSets: roles, permissions, role permissions, role assignments, scope assignments.
"""

from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from apps.access.permissions import HasCapability
from apps.access.querysets import _scope_q
from apps.access.scope import actor_can_access_scope, get_accessible_scope_paths
from apps.access.viewsets import ActionCapabilityMixin, ReadAfterWriteMixin
from apps.audit.services import log_audit

from .models import AccessRole, AccessRolePermission, Permission, UserRoleAssignment, UserScopeAssignment
from .serializers import (
    AccessRoleSerializer,
    AccessRoleWriteSerializer,
    AccessRolePermissionSerializer,
    AccessRolePermissionWriteSerializer,
    PermissionSerializer,
    UserRoleAssignmentSerializer,
    UserScopeAssignmentSerializer,
)


class AccessRoleViewSet(ReadAfterWriteMixin, ActionCapabilityMixin, viewsets.ModelViewSet):
    """
    CRUD for AccessRole.

    Capability map:
      list/retrieve → role.read
      create        → role.create
      update        → role.update
      destroy       → role.delete  (soft-deactivate only)
    """
    read_serializer_class = AccessRoleSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['org', 'is_active']
    search_fields = ['name', 'code']

    action_required_capabilities = {
        'list':           'role.read',
        'retrieve':       'role.read',
        'create':         'role.create',
        'update':         'role.update',
        'partial_update': 'role.update',
        'destroy':        'role.delete',
    }

    def get_queryset(self):
        qs = AccessRole.objects.all().order_by('name')
        if self.request.user.is_superuser:
            return qs
        return qs.filter(org=self.request.user.org)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return AccessRoleWriteSerializer
        return AccessRoleSerializer

    def perform_create(self, serializer):
        actor = self.request.user
        org = serializer.validated_data.get('org') if actor.is_superuser else actor.org
        if not org:
            raise serializers.ValidationError({'org': 'org is required.'})
        code = serializer.validated_data.get('code', '')
        if AccessRole.objects.filter(org=org, code=code).exists():
            raise serializers.ValidationError(
                {'code': 'A role with this code already exists in this org.'}
            )
        role = serializer.save(org=org)
        log_audit(actor, 'role.create', role, request=self.request)

    def perform_update(self, serializer):
        instance = serializer.instance
        code = serializer.validated_data.get('code', instance.code)
        qs = AccessRole.objects.filter(org=instance.org, code=code).exclude(pk=instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {'code': 'A role with this code already exists in this org.'}
            )
        role = serializer.save()
        log_audit(self.request.user, 'role.update', role, request=self.request)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        log_audit(self.request.user, 'role.deactivate', instance, request=self.request)


class PermissionViewSet(ActionCapabilityMixin, viewsets.ReadOnlyModelViewSet):
    """
    Read-only catalog of all Permission entries.
    Requires role.read capability.
    """
    queryset = Permission.objects.all().order_by('resource', 'action')
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['action', 'resource']
    search_fields = ['code', 'resource', 'action']

    action_required_capabilities = {
        'list':     'role.read',
        'retrieve': 'role.read',
    }


class AccessRolePermissionViewSet(ReadAfterWriteMixin, ActionCapabilityMixin, viewsets.ModelViewSet):
    """
    Manage AccessRolePermission (role ↔ permission mappings).

    Capability map:
      list/retrieve → role.read
      create/destroy → role.update

    PATCH/PUT are disabled; only create/delete make sense for a junction table.
    """
    read_serializer_class = AccessRolePermissionSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['role', 'permission', 'permission__resource']
    search_fields = ['role__code', 'role__name', 'permission__code', 'permission__resource', 'permission__action']
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    action_required_capabilities = {
        'list':     'role.read',
        'retrieve': 'role.read',
        'create':   'role.update',
        'destroy':  'role.update',
    }

    def get_queryset(self):
        qs = AccessRolePermission.objects.select_related(
            'role', 'permission',
        ).order_by('role__code', 'permission__code')
        if self.request.user.is_superuser:
            return qs
        return qs.filter(role__org=self.request.user.org)

    def get_serializer_class(self):
        if self.action == 'create':
            return AccessRolePermissionWriteSerializer
        return AccessRolePermissionSerializer

    def perform_create(self, serializer):
        actor = self.request.user
        role = serializer.validated_data['role']
        if not actor.is_superuser and role.org != actor.org:
            raise PermissionDenied('You cannot attach permissions to a role from another org.')
        rp = serializer.save()
        log_audit(actor, 'role_permission.attach', rp, request=self.request)

    def perform_destroy(self, instance):
        actor = self.request.user
        if not actor.is_superuser and instance.role.org != actor.org:
            raise PermissionDenied('You cannot detach permissions from a role in another org.')
        log_audit(actor, 'role_permission.detach', instance, request=self.request)
        instance.delete()


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

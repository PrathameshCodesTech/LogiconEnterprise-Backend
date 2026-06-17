from rest_framework import status, viewsets, generics
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_departments_for_user
from apps.access.viewsets import ActionCapabilityMixin

from .models import Organization, ScopeNode, Department
from .serializers import OrganizationSerializer, ScopeNodeSerializer, DepartmentSerializer, DepartmentWriteSerializer


class MeView(generics.RetrieveAPIView):
    """
    GET /api/core/me/
    Returns current user info with scope assignments, role assignments, and capabilities.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user

        # Import here to avoid circular imports
        from apps.access.models import UserScopeAssignment, UserRoleAssignment
        from apps.access.serializers import UserScopeAssignmentSerializer, UserRoleAssignmentSerializer
        from apps.access.capabilities import get_user_access_profile, get_user_capabilities

        scope_assignments = UserScopeAssignment.objects.filter(user=user).select_related('scope_node')
        role_assignments = UserRoleAssignment.objects.filter(user=user).select_related('role', 'scope_node')

        access_profile = get_user_access_profile(user)

        # Department info for internal MRF creation
        department = getattr(user, 'department', None)
        department_id = department.id if department else None
        department_name = department.name if department else None
        department_code = department.code if department else None

        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'user_type': getattr(user, 'user_type', ''),
            'org': user.org_id,
            'department': department_id,
            'department_name': department_name,
            'department_code': department_code,
            'scope_assignments': UserScopeAssignmentSerializer(scope_assignments, many=True).data,
            'role_assignments': UserRoleAssignmentSerializer(role_assignments, many=True).data,
            'capabilities': get_user_capabilities(user),
            **access_profile,
        })


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/core/organizations/        — list
    GET /api/core/organizations/{id}/   — retrieve
    """
    queryset = Organization.objects.filter(is_active=True)
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['is_active', 'code']
    search_fields = ['name', 'code']


class ScopeNodeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/core/scope-nodes/        — list
    GET /api/core/scope-nodes/{id}/   — retrieve
    """
    queryset = ScopeNode.objects.filter(is_active=True).select_related('org', 'parent')
    serializer_class = ScopeNodeSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['node_type', 'is_active', 'org', 'parent']
    search_fields = ['name', 'code', 'path']


class DepartmentViewSet(ActionCapabilityMixin, ModelViewSet):
    """
    Full CRUD for Department master data.
    Scoped to the actor's org; superusers see all.
    Soft-delete: DELETE sets is_active=False.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['org', 'client', 'site', 'is_active']
    search_fields = ['name', 'code', 'description', 'client__name', 'site__name']

    action_required_capabilities = {
        'list':           'department.read',
        'retrieve':       'department.read',
        'create':         'department.create',
        'update':         'department.update',
        'partial_update': 'department.update',
        'destroy':        'department.delete',
    }

    def get_queryset(self):
        qs = Department.objects.select_related('org', 'client', 'site').order_by('org', 'name')
        user = self.request.user
        if user.is_superuser:
            return qs
        return filter_departments_for_user(qs, user)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return DepartmentWriteSerializer
        return DepartmentSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        actor = request.user
        org = serializer.validated_data.get('org') if actor.is_superuser else actor.org
        if not org:
            raise ValidationError({'detail': 'Your account has no organization set.'})

        instance = serializer.save(org=org)
        out = DepartmentSerializer(instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = DepartmentSerializer(instance, context={'request': request})
        return Response(out.data)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])

from rest_framework import viewsets, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Organization, ScopeNode
from .serializers import OrganizationSerializer, ScopeNodeSerializer


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
        from apps.access.capabilities import get_user_capabilities

        scope_assignments = UserScopeAssignment.objects.filter(user=user).select_related('scope_node')
        role_assignments = UserRoleAssignment.objects.filter(user=user).select_related('role', 'scope_node')

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
            'scope_assignments': UserScopeAssignmentSerializer(scope_assignments, many=True).data,
            'role_assignments': UserRoleAssignmentSerializer(role_assignments, many=True).data,
            'capabilities': get_user_capabilities(user),
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

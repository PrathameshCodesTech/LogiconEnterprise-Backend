from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import JobRole
from .serializers import JobRoleSerializer


class JobRoleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = JobRole.objects.filter(is_active=True)
    serializer_class = JobRoleSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['org', 'skill_category', 'is_active']
    search_fields = ['name', 'code']

"""
apps/jobs/views.py

Writable ViewSet for JobRole master data.
Create/update/delete are capability-gated; org is always injected from the actor.
"""

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.access.permissions import HasCapability
from apps.access.viewsets import ActionCapabilityMixin

from .models import JobRole
from .serializers import JobRoleSerializer, JobRoleWriteSerializer


class JobRoleViewSet(ActionCapabilityMixin, ModelViewSet):
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['org', 'skill_category', 'is_active']
    search_fields = ['name', 'code']

    action_required_capabilities = {
        'list':           'job_role.read',
        'retrieve':       'job_role.read',
        'create':         'job_role.create',
        'update':         'job_role.update',
        'partial_update': 'job_role.update',
        'destroy':        'job_role.delete',
    }

    def get_queryset(self):
        qs = JobRole.objects.select_related('org').order_by('name')
        user = self.request.user
        if user.is_superuser:
            return qs
        org_id = getattr(user, 'org_id', None)
        if org_id:
            return qs.filter(org_id=org_id)
        return qs.none()

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return JobRoleWriteSerializer
        return JobRoleSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        actor = request.user
        org = serializer.validated_data.get('org') if actor.is_superuser else actor.org
        if not org:
            raise ValidationError({'detail': 'Your account has no organization set.'})

        instance = serializer.save(org=org)
        out = JobRoleSerializer(instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = JobRoleSerializer(instance, context={'request': request})
        return Response(out.data)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])

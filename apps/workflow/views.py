from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import WorkflowTemplate, WorkflowInstance
from .serializers import WorkflowTemplateSerializer, WorkflowInstanceSerializer


class WorkflowTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WorkflowTemplate.objects.filter(is_active=True)
    serializer_class = WorkflowTemplateSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['org', 'module', 'is_active']
    search_fields = ['name', 'code']


class WorkflowInstanceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WorkflowInstance.objects.all()
    serializer_class = WorkflowInstanceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['template', 'status', 'object_type', 'started_by']

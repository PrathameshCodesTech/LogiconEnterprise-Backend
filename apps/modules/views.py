from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import ModuleActivation
from .serializers import ModuleActivationSerializer


class ModuleActivationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/modules/activations/ — list all module activations
    """
    queryset = ModuleActivation.objects.select_related('scope_node').all()
    serializer_class = ModuleActivationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['module', 'is_active', 'scope_node']
    search_fields = ['module', 'scope_node__name']

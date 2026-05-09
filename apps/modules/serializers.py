from rest_framework import serializers
from .models import ModuleActivation


class ModuleActivationSerializer(serializers.ModelSerializer):
    scope_node_path = serializers.CharField(source='scope_node.path', read_only=True)

    class Meta:
        model = ModuleActivation
        fields = [
            'id', 'module', 'scope_node', 'scope_node_path',
            'is_active', 'override_parent', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

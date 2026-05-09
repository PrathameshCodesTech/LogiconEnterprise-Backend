from rest_framework import serializers
from .models import WorkflowTemplate, WorkflowStepTemplate, WorkflowInstance, WorkflowAction


class WorkflowStepTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStepTemplate
        fields = ['id', 'template', 'step_order', 'name', 'approver_role', 'approver_scope_node', 'is_client_step']


class WorkflowTemplateSerializer(serializers.ModelSerializer):
    steps = WorkflowStepTemplateSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowTemplate
        fields = ['id', 'org', 'name', 'code', 'scope_node', 'module', 'is_active', 'steps', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class WorkflowActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowAction
        fields = ['id', 'instance', 'step_name', 'action_by', 'action', 'note', 'created_at']
        read_only_fields = ['created_at']


class WorkflowInstanceSerializer(serializers.ModelSerializer):
    actions = WorkflowActionSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowInstance
        fields = ['id', 'template', 'object_type', 'object_id', 'status', 'started_by', 'actions', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

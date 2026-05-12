from rest_framework import serializers

from .models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowInstance, WorkflowStepInstance, WorkflowAction,
)


# ─── Read serializers ─────────────────────────────────────────────────────────

class WorkflowStepTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStepTemplate
        fields = [
            'id', 'template', 'order', 'code', 'name',
            'assignment_mode', 'actor_type',
            'on_approve_next', 'on_reject_target', 'on_request_changes_target',
            'requires_comment_on_reject', 'requires_comment_on_request_changes',
            'sla_hours',
        ]
        read_only_fields = fields


class WorkflowTemplateSerializer(serializers.ModelSerializer):
    steps = WorkflowStepTemplateSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowTemplate
        fields = [
            'id', 'org', 'name', 'code', 'trigger_type', 'version',
            'description', 'is_active', 'steps', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class WorkflowStepInstanceSerializer(serializers.ModelSerializer):
    assigned_user_username = serializers.CharField(
        source='assigned_user.username', read_only=True, default=None,
    )
    acted_by_username = serializers.CharField(
        source='acted_by.username', read_only=True, default=None,
    )

    class Meta:
        model = WorkflowStepInstance
        fields = [
            'id', 'workflow', 'step_template',
            'step_order', 'step_code', 'step_name',
            'assignment_mode', 'actor_type',
            'on_approve_next', 'on_reject_target', 'on_request_changes_target',
            'requires_comment_on_reject', 'requires_comment_on_request_changes',
            'sla_hours',
            'assigned_user', 'assigned_user_username', 'assigned_at',
            'assigned_department', 'assigned_department_name_snapshot', 'assigned_department_code_snapshot',
            'status', 'acted_by', 'acted_by_username', 'acted_at',
            'action_taken', 'comment',
            'activated_at', 'due_at',
        ]
        read_only_fields = fields


class WorkflowActionSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = WorkflowAction
        fields = [
            'id', 'workflow', 'step_instance',
            'actor', 'actor_username', 'action', 'comment',
            'reassign_from', 'reassign_to', 'created_at',
        ]
        read_only_fields = fields


class WorkflowInstanceSerializer(serializers.ModelSerializer):
    steps = WorkflowStepInstanceSerializer(many=True, read_only=True)
    audit_trail = WorkflowActionSerializer(many=True, read_only=True)
    current_step = serializers.SerializerMethodField()
    initiated_by_username = serializers.CharField(
        source='initiated_by.username', read_only=True,
    )

    class Meta:
        model = WorkflowInstance
        fields = [
            'id', 'org', 'mrf', 'client_onboarding_request',
            'template', 'template_version',
            'status', 'initiated_by', 'initiated_by_username',
            'started_at', 'completed_at',
            'current_step', 'steps', 'audit_trail',
        ]
        read_only_fields = fields

    # 'created_at' is the canonical start timestamp from TimeStampedModel
    started_at = serializers.DateTimeField(source='created_at', read_only=True)

    def get_current_step(self, obj):
        step = obj.steps.filter(status='active').first()
        if step is None:
            return None
        return WorkflowStepInstanceSerializer(step).data


# ─── Write serializers ────────────────────────────────────────────────────────

class ActOnStepSerializer(serializers.Serializer):
    ACTION_CHOICES = [
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('request_changes', 'Request Changes'),
    ]
    action = serializers.ChoiceField(choices=ACTION_CHOICES)
    comment = serializers.CharField(required=False, allow_blank=True, default='')


class ReassignStepSerializer(serializers.Serializer):
    new_user = serializers.IntegerField(help_text='Primary key of the new assignee.')
    comment = serializers.CharField(required=False, allow_blank=True, default='')

"""
apps/workflow/models.py

WorkflowTemplate, WorkflowStepTemplate, WorkflowInstance, WorkflowAction.

Workflow is fully configurable per org/module — not hardcoded.
Steps support branching (on_approve / on_reject targets), SLA, and
client-step flagging for MRF client review gates.
"""

from django.conf import settings
from django.db import models
from apps.core.models import Organization, ScopeNode, TimeStampedModel


class WorkflowTemplate(TimeStampedModel):
    """A reusable workflow definition for an org/module."""
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='workflow_templates')
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    scope_node = models.ForeignKey(
        ScopeNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_templates',
    )
    module = models.CharField(max_length=32)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Workflow Template'
        verbose_name_plural = 'Workflow Templates'
        unique_together = [['org', 'code']]
        ordering = ['org', 'name']

    def __str__(self):
        return f"{self.name} ({self.module})"


class WorkflowStepTemplate(models.Model):
    """
    A single configurable step in a workflow template.

    step_key is unique within a template and is used for branching references.
    on_approve_next_step / on_reject_target_step allow non-linear flows.
    """
    template = models.ForeignKey(WorkflowTemplate, on_delete=models.CASCADE, related_name='steps')
    step_key = models.CharField(max_length=64)
    step_order = models.PositiveIntegerField()
    name = models.CharField(max_length=128)
    approver_role = models.ForeignKey(
        'access.AccessRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_steps',
    )
    approver_scope_node = models.ForeignKey(
        ScopeNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_steps',
    )
    is_client_step = models.BooleanField(default=False)
    on_approve_next_step = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approve_predecessors',
    )
    on_reject_target_step = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reject_predecessors',
    )
    can_request_changes = models.BooleanField(default=False)
    sla_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='SLA in hours for this step. Null means no SLA.',
    )

    class Meta:
        verbose_name = 'Workflow Step Template'
        verbose_name_plural = 'Workflow Step Templates'
        unique_together = [['template', 'step_key']]
        ordering = ['template', 'step_order']

    def __str__(self):
        return f"{self.template} — Step {self.step_order}: {self.name}"


class WorkflowInstance(TimeStampedModel):
    """A running instance of a workflow for a specific object (e.g. an MRF)."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    template = models.ForeignKey(WorkflowTemplate, on_delete=models.PROTECT, related_name='instances')
    object_type = models.CharField(max_length=64)
    object_id = models.PositiveBigIntegerField()
    current_step = models.ForeignKey(
        WorkflowStepTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='active_instances',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='started_workflows',
    )

    class Meta:
        verbose_name = 'Workflow Instance'
        verbose_name_plural = 'Workflow Instances'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['object_type', 'object_id']),
        ]

    def __str__(self):
        return f"Workflow #{self.pk} ({self.object_type} #{self.object_id}) — {self.status}"


class WorkflowAction(models.Model):
    """An action taken on a workflow instance at a specific step."""

    ACTION_CHOICES = [
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('comment', 'Comment'),
        ('reassign', 'Reassign'),
        ('request_changes', 'Request Changes'),
    ]

    instance = models.ForeignKey(WorkflowInstance, on_delete=models.CASCADE, related_name='actions')
    step = models.ForeignKey(
        WorkflowStepTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actions',
    )
    step_snapshot_name = models.CharField(
        max_length=128,
        blank=True,
        help_text='Snapshot of step name at time of action for audit trail.',
    )
    action_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_actions',
    )
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Workflow Action'
        verbose_name_plural = 'Workflow Actions'
        ordering = ['instance', 'created_at']

    def __str__(self):
        return f"{self.instance} — {self.action} by {self.action_by}"

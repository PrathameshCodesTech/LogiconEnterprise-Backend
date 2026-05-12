"""
apps/workflow/models.py

MRF workflow engine — Phase 1 (named_user assignment only).

Models:
  WorkflowTemplate          — reusable template definition
  WorkflowStepTemplate      — ordered steps within a template
  WorkflowTemplateMapping   — which template applies to which org/client/site
  StepAssignmentConfig      — who handles each step at org/client/site level
  WorkflowInstance          — a running workflow for one MRF
  WorkflowStepInstance      — runtime snapshot of one step (all fields frozen at start)
  WorkflowAction            — append-only audit trail
"""

from django.conf import settings
from django.db import models

from apps.core.models import Organization, TimeStampedModel

TRIGGER_TYPE_CHOICES = [
    ('mrf', 'MRF'),
    ('client_onboarding', 'Client Onboarding'),
]

ASSIGNMENT_MODE_CHOICES = [
    ('named_user', 'Named User'),
    ('queue', 'Queue'),
    ('claim', 'Claim'),
]

ACTOR_TYPE_CHOICES = [
    ('internal', 'Internal'),
    ('client', 'Client'),
    ('field', 'Field'),
]


# ─── Template definition ──────────────────────────────────────────────────────

class WorkflowTemplate(TimeStampedModel):
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE,
        null=True, blank=True, related_name='workflow_templates',
        help_text='Null = system-wide template usable by all orgs.',
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    trigger_type = models.CharField(max_length=32, choices=TRIGGER_TYPE_CHOICES, default='mrf')
    version = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Workflow Template'
        verbose_name_plural = 'Workflow Templates'
        ordering = ['org', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'code'],
                condition=models.Q(org__isnull=False),
                name='unique_wt_org_code',
            ),
            models.UniqueConstraint(
                fields=['code'],
                condition=models.Q(org__isnull=True),
                name='unique_wt_global_code',
            ),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.trigger_type})"


class WorkflowStepTemplate(models.Model):
    template = models.ForeignKey(
        WorkflowTemplate, on_delete=models.CASCADE, related_name='steps',
    )
    order = models.PositiveIntegerField()
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    assignment_mode = models.CharField(
        max_length=16, choices=ASSIGNMENT_MODE_CHOICES, default='named_user',
    )
    actor_type = models.CharField(
        max_length=16, choices=ACTOR_TYPE_CHOICES, default='internal',
    )
    # Transition targets are step codes (not FKs) to avoid circular dependency.
    # Empty string = default sequential / complete workflow.
    # 'END' = explicitly mark as final step (approve → complete).
    on_approve_next = models.CharField(
        max_length=64, blank=True,
        help_text='Step code to activate on approve. Empty = next sequential. "END" = complete workflow.',
    )
    on_reject_target = models.CharField(
        max_length=64, blank=True,
        help_text='Step code to reactivate on reject. Empty = reject workflow.',
    )
    on_request_changes_target = models.CharField(
        max_length=64, blank=True,
        help_text='Step code to reactivate on request_changes. Empty = reject workflow.',
    )
    requires_comment_on_reject = models.BooleanField(default=True)
    requires_comment_on_request_changes = models.BooleanField(default=True)
    sla_hours = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'Workflow Step Template'
        verbose_name_plural = 'Workflow Step Templates'
        ordering = ['template', 'order']
        unique_together = [
            ['template', 'order'],
            ['template', 'code'],
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.template_id:
            return
        sibling_codes = set(
            WorkflowStepTemplate.objects
            .filter(template_id=self.template_id)
            .exclude(pk=self.pk)
            .values_list('code', flat=True)
        )
        errors = {}
        if self.on_approve_next and self.on_approve_next != 'END':
            if self.on_approve_next not in sibling_codes:
                errors['on_approve_next'] = (
                    f'Step code "{self.on_approve_next}" does not exist in this template. '
                    f'Leave blank for next sequential, use "END" to complete workflow, '
                    f'or choose from: {sorted(sibling_codes) or "(none yet)"}.'
                )
        if self.on_reject_target and self.on_reject_target not in sibling_codes:
            errors['on_reject_target'] = (
                f'Step code "{self.on_reject_target}" does not exist in this template.'
            )
        if self.on_request_changes_target and self.on_request_changes_target not in sibling_codes:
            errors['on_request_changes_target'] = (
                f'Step code "{self.on_request_changes_target}" does not exist in this template.'
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.template.code} — step {self.order}: {self.name}"


# ─── Mapping: which template applies where ────────────────────────────────────

class WorkflowTemplateMapping(TimeStampedModel):
    """
    Determines which WorkflowTemplate to use for a given org/client/site.
    Resolution order: site → client → org default.
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='workflow_template_mappings',
    )
    trigger_type = models.CharField(max_length=32, choices=TRIGGER_TYPE_CHOICES, default='mrf')
    template = models.ForeignKey(
        WorkflowTemplate, on_delete=models.PROTECT, related_name='mappings',
    )
    client = models.ForeignKey(
        'sites.Client', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_template_mappings',
    )
    site = models.ForeignKey(
        'sites.SiteProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_template_mappings',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Workflow Template Mapping'
        verbose_name_plural = 'Workflow Template Mappings'
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'trigger_type', 'site'],
                condition=models.Q(site__isnull=False, is_active=True),
                name='unique_wtm_site',
            ),
            models.UniqueConstraint(
                fields=['org', 'trigger_type', 'client'],
                condition=models.Q(client__isnull=False, site__isnull=True, is_active=True),
                name='unique_wtm_client',
            ),
            models.UniqueConstraint(
                fields=['org', 'trigger_type'],
                condition=models.Q(client__isnull=True, site__isnull=True, is_active=True),
                name='unique_wtm_org_default',
            ),
        ]

    def __str__(self):
        scope = self.site or self.client or f"org:{self.org_id}"
        return f"{self.trigger_type} → {self.template.code} @ {scope}"


# ─── Assignment config: who handles each step ────────────────────────────────

class StepAssignmentConfig(TimeStampedModel):
    """
    Defines who handles a given step_code for a given org/client/site.
    Resolution order: site → client → org default.
    Phase 1: only named_user mode is supported.
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='step_assignment_configs',
    )
    trigger_type = models.CharField(max_length=32, choices=TRIGGER_TYPE_CHOICES, default='mrf')
    step_code = models.CharField(max_length=64)
    client = models.ForeignKey(
        'sites.Client', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='step_assignment_configs',
    )
    site = models.ForeignKey(
        'sites.SiteProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='step_assignment_configs',
    )
    department = models.ForeignKey(
        'core.Department', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='step_assignment_configs',
        help_text='Expected department for the assigned user. Validated at workflow start.',
    )
    assignment_mode = models.CharField(
        max_length=16, choices=ASSIGNMENT_MODE_CHOICES, default='named_user',
    )
    named_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='step_assignment_configs',
    )
    eligible_role = models.ForeignKey(
        'access.AccessRole', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='step_assignment_configs',
    )
    eligible_scope = models.ForeignKey(
        'core.ScopeNode', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='step_assignment_configs',
    )
    note = models.TextField(blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Step Assignment Config'
        verbose_name_plural = 'Step Assignment Configs'
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'trigger_type', 'step_code', 'site'],
                condition=models.Q(site__isnull=False, is_active=True),
                name='unique_sac_site',
            ),
            models.UniqueConstraint(
                fields=['org', 'trigger_type', 'step_code', 'client'],
                condition=models.Q(client__isnull=False, site__isnull=True, is_active=True),
                name='unique_sac_client',
            ),
            models.UniqueConstraint(
                fields=['org', 'trigger_type', 'step_code'],
                condition=models.Q(client__isnull=True, site__isnull=True, is_active=True),
                name='unique_sac_org_default',
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}

        if self.department_id and self.org_id:
            from apps.core.models import Department
            dept_info = (
                Department.objects
                .filter(pk=self.department_id)
                .values('org_id', 'client_id', 'site_id', 'name')
                .first()
            )
            if dept_info:
                dept_name = dept_info['name']

                # Department must belong to same org
                if dept_info['org_id'] != self.org_id:
                    errors['department'] = 'Department must belong to the same organization.'
                else:
                    # Department scope must be compatible with the assignment scope
                    dept_client_id = dept_info['client_id']
                    dept_site_id = dept_info['site_id']

                    if self.site_id:
                        # Site-level SAC: dept must be org-level, same-client-level, or same-site-level
                        from apps.sites.models import SiteProfile
                        site_client_id = (
                            SiteProfile.objects
                            .filter(pk=self.site_id)
                            .values_list('client_id', flat=True)
                            .first()
                        )
                        if dept_site_id is not None and dept_site_id != self.site_id:
                            errors['department'] = (
                                f'Department "{dept_name}" is scoped to a different site. '
                                f'For a site-level assignment, department must be org-level, '
                                f'client-level for the same client, or site-level for this site.'
                            )
                        elif dept_site_id is None and dept_client_id is not None and dept_client_id != site_client_id:
                            errors['department'] = (
                                f'Department "{dept_name}" belongs to a different client. '
                                f'For a site-level assignment, department must be org-level, '
                                f'client-level for the same client, or site-level for this site.'
                            )
                    elif self.client_id:
                        # Client-level SAC: dept must be org-level or same-client-level
                        if dept_site_id is not None:
                            errors['department'] = (
                                f'Department "{dept_name}" is site-scoped. '
                                f'For a client-level assignment, department must be org-level '
                                f'or client-level for the same client.'
                            )
                        elif dept_client_id is not None and dept_client_id != self.client_id:
                            errors['department'] = (
                                f'Department "{dept_name}" belongs to a different client. '
                                f'For a client-level assignment, department must be org-level '
                                f'or client-level for the same client.'
                            )
                    else:
                        # Org-level SAC: department must be org-level (no client/site scope)
                        if dept_client_id is not None or dept_site_id is not None:
                            errors['department'] = (
                                f'Department "{dept_name}" is not org-level. '
                                f'For an org-level assignment, department must have no client or site scope.'
                            )

        # Named user must belong to the specified department
        if self.department_id and self.named_user_id and 'department' not in errors:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user_dept_id = (
                User.objects
                .filter(pk=self.named_user_id)
                .values_list('department_id', flat=True)
                .first()
            )
            if user_dept_id != self.department_id:
                from apps.core.models import Department
                dept_name = (
                    Department.objects
                    .filter(pk=self.department_id)
                    .values_list('name', flat=True)
                    .first()
                ) or ''
                username = (
                    User.objects
                    .filter(pk=self.named_user_id)
                    .values_list('username', flat=True)
                    .first()
                ) or ''
                errors['named_user'] = (
                    f'User "{username}" does not belong to department "{dept_name}". '
                    f'Either change the department or select a different user.'
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        scope = self.site or self.client or f"org:{self.org_id}"
        return f"{self.trigger_type}/{self.step_code} → {self.assignment_mode} @ {scope}"


# ─── Runtime instances ────────────────────────────────────────────────────────

class WorkflowInstance(TimeStampedModel):
    """
    A running workflow attached to exactly one target object.
    Either mrf or client_onboarding_request must be set (never both, never neither).
    Enforced at the service layer.
    """

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    org = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name='workflow_instances',
    )
    mrf = models.ForeignKey(
        'mrf.ManpowerRequest', on_delete=models.PROTECT,
        null=True, blank=True, related_name='workflow_instances',
    )
    client_onboarding_request = models.ForeignKey(
        'onboarding.ClientOnboardingRequest', on_delete=models.PROTECT,
        null=True, blank=True, related_name='workflow_instances',
    )
    template = models.ForeignKey(
        WorkflowTemplate, on_delete=models.PROTECT, related_name='instances',
    )
    template_version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='active')
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='initiated_workflows',
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Workflow Instance'
        verbose_name_plural = 'Workflow Instances'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(mrf__isnull=False, client_onboarding_request__isnull=True) |
                    models.Q(mrf__isnull=True, client_onboarding_request__isnull=False)
                ),
                name='workflow_instance_exactly_one_target',
            ),
            models.UniqueConstraint(
                fields=['mrf'],
                condition=models.Q(status='active', mrf__isnull=False),
                name='unique_active_workflow_per_mrf',
            ),
            models.UniqueConstraint(
                fields=['client_onboarding_request'],
                condition=models.Q(status='active', client_onboarding_request__isnull=False),
                name='unique_active_workflow_per_onboarding',
            ),
        ]

    def __str__(self):
        if self.mrf_id:
            target = f"MRF #{self.mrf_id}"
        elif self.client_onboarding_request_id:
            target = f"Onboarding #{self.client_onboarding_request_id}"
        else:
            target = "Unknown"
        return f"Workflow #{self.pk} for {target} — {self.status}"

    def clean(self):
        from django.core.exceptions import ValidationError
        has_mrf = self.mrf_id is not None
        has_onboarding = self.client_onboarding_request_id is not None
        if has_mrf == has_onboarding:
            raise ValidationError(
                'WorkflowInstance must be attached to exactly one target: mrf or client_onboarding_request.'
            )


class WorkflowStepInstance(models.Model):
    """
    Runtime snapshot of one step within a WorkflowInstance.
    All template fields are copied at creation and never updated from template
    (frozen snapshot). Only runtime fields (status, acted_by, etc.) change.
    """

    STEP_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('request_changes', 'Request Changes'),
        ('skipped', 'Skipped'),
    ]

    ACTION_TAKEN_CHOICES = [
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('request_changes', 'Request Changes'),
    ]

    # ── Relationship ──────────────────────────────────────────────────────────
    workflow = models.ForeignKey(
        WorkflowInstance, on_delete=models.CASCADE, related_name='steps',
    )
    step_template = models.ForeignKey(
        WorkflowStepTemplate, on_delete=models.PROTECT, related_name='instances',
    )

    # ── Template snapshots (frozen at step creation) ──────────────────────────
    step_order = models.PositiveIntegerField()
    step_code = models.CharField(max_length=64)
    step_name = models.CharField(max_length=128)
    assignment_mode = models.CharField(max_length=16, choices=ASSIGNMENT_MODE_CHOICES)
    actor_type = models.CharField(max_length=16, choices=ACTOR_TYPE_CHOICES)
    on_approve_next = models.CharField(max_length=64, blank=True)
    on_reject_target = models.CharField(max_length=64, blank=True)
    on_request_changes_target = models.CharField(max_length=64, blank=True)
    requires_comment_on_reject = models.BooleanField(default=True)
    requires_comment_on_request_changes = models.BooleanField(default=True)
    sla_hours = models.PositiveIntegerField(null=True, blank=True)

    # ── Assignment snapshot ───────────────────────────────────────────────────
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_step_assignments',
    )
    assigned_department = models.ForeignKey(
        'core.Department', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_step_assignments',
    )
    assigned_department_name_snapshot = models.CharField(max_length=128, blank=True)
    assigned_department_code_snapshot = models.CharField(max_length=64, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)

    # ── Runtime state ─────────────────────────────────────────────────────────
    status = models.CharField(max_length=16, choices=STEP_STATUS_CHOICES, default='pending')
    acted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_step_actions',
    )
    acted_at = models.DateTimeField(null=True, blank=True)
    action_taken = models.CharField(max_length=16, choices=ACTION_TAKEN_CHOICES, blank=True)
    comment = models.TextField(blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Workflow Step Instance'
        verbose_name_plural = 'Workflow Step Instances'
        ordering = ['workflow', 'step_order']

    def __str__(self):
        return f"Step {self.step_order} ({self.step_code}) of Workflow #{self.workflow_id} — {self.status}"


class WorkflowAction(models.Model):
    """Append-only audit log for all workflow events. Never update or delete."""

    ACTION_CHOICES = [
        ('start', 'Start'),
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('request_changes', 'Request Changes'),
        ('reassign', 'Reassign'),
    ]

    workflow = models.ForeignKey(
        WorkflowInstance, on_delete=models.CASCADE, related_name='audit_trail',
    )
    step_instance = models.ForeignKey(
        WorkflowStepInstance, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit_entries',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='workflow_audit_actions',
    )
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    comment = models.TextField(blank=True)
    reassign_from = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reassigned_from_workflow_steps',
    )
    reassign_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reassigned_to_workflow_steps',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Workflow Action'
        verbose_name_plural = 'Workflow Actions'
        ordering = ['workflow', 'created_at']

    def __str__(self):
        return f"[{self.action}] on Workflow #{self.workflow_id} by {self.actor_id} at {self.created_at}"

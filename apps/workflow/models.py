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


# ─── Approval Routes ─────────────────────────────────────────────────────────

class ApprovalRoute(TimeStampedModel):
    """
    A named business route that users select before sending for approval.
    Points to a WorkflowTemplate and carries a set of step assignments.

    Scope levels:
      org route:    client=null, site=null
      client route: client set, site=null
      site route:   site set
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='approval_routes',
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    trigger_type = models.CharField(max_length=32, choices=TRIGGER_TYPE_CHOICES)
    template = models.ForeignKey(
        WorkflowTemplate, on_delete=models.PROTECT, related_name='approval_routes',
    )
    client = models.ForeignKey(
        'sites.Client', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approval_routes',
    )
    site = models.ForeignKey(
        'sites.SiteProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approval_routes',
    )
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Approval Route'
        verbose_name_plural = 'Approval Routes'
        ordering = ['org', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'code'],
                name='unique_ar_org_code',
            ),
            # Only one active default per org+trigger_type+site scope
            models.UniqueConstraint(
                fields=['org', 'trigger_type', 'site'],
                condition=models.Q(site__isnull=False, is_default=True, is_active=True),
                name='unique_ar_default_site',
            ),
            # Only one active default per org+trigger_type+client scope (no site)
            models.UniqueConstraint(
                fields=['org', 'trigger_type', 'client'],
                condition=models.Q(
                    client__isnull=False, site__isnull=True, is_default=True, is_active=True,
                ),
                name='unique_ar_default_client',
            ),
            # Only one active default per org+trigger_type at org level
            models.UniqueConstraint(
                fields=['org', 'trigger_type'],
                condition=models.Q(
                    client__isnull=True, site__isnull=True, is_default=True, is_active=True,
                ),
                name='unique_ar_default_org',
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}

        if self.template_id and self.org_id:
            tpl = (
                WorkflowTemplate.objects
                .filter(pk=self.template_id)
                .values('org_id', 'trigger_type')
                .first()
            )
            if tpl:
                if tpl['org_id'] is not None and tpl['org_id'] != self.org_id:
                    errors['template'] = (
                        'Template must belong to the same organization as the route.'
                    )
                if tpl['trigger_type'] != self.trigger_type:
                    errors['trigger_type'] = (
                        f'Route trigger_type "{self.trigger_type}" does not match '
                        f'template trigger_type "{tpl["trigger_type"]}".'
                    )

        if self.org_id:
            from apps.sites.models import Client as ClientModel, SiteProfile as SiteModel

            if self.client_id:
                client_org_id = (
                    ClientModel.objects
                    .filter(pk=self.client_id)
                    .values_list('org_id', flat=True)
                    .first()
                )
                if client_org_id is not None and client_org_id != self.org_id:
                    errors['client'] = 'Client must belong to the same organization as the route.'

            if self.site_id:
                site_data = (
                    SiteModel.objects
                    .filter(pk=self.site_id)
                    .values('org_id', 'client_id')
                    .first()
                )
                if site_data:
                    if site_data['org_id'] != self.org_id:
                        errors['site'] = 'Site must belong to the same organization as the route.'
                    elif self.client_id and site_data['client_id'] != self.client_id:
                        errors['site'] = 'Site must belong to the selected client.'

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        scope = self.site or self.client or f'org:{self.org_id}'
        return f'{self.name} ({self.trigger_type}) @ {scope}'


class ApprovalRouteStepAssignment(TimeStampedModel):
    """
    Defines who handles each template step when a workflow is started via an ApprovalRoute.
    Phase 1: only named_user assignment mode is supported for active assignments.
    """
    route = models.ForeignKey(
        ApprovalRoute, on_delete=models.CASCADE, related_name='step_assignments',
    )
    step_code = models.CharField(max_length=64)
    department = models.ForeignKey(
        'core.Department', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approval_route_step_assignments',
    )
    assignment_mode = models.CharField(
        max_length=16, choices=ASSIGNMENT_MODE_CHOICES, default='named_user',
    )
    named_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approval_route_step_assignments',
    )
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Approval Route Step Assignment'
        verbose_name_plural = 'Approval Route Step Assignments'
        ordering = ['route', 'step_code']
        constraints = [
            models.UniqueConstraint(
                fields=['route', 'step_code'],
                condition=models.Q(is_active=True),
                name='unique_arsa_route_step',
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}

        # step_code must exist in route.template steps
        if self.route_id and self.step_code:
            route_template_id = (
                ApprovalRoute.objects
                .filter(pk=self.route_id)
                .values_list('template_id', flat=True)
                .first()
            )
            if route_template_id is not None:
                if not WorkflowStepTemplate.objects.filter(
                    template_id=route_template_id, code=self.step_code,
                ).exists():
                    errors['step_code'] = (
                        f'Step code "{self.step_code}" does not exist in the route\'s template.'
                    )

        # Named user required and valid for active named_user assignments
        if self.is_active and self.assignment_mode == 'named_user':
            if not self.named_user_id:
                errors['named_user'] = (
                    'A named user is required for named_user assignment mode.'
                )
            elif self.route_id:
                route_org_id = (
                    ApprovalRoute.objects
                    .filter(pk=self.route_id)
                    .values_list('org_id', flat=True)
                    .first()
                )
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user_data = (
                    User.objects
                    .filter(pk=self.named_user_id)
                    .values('is_active', 'org_id', 'username')
                    .first()
                )
                if user_data:
                    if not user_data['is_active']:
                        errors['named_user'] = f'User "{user_data["username"]}" is inactive.'
                    elif route_org_id and user_data['org_id'] != route_org_id:
                        errors['named_user'] = (
                            'User must belong to the same organization as the route.'
                        )

        # Department scope and user-department match validation
        if self.department_id and self.route_id and 'department' not in errors:
            from apps.core.models import Department
            dept_data = (
                Department.objects
                .filter(pk=self.department_id)
                .values('org_id', 'client_id', 'site_id', 'name')
                .first()
            )
            route_data = (
                ApprovalRoute.objects
                .filter(pk=self.route_id)
                .values('org_id', 'client_id', 'site_id')
                .first()
            )
            if dept_data and route_data:
                if dept_data['org_id'] != route_data['org_id']:
                    errors['department'] = (
                        'Department must belong to the same organization as the route.'
                    )
                else:
                    dept_name = dept_data['name']
                    dept_client_id = dept_data['client_id']
                    dept_site_id = dept_data['site_id']
                    route_site_id = route_data['site_id']
                    route_client_id = route_data['client_id']

                    if route_site_id:
                        from apps.sites.models import SiteProfile
                        site_client_id = (
                            SiteProfile.objects
                            .filter(pk=route_site_id)
                            .values_list('client_id', flat=True)
                            .first()
                        )
                        if dept_site_id is not None and dept_site_id != route_site_id:
                            errors['department'] = (
                                f'Department "{dept_name}" is scoped to a different site. '
                                f'Site-level route requires org-level, same-client-level, '
                                f'or same-site-level department.'
                            )
                        elif (dept_site_id is None and dept_client_id is not None
                              and dept_client_id != site_client_id):
                            errors['department'] = (
                                f'Department "{dept_name}" belongs to a different client. '
                                f'Site-level route requires org-level, same-client-level, '
                                f'or same-site-level department.'
                            )
                    elif route_client_id:
                        if dept_site_id is not None:
                            errors['department'] = (
                                f'Department "{dept_name}" is site-scoped. '
                                f'Client-level route requires org-level or same-client department.'
                            )
                        elif dept_client_id is not None and dept_client_id != route_client_id:
                            errors['department'] = (
                                f'Department "{dept_name}" belongs to a different client. '
                                f'Client-level route requires org-level or same-client department.'
                            )
                    else:
                        if dept_client_id is not None or dept_site_id is not None:
                            errors['department'] = (
                                f'Department "{dept_name}" is not org-level. '
                                f'Org-level route requires org-level department.'
                            )

        # named_user must belong to specified department
        if (self.department_id and self.named_user_id
                and 'department' not in errors and 'named_user' not in errors):
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
                errors['named_user'] = (
                    f'User does not belong to department "{dept_name}".'
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.route.code}/{self.step_code} → {self.assignment_mode}'


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

    # Selected approval route (nullable for legacy workflows started before routes existed)
    approval_route = models.ForeignKey(
        ApprovalRoute, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_instances',
    )
    approval_route_name_snapshot = models.CharField(max_length=255, blank=True)
    approval_route_code_snapshot = models.CharField(max_length=64, blank=True)

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

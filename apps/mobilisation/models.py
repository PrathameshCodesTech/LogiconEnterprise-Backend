"""
apps/mobilisation/models.py

MobilisationSetupRequest — post-win mobilisation setup request.
Proposed setup models — planned departments/users stored before real records exist.

All mobilisation is sales-led. Client/Sites/SRRs/BudgetPlan are created during
convert_won_sales_lead_to_onboarding_setup(); only departments and users are
proposed here and created during finalization.
"""

from django.conf import settings
from django.db import models

from apps.core.models import Organization, TimeStampedModel


MOBILISATION_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('operations_setup', 'Operations Setup'),
    ('setup_completed', 'Setup Completed'),
    ('submitted', 'Submitted'),
    ('in_review', 'In Review'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]

MOBILISATION_TYPE_CHOICES = [
    ('new_client', 'New Client'),
    ('new_site_expansion', 'New Site Expansion'),
    ('scope_expansion', 'Scope Expansion'),
]

FINALIZATION_STATUS_CHOICES = [
    ('not_finalized', 'Not Finalized'),
    ('finalized', 'Finalized'),
    ('failed', 'Failed'),
]

SETUP_STRATEGY_CHOICES = [
    ('simple', 'Simple'),
    ('role_grouped', 'Role Grouped'),
    ('custom', 'Custom'),
]

SCOPE_LEVEL_CHOICES = [
    ('client', 'Client'),
    ('site', 'Site'),
]


class MobilisationSetupRequest(TimeStampedModel):
    """
    An internal request to mobilise a new client after a SalesLead is won.
    Created programmatically by convert_won_sales_lead_to_onboarding_setup().

    Client/Sites/SRRs/BudgetPlan are created at conversion time.
    Only proposed departments and users are stored here for finalization.
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE,
        related_name='mobilisation_requests',
    )
    client = models.ForeignKey(
        'sites.Client', on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='mobilisation_requests',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='mobilisation_requests',
    )
    status = models.CharField(
        max_length=16, choices=MOBILISATION_STATUS_CHOICES, default='draft',
    )
    mobilisation_type = models.CharField(
        max_length=32, choices=MOBILISATION_TYPE_CHOICES,
    )
    summary = models.TextField(blank=True)
    operations_notes = models.TextField(blank=True)
    hr_notes = models.TextField(blank=True)
    finance_notes = models.TextField(blank=True)
    budget_plan = models.ForeignKey(
        'budgets.BudgetPlan',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='mobilisation_requests',
    )

    # ── Finalization fields ────────────────────────────────────────────────────
    assigned_operations_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_mobilisation_setup_requests',
    )
    submitted_to_operations_at = models.DateTimeField(null=True, blank=True)
    setup_completed_at = models.DateTimeField(null=True, blank=True)
    setup_completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='completed_mobilisation_setup_requests',
    )

    finalization_status = models.CharField(
        max_length=16, choices=FINALIZATION_STATUS_CHOICES, default='not_finalized',
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='finalized_mobilisation_requests',
    )
    finalization_error = models.TextField(blank=True)

    # ── Sales-bridge fields ────────────────────────────────────────────────────
    source_sales_lead = models.ForeignKey(
        'sales.SalesLead', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mobilisation_requests',
    )
    source_proposal_version = models.ForeignKey(
        'sales.ProposalVersion', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mobilisation_requests',
    )

    mobilisation_requires_approval = models.BooleanField(default=True)
    setup_strategy = models.CharField(
        max_length=20, choices=SETUP_STRATEGY_CHOICES, default='custom',
        help_text='How the operations setup builder was last generated/saved.',
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'mobilisation_setup_request'
        verbose_name = 'Mobilisation Setup Request'
        verbose_name_plural = 'Mobilisation Setup Requests'
        ordering = ['-created_at']

    def __str__(self):
        label = self.client or '(no client)'
        return f"Mobilisation #{self.pk}: {label} ({self.mobilisation_type}) — {self.status}"


class MobilisationProposedDepartment(TimeStampedModel):
    """Planned department to be created under the mobilised client/site during finalization."""
    request = models.ForeignKey(
        MobilisationSetupRequest, on_delete=models.CASCADE,
        related_name='proposed_departments',
    )
    real_site = models.ForeignKey(
        'sites.SiteProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mobilisation_proposed_departments',
    )
    scope_level = models.CharField(max_length=8, choices=SCOPE_LEVEL_CHOICES, default='site')
    name = models.CharField(max_length=128)
    code = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_locked = models.BooleanField(
        default=False,
        help_text='Locked system-required rows, such as Client Administration, cannot be removed.',
    )
    source_key = models.CharField(
        max_length=128, blank=True,
        help_text='Stable builder/suggestion key used for idempotent setup generation.',
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'mobilisation_proposed_department'
        verbose_name = 'Mobilisation Proposed Department'
        verbose_name_plural = 'Mobilisation Proposed Departments'
        ordering = ['sort_order', 'name', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'code'],
                condition=models.Q(is_active=True, real_site__isnull=True),
                name='unique_active_mob_dept_client_level',
            ),
            models.UniqueConstraint(
                fields=['request', 'real_site', 'code'],
                condition=models.Q(is_active=True, real_site__isnull=False),
                name='unique_active_mob_dept_real_site_level',
            ),
        ]

    def __str__(self):
        return f"Proposed Dept {self.code} for Mobilisation #{self.request_id}"


class MobilisationProposedDepartmentRole(TimeStampedModel):
    """Maps an approved site role requirement into a proposed mobilisation department."""
    request = models.ForeignKey(
        MobilisationSetupRequest,
        on_delete=models.CASCADE,
        related_name='proposed_department_roles',
    )
    proposed_department = models.ForeignKey(
        MobilisationProposedDepartment,
        on_delete=models.CASCADE,
        related_name='role_mappings',
    )
    site_role_requirement = models.ForeignKey(
        'sites.SiteRoleRequirement',
        on_delete=models.CASCADE,
        related_name='mobilisation_department_mappings',
    )
    real_site = models.ForeignKey(
        'sites.SiteProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mobilisation_department_role_mappings',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mobilisation_department_role_mappings',
    )
    approved_headcount_snapshot = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'mobilisation_proposed_department_role'
        verbose_name = 'Mobilisation Proposed Department Role'
        verbose_name_plural = 'Mobilisation Proposed Department Roles'
        ordering = ['real_site__name', 'sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'site_role_requirement'],
                condition=models.Q(is_active=True),
                name='unique_active_mob_role_assignment',
            ),
        ]
        indexes = [
            models.Index(fields=['request', 'is_active']),
            models.Index(fields=['proposed_department', 'is_active']),
            models.Index(fields=['site_role_requirement', 'is_active']),
        ]

    def save(self, *args, **kwargs):
        if self.site_role_requirement_id:
            self.real_site_id = self.site_role_requirement.site_id
            self.job_role_id = self.site_role_requirement.job_role_id
            self.approved_headcount_snapshot = self.site_role_requirement.approved_headcount or 0
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"Role mapping SRR #{self.site_role_requirement_id} "
            f"â†’ Proposed Dept #{self.proposed_department_id}"
        )


class MobilisationProposedUser(TimeStampedModel):
    """Planned login user to be created when mobilisation is approved and finalized."""
    request = models.ForeignKey(
        MobilisationSetupRequest,
        on_delete=models.CASCADE,
        related_name='proposed_users',
    )
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    user_type = models.CharField(
        max_length=32,
        choices=[('client', 'Client'), ('site_manager', 'Site Manager')],
        default='client',
    )
    access_role = models.ForeignKey(
        'access.AccessRole',
        on_delete=models.PROTECT,
        related_name='mobilisation_proposed_users',
    )
    scope_level = models.CharField(
        max_length=16,
        choices=[('client', 'Client'), ('site', 'Site')],
        default='client',
    )
    real_site = models.ForeignKey(
        'sites.SiteProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mobilisation_proposed_users',
    )
    is_primary_contact = models.BooleanField(default=False)
    send_invite_on_finalization = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_from_mobilisation_proposals',
    )
    invite_status = models.CharField(
        max_length=32,
        choices=[
            ('pending', 'Pending'),
            ('not_required', 'Not required'),
            ('sent', 'Sent'),
            ('failed', 'Failed'),
        ],
        default='pending',
    )
    invite_error = models.TextField(blank=True)

    class Meta:
        db_table = 'mobilisation_proposed_user'
        verbose_name = 'Mobilisation Proposed User'
        verbose_name_plural = 'Mobilisation Proposed Users'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'email'],
                condition=models.Q(is_active=True),
                name='unique_active_mob_proposed_user_email',
            ),
            models.UniqueConstraint(
                fields=['request'],
                condition=models.Q(is_active=True, is_primary_contact=True),
                name='unique_primary_mob_proposed_user',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Proposed User {self.email} for Mobilisation #{self.request_id}"

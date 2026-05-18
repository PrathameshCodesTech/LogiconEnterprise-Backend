"""
apps/onboarding/models.py

ClientOnboardingRequest — internal setup request when Sales onboards a new client or site.
Proposed setup models — planned sites/departments/SRRs stored before real records exist.
"""

from django.conf import settings
from django.db import models

from apps.budgets.models import BUDGET_NATURE_CHOICES, BUDGET_TYPE_CHOICES
from apps.core.models import Organization, TimeStampedModel


ONBOARDING_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('in_review', 'In Review'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]

ONBOARDING_TYPE_CHOICES = [
    ('new_client', 'New Client'),
    ('new_site_expansion', 'New Site Expansion'),
]

FINALIZATION_STATUS_CHOICES = [
    ('not_finalized', 'Not Finalized'),
    ('finalized', 'Finalized'),
    ('failed', 'Failed'),
]

SCOPE_LEVEL_CHOICES = [
    ('client', 'Client'),
    ('site', 'Site'),
]

BUDGET_SCOPE_LEVEL_CHOICES = [
    ('client', 'Client'),
    ('site', 'Site'),
    ('department', 'Department'),
]


class ClientOnboardingRequest(TimeStampedModel):
    """
    An internal request to onboard a new client or expand an existing client.
    Created by Sales; reviewed through the client_onboarding workflow.

    new_client:        client is null; proposed_client_* fields carry the intent.
    new_site_expansion: client is set (existing master); proposed_client_* unused.
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE,
        related_name='client_onboarding_requests',
    )
    client = models.ForeignKey(
        'sites.Client', on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='onboarding_requests',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='client_onboarding_requests',
    )
    status = models.CharField(
        max_length=16, choices=ONBOARDING_STATUS_CHOICES, default='draft',
    )
    onboarding_type = models.CharField(
        max_length=32, choices=ONBOARDING_TYPE_CHOICES,
    )
    expected_site_count = models.PositiveIntegerField(null=True, blank=True)
    summary = models.TextField(blank=True)
    operations_notes = models.TextField(blank=True)
    hr_notes = models.TextField(blank=True)
    finance_notes = models.TextField(blank=True)
    budget_plan = models.ForeignKey(
        'budgets.BudgetPlan',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='client_onboarding_requests',
    )

    # ── Proposed client fields (new_client type only) ──────────────────────────
    proposed_client_name = models.CharField(max_length=255, blank=True)
    proposed_client_code = models.CharField(max_length=64, blank=True)
    proposed_contact_name = models.CharField(max_length=128, blank=True)
    proposed_contact_email = models.EmailField(blank=True)
    proposed_contact_phone = models.CharField(max_length=20, blank=True)
    proposed_industry = models.CharField(max_length=128, blank=True)
    proposed_billing_address = models.TextField(blank=True)
    proposed_gst_number = models.CharField(max_length=20, blank=True)

    # ── Finalization fields ────────────────────────────────────────────────────
    created_client = models.ForeignKey(
        'sites.Client', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='finalized_from',
    )
    finalization_status = models.CharField(
        max_length=16, choices=FINALIZATION_STATUS_CHOICES, default='not_finalized',
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='finalized_onboarding_requests',
    )
    finalization_error = models.TextField(blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Client Onboarding Request'
        verbose_name_plural = 'Client Onboarding Requests'
        ordering = ['-created_at']

    def __str__(self):
        label = self.client or self.proposed_client_name or '(no client)'
        return f"Onboarding #{self.pk}: {label} ({self.onboarding_type}) — {self.status}"


class ClientOnboardingProposedSite(TimeStampedModel):
    """Planned site to be created when onboarding is approved and finalized."""
    request = models.ForeignKey(
        ClientOnboardingRequest, on_delete=models.CASCADE,
        related_name='proposed_sites',
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=128, blank=True)
    state = models.CharField(max_length=128, blank=True)
    pincode = models.CharField(max_length=12, blank=True)
    contact_person = models.CharField(max_length=128, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    location_area = models.ForeignKey(
        'wages.LocationArea', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposed_sites',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Proposed Site'
        verbose_name_plural = 'Proposed Sites'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'code'],
                condition=models.Q(is_active=True),
                name='unique_active_proposed_site_code_per_request',
            ),
        ]

    def __str__(self):
        return f"Proposed Site {self.code} for Onboarding #{self.request_id}"


class ClientOnboardingProposedDepartment(TimeStampedModel):
    """Planned department to be created under the onboarded client/site."""
    request = models.ForeignKey(
        ClientOnboardingRequest, on_delete=models.CASCADE,
        related_name='proposed_departments',
    )
    proposed_site = models.ForeignKey(
        ClientOnboardingProposedSite, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposed_departments',
    )
    scope_level = models.CharField(max_length=8, choices=SCOPE_LEVEL_CHOICES, default='site')
    name = models.CharField(max_length=128)
    code = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Proposed Department'
        verbose_name_plural = 'Proposed Departments'
        ordering = ['-created_at']
        constraints = [
            # client-level: unique active code per request (no site)
            models.UniqueConstraint(
                fields=['request', 'code'],
                condition=models.Q(is_active=True, proposed_site__isnull=True),
                name='unique_active_proposed_dept_client_level',
            ),
            # site-level: unique active code per request+site
            models.UniqueConstraint(
                fields=['request', 'proposed_site', 'code'],
                condition=models.Q(is_active=True, proposed_site__isnull=False),
                name='unique_active_proposed_dept_site_level',
            ),
        ]

    def __str__(self):
        return f"Proposed Dept {self.code} for Onboarding #{self.request_id}"


class ClientOnboardingProposedSiteRoleRequirement(TimeStampedModel):
    """Planned SiteRoleRequirement to be created after finalization."""
    request = models.ForeignKey(
        ClientOnboardingRequest, on_delete=models.CASCADE,
        related_name='proposed_role_requirements',
    )
    proposed_site = models.ForeignKey(
        ClientOnboardingProposedSite, on_delete=models.CASCADE,
        related_name='proposed_role_requirements',
    )
    proposed_department = models.ForeignKey(
        ClientOnboardingProposedDepartment, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposed_role_requirements',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole', on_delete=models.CASCADE,
        related_name='proposed_site_requirements',
    )
    approved_headcount = models.PositiveIntegerField()
    billing_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    wage_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wage_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shift_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    billing_type = models.CharField(
        max_length=16,
        choices=[('billable', 'Billable'), ('non_billable', 'Non-Billable')],
        default='billable',
    )
    wage_category = models.ForeignKey(
        'wages.WageCategory', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposed_site_requirements',
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Proposed Site Role Requirement'
        verbose_name_plural = 'Proposed Site Role Requirements'
        ordering = ['-created_at']

    def __str__(self):
        return f"Proposed SRR {self.job_role_id} × {self.approved_headcount} for Onboarding #{self.request_id}"


class ClientOnboardingProposedBudget(TimeStampedModel):
    """Planned BudgetPlan to be created when onboarding is approved and finalized."""
    request = models.ForeignKey(
        ClientOnboardingRequest, on_delete=models.CASCADE,
        related_name='proposed_budgets',
    )
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=64)
    budget_nature = models.CharField(max_length=16, choices=BUDGET_NATURE_CHOICES)
    budget_type = models.CharField(
        max_length=16, choices=BUDGET_TYPE_CHOICES, default='onboarding',
    )
    scope_level = models.CharField(
        max_length=16, choices=BUDGET_SCOPE_LEVEL_CHOICES, default='client',
    )
    proposed_site = models.ForeignKey(
        ClientOnboardingProposedSite, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposed_budgets',
    )
    proposed_department = models.ForeignKey(
        ClientOnboardingProposedDepartment, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposed_budgets',
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=8, default='INR')
    period_start = models.DateField()
    period_end = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Proposed Budget'
        verbose_name_plural = 'Proposed Budgets'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'code'],
                condition=models.Q(is_active=True),
                name='unique_active_proposed_budget_code_per_request',
            ),
        ]

    def __str__(self):
        return f"Proposed Budget {self.code} for Onboarding #{self.request_id}"


class ClientOnboardingProposedUser(TimeStampedModel):
    """Planned login user to be created when onboarding is approved and finalized."""
    request = models.ForeignKey(
        ClientOnboardingRequest,
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
        related_name='onboarding_proposed_users',
    )
    scope_level = models.CharField(
        max_length=16,
        choices=[('client', 'Client'), ('site', 'Site')],
        default='client',
    )
    proposed_site = models.ForeignKey(
        ClientOnboardingProposedSite,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='proposed_users',
    )
    is_primary_contact = models.BooleanField(default=False)
    send_invite_on_finalization = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_from_onboarding_proposals',
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
        verbose_name = 'Proposed User'
        verbose_name_plural = 'Proposed Users'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'email'],
                condition=models.Q(is_active=True),
                name='unique_active_proposed_user_email_per_request',
            ),
            models.UniqueConstraint(
                fields=['request'],
                condition=models.Q(is_active=True, is_primary_contact=True),
                name='unique_primary_proposed_user_per_request',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Proposed User {self.email} for Onboarding #{self.request_id}"

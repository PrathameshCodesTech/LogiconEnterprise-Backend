"""
apps/mrf/models.py

ManpowerRequest (MRF) and MRFLineItem.

One MRF is raised for one site but can contain multiple line items
(one per job role). Each line item references a SiteRoleRequirement
to validate against approved headcount limits.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import Organization, TimeStampedModel


MRF_TYPE_CHOICES = [
    ('new_hiring', 'New Hiring'),
    ('replacement', 'Replacement'),
    ('headcount_increase', 'Headcount Increase'),
    ('rate_revision', 'Rate Revision'),
]

MRF_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('hr_review', 'HR Review'),
    ('finance_review', 'Finance Review'),
    ('admin_review', 'Admin/HOD Review'),
    ('client_review', 'Client Review'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]


class ManpowerRequest(TimeStampedModel):
    """Manpower Requisition Form - a formal request for workers at a site."""

    BILLING_TYPE_CHOICES = [
        ('billable', 'Billable'),
        ('non_billable', 'Non-Billable'),
    ]

    REQUESTER_TYPE_CHOICES = [
        ('internal', 'Internal'),
        ('client', 'Client'),
    ]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='manpower_requests')
    site = models.ForeignKey(
        'sites.SiteProfile',
        on_delete=models.CASCADE,
        related_name='manpower_requests',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='manpower_requests',
    )
    requested_by_type = models.CharField(
        max_length=16,
        choices=REQUESTER_TYPE_CHOICES,
        default='internal',
    )
    mrf_type = models.CharField(max_length=32, choices=MRF_TYPE_CHOICES)
    status = models.CharField(max_length=32, choices=MRF_STATUS_CHOICES, default='draft')
    requesting_department = models.ForeignKey(
        'core.Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requested_mrfs',
        help_text='Department/team raising the manpower request.',
    )
    required_department = models.ForeignKey(
        'core.Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='required_mrfs',
        help_text='Department/service where manpower is needed.',
    )
    department = models.CharField(max_length=128, blank=True)
    billing_type = models.CharField(max_length=16, choices=BILLING_TYPE_CHOICES)
    required_by_date = models.DateField(null=True, blank=True)
    reason = models.TextField(blank=True)
    client_visible = models.BooleanField(
        default=False,
        help_text='Whether this MRF is visible to the client user portal.',
    )
    budget_plan = models.ForeignKey(
        'budgets.BudgetPlan',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='mrf_requests',
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    # ── Client-form header fields ─────────────────────────────────────────────
    request_number = models.CharField(max_length=64, blank=True)

    experience_min_years = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    experience_max_years = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
    )

    reporting_to = models.CharField(max_length=255, blank=True)
    mis_requirement = models.CharField(max_length=255, blank=True)

    education_requirement = models.TextField(blank=True)
    gender_preference = models.CharField(max_length=64, blank=True)
    special_requirement = models.TextField(blank=True)

    salary_range_text = models.CharField(max_length=255, blank=True)
    ctc_budget_text = models.CharField(max_length=255, blank=True)

    reference_note = models.TextField(blank=True)
    other_remarks = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Manpower Request'
        verbose_name_plural = 'Manpower Requests'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['org', 'status']),
            models.Index(fields=['site', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'request_number'],
                condition=~Q(request_number=''),
                name='unique_mrf_request_number_per_org_when_set',
            ),
        ]

    def __str__(self):
        return f"MRF #{self.pk} ({self.mrf_type}) - {self.status}"


class MRFLineItem(models.Model):
    """
    A single line item in an MRF - one job role with headcount.

    site_role_requirement links back to the approved limit for this role
    at this site, enabling validation at submission and approval time.
    Wage and billing snapshots are captured at MRF creation for audit trail.
    """

    mrf = models.ForeignKey(ManpowerRequest, on_delete=models.CASCADE, related_name='line_items')
    site_role_requirement = models.ForeignKey(
        'sites.SiteRoleRequirement',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mrf_line_items',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.PROTECT,
        related_name='mrf_line_items',
    )
    headcount = models.PositiveIntegerField()
    replacement_for_employee = models.CharField(max_length=255, blank=True)
    required_skills = models.JSONField(default=list)
    wage_category = models.ForeignKey(
        'wages.WageCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mrf_line_items',
    )
    min_wage_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wage_min_requested = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wage_max_requested = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    billing_rate_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    budget_plan = models.ForeignKey(
        'budgets.BudgetPlan',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='mrf_line_items',
    )

    class Meta:
        verbose_name = 'MRF Line Item'
        verbose_name_plural = 'MRF Line Items'

    def __str__(self):
        return f"MRF #{self.mrf_id} - {self.job_role} x {self.headcount}"


class MRFSupportRequirement(TimeStampedModel):
    """IT and Admin support checklist linked one-to-one to an MRF."""

    mrf = models.OneToOneField(
        ManpowerRequest,
        on_delete=models.CASCADE,
        related_name='support_requirement',
    )

    # IT Use Only
    laptop_required = models.BooleanField(default=False)
    desktop_required = models.BooleanField(default=False)
    mail_id_required = models.BooleanField(default=False)
    hrms_login_required = models.BooleanField(default=False)
    outlook_required = models.BooleanField(default=False)
    ms_office_required = models.BooleanField(default=False)
    windows_required = models.BooleanField(default=False)
    own_or_rental = models.CharField(max_length=64, blank=True)

    # Admin Use Only
    sim_card_required = models.BooleanField(default=False)
    data_card_required = models.BooleanField(default=False)
    uniform_required = models.BooleanField(default=False)
    visiting_cards_required = models.BooleanField(default=False)
    seating_required = models.BooleanField(default=False)
    admin_location = models.CharField(max_length=255, blank=True)
    admin_other = models.TextField(blank=True)

    class Meta:
        verbose_name = 'MRF Support Requirement'
        verbose_name_plural = 'MRF Support Requirements'

    def __str__(self):
        return f"Support for MRF #{self.mrf_id}"

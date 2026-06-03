"""
apps/budgets/models.py

BudgetPlan — source-of-truth for billable and non-billable budgets.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import Organization, Department, TimeStampedModel


SOURCE_TYPE_CHOICES = [
    ('sales_conversion', 'Sales Conversion'),
    ('manual_admin', 'Manual Admin'),
    ('import', 'Import'),
]

BUDGET_NATURE_CHOICES = [
    ('billable', 'Billable'),
    ('non_billable', 'Non-Billable'),
]

BUDGET_TYPE_CHOICES = [
    ('onboarding', 'Onboarding'),
    ('manpower', 'Manpower'),
    ('hiring', 'Hiring'),
    ('operations', 'Operations'),
    ('deployment', 'Deployment'),
    ('general', 'General'),
]

BUDGET_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('closed', 'Closed'),
    ('inactive', 'Inactive'),
]

RESERVATION_STATUS_CHOICES = [
    ('reserved', 'Reserved'),
    ('committed', 'Committed'),
    ('released', 'Released'),
    ('cancelled', 'Cancelled'),
]


class BudgetPlan(TimeStampedModel):
    """
    A named budget allocation for a period, scoped to a client/site (billable)
    or a department (non-billable).
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='budget_plans'
    )
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=64)

    budget_nature = models.CharField(max_length=16, choices=BUDGET_NATURE_CHOICES)
    budget_type = models.CharField(max_length=16, choices=BUDGET_TYPE_CHOICES, default='general')

    client = models.ForeignKey(
        'sites.Client', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='budget_plans',
    )
    site = models.ForeignKey(
        'sites.SiteProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='budget_plans',
    )
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='budget_plans',
    )

    period_start = models.DateField()
    period_end = models.DateField(null=True, blank=True)

    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=8, default='INR')

    status = models.CharField(max_length=16, choices=BUDGET_STATUS_CHOICES, default='draft')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    source_type = models.CharField(
        max_length=20, choices=SOURCE_TYPE_CHOICES, default='manual_admin',
    )
    source_sales_lead = models.ForeignKey(
        'sales.SalesLead', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='converted_budget_plans',
    )
    source_proposal_version = models.ForeignKey(
        'sales.ProposalVersion', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='converted_budget_plans',
    )

    class Meta:
        verbose_name = 'Budget Plan'
        verbose_name_plural = 'Budget Plans'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'code'],
                condition=Q(is_active=True),
                name='unique_active_budget_code_per_org',
            )
        ]
        indexes = [
            models.Index(fields=['org', 'budget_nature', 'status']),
            models.Index(fields=['org', 'client', 'site']),
            models.Index(fields=['org', 'department']),
            models.Index(fields=['period_start', 'period_end']),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class BudgetReservation(TimeStampedModel):
    """
    Ledger record that holds budget while an MRF is under approval.

    Lifecycle:
      reserved  → budget held while workflow is active
      committed → workflow approved; budget consumed
      released  → workflow finally rejected or request withdrawn
      cancelled → MRF cancelled after commitment
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='budget_reservations',
    )
    budget_plan = models.ForeignKey(
        BudgetPlan, on_delete=models.PROTECT, related_name='reservations',
    )
    mrf = models.ForeignKey(
        'mrf.ManpowerRequest', on_delete=models.CASCADE, related_name='budget_reservations',
    )
    line_item = models.ForeignKey(
        'mrf.MRFLineItem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='budget_reservations',
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(
        max_length=16, choices=RESERVATION_STATUS_CHOICES, default='reserved',
    )
    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='budget_reservations_made',
    )
    reserved_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Budget Reservation'
        verbose_name_plural = 'Budget Reservations'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['mrf', 'status']),
            models.Index(fields=['budget_plan', 'status']),
        ]

    def __str__(self):
        return f'Reservation MRF#{self.mrf_id} / {self.budget_plan} — {self.status}'

"""
apps/sites/models.py

Client, SiteProfile, SiteCommercial, SiteRoleRequirement.

One client → many sites.
One site → many role requirements (headcount + commercial config per job role).
"""

from django.conf import settings
from django.db import models
from apps.core.models import Organization, ScopeNode, TimeStampedModel


class Client(TimeStampedModel):
    """A client organization that is serviced at one or more sites."""
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='clients')
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    contact_name = models.CharField(max_length=128, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    industry = models.CharField(max_length=128, blank=True)
    billing_address = models.TextField(blank=True)
    gst_number = models.CharField(max_length=20, blank=True)
    scope_node = models.ForeignKey(
        ScopeNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='client_profiles',
        help_text='Client-level scope node used for client admins and client-wide access.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_clients',
    )
    owner_sales_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_clients',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Client'
        verbose_name_plural = 'Clients'
        unique_together = [['org', 'code']]
        indexes = [
            models.Index(fields=['org', 'scope_node']),
        ]
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class SiteProfile(TimeStampedModel):
    """A physical site/location where workers are deployed."""

    SHIFT_TYPE_CHOICES = [
        ('day', 'Day'),
        ('night', 'Night'),
        ('rotational', 'Rotational'),
        ('custom', 'Custom'),
    ]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='site_profiles')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='sites')
    scope_node = models.ForeignKey(
        ScopeNode,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='site_profiles',
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=128, blank=True)
    state = models.CharField(max_length=128, blank=True)
    pincode = models.CharField(max_length=12, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geofence_radius_meters = models.IntegerField(default=100)
    shift_type = models.CharField(max_length=16, choices=SHIFT_TYPE_CHOICES, blank=True)
    contact_person = models.CharField(max_length=128, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_sites',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Site Profile'
        verbose_name_plural = 'Site Profiles'
        unique_together = [['org', 'code']]
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class SiteCommercial(TimeStampedModel):
    """Global commercial / billing details for a site (site-level, not role-level)."""
    site = models.ForeignKey(SiteProfile, on_delete=models.CASCADE, related_name='commercials')
    billing_rate = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Site-level monthly billing rate',
    )
    approved_budget_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    approved_budget_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Site Commercial'
        verbose_name_plural = 'Site Commercials'
        ordering = ['-effective_from']

    def __str__(self):
        return f"Commercial for {self.site} from {self.effective_from}"


class SiteRoleRequirement(TimeStampedModel):
    """
    Approved headcount + commercial config for one job role at one site.

    Replaces the old SiteApprovedHeadcount. A site can have many role
    requirements — e.g. Housekeeping ×10, Security Guard ×5, Electrician ×2.
    MRF line items reference this to validate requests against approved limits.
    """

    BILLING_TYPE_CHOICES = [
        ('billable', 'Billable'),
        ('non_billable', 'Non-Billable'),
    ]

    site = models.ForeignKey(SiteProfile, on_delete=models.CASCADE, related_name='role_requirements')
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.CASCADE,
        related_name='site_requirements',
    )
    approved_headcount = models.PositiveIntegerField()
    billing_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    wage_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wage_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    shift_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    billing_type = models.CharField(max_length=16, choices=BILLING_TYPE_CHOICES, default='billable')
    wage_category = models.ForeignKey(
        'wages.WageCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='site_requirements',
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Site Role Requirement'
        verbose_name_plural = 'Site Role Requirements'
        ordering = ['site', 'job_role', '-effective_from']
        indexes = [
            models.Index(fields=['site', 'job_role', 'is_active']),
            models.Index(fields=['billing_type']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['site', 'job_role', 'effective_from'],
                condition=models.Q(is_active=True),
                name='unique_active_site_role_requirement',
            )
        ]

    def __str__(self):
        return f"{self.site} — {self.job_role} × {self.approved_headcount} ({self.billing_type})"

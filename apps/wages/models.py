"""
apps/wages/models.py

LocationArea — business geography master (state/region/city/zone hierarchy).
WageCategory — skill-based classification.
MinimumWageRate — org-scoped, location/state-city aware, role/date-range wage floor.
"""

from django.db import models
from apps.core.models import TimeStampedModel


# ─── Location Area ────────────────────────────────────────────────────────────

class LocationArea(models.Model):
    """
    Business geography node (state → region → city → zone).

    Separate from ScopeNode: ScopeNode is access-control hierarchy;
    LocationArea is wage/commercial geography master.
    """

    AREA_TYPE_CHOICES = [
        ('state',  'State'),
        ('region', 'Region'),
        ('city',   'City'),
        ('zone',   'Zone'),
    ]

    name = models.CharField(max_length=128)
    code = models.CharField(max_length=64)
    area_type = models.CharField(max_length=16, choices=AREA_TYPE_CHOICES)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='children',
    )
    state_name = models.CharField(
        max_length=128, blank=True,
        help_text='Denormalized ancestor state name for fast filtering.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Location Area'
        verbose_name_plural = 'Location Areas'
        unique_together = [['parent', 'code']]
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['area_type']),
            models.Index(fields=['parent']),
            models.Index(fields=['is_active']),
        ]
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_area_type_display()})"

    def ancestor_ids(self):
        """Walk parent chain and return list of ancestor PKs (nearest first)."""
        ids = []
        node = self.parent
        while node is not None:
            ids.append(node.pk)
            node = node.parent
        return ids


# ─── Wage Category ────────────────────────────────────────────────────────────

class WageCategory(models.Model):
    """Skill-based wage category (e.g. unskilled, skilled)."""
    name = models.CharField(max_length=128)
    code = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Wage Category'
        verbose_name_plural = 'Wage Categories'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


# ─── Minimum Wage Rate ────────────────────────────────────────────────────────

class MinimumWageRate(TimeStampedModel):
    """
    Minimum wage floor for an org, location, wage category, optional role, and date range.

    Lookup priority (most-specific wins):
    1. exact location + role + wage_category
    2. parent/ancestor location + role + wage_category
    3. exact location + wage_category (role=None)
    4. parent/ancestor location + wage_category (role=None)
    5. legacy city/state fallback when no location FK is provided
    """

    org = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='wage_rates',
        help_text='Null means applies to all orgs (system-level default).',
    )
    location = models.ForeignKey(
        LocationArea,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='wage_rates',
        help_text='Structured location from LocationArea master. Takes precedence over state/city.',
    )
    state = models.CharField(max_length=128, blank=True, help_text='Legacy fallback when location FK is not set.')
    city = models.CharField(max_length=128, blank=True, null=True, help_text='Legacy fallback city.')
    wage_category = models.ForeignKey(
        WageCategory,
        on_delete=models.CASCADE,
        related_name='wage_rates',
    )
    role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='wage_rates',
        help_text='Role-specific override. Null applies to all roles in the category.',
    )
    monthly_wage = models.DecimalField(max_digits=10, decimal_places=2)
    daily_wage = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    source_note = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Minimum Wage Rate'
        verbose_name_plural = 'Minimum Wage Rates'
        indexes = [
            models.Index(fields=['org', 'is_active']),
            models.Index(fields=['location']),
            models.Index(fields=['state', 'city']),
            models.Index(fields=['wage_category']),
            models.Index(fields=['role']),
            models.Index(fields=['effective_from']),
        ]
        ordering = ['-effective_from']

    def __str__(self):
        if self.location:
            loc_str = str(self.location)
        elif self.city:
            loc_str = f"{self.state}/{self.city}"
        else:
            loc_str = self.state or '(global)'
        role_suffix = f" [{self.role}]" if self.role else ""
        return f"{self.wage_category} @ {loc_str}{role_suffix} from {self.effective_from}"

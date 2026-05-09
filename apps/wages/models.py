"""
apps/wages/models.py

WageCategory and MinimumWageRate.

Wage lookup order: role-specific override → city-specific → state-level fallback.
"""

from django.db import models
from apps.core.models import TimeStampedModel


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


class MinimumWageRate(TimeStampedModel):
    """
    State/city minimum wage rate for a wage category and date range.

    Lookup priority:
    1. role + city match (most specific)
    2. role + state match
    3. city match (no role)
    4. state match (no role, no city) — fallback
    """
    state = models.CharField(max_length=128)
    city = models.CharField(max_length=128, blank=True, null=True)
    wage_category = models.ForeignKey(
        WageCategory,
        on_delete=models.CASCADE,
        related_name='wage_rates',
    )
    role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wage_rates',
        help_text='Optional role-specific wage override. Null means applies to all roles in category.',
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
            models.Index(fields=['state', 'city']),
            models.Index(fields=['wage_category']),
            models.Index(fields=['role']),
            models.Index(fields=['effective_from']),
        ]
        ordering = ['-effective_from']

    def __str__(self):
        location = f"{self.state}/{self.city}" if self.city else self.state
        role_suffix = f" [{self.role}]" if self.role else ""
        return f"{self.wage_category} @ {location}{role_suffix} from {self.effective_from}"

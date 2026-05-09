"""
apps/jobs/models.py

Blue-collar job roles (NOT access control roles).
Examples: Housekeeping, Security Guard, Electrician, Plumber, Supervisor.
"""

from django.db import models
from apps.core.models import Organization, TimeStampedModel


class JobRole(TimeStampedModel):
    """A blue-collar job role within an organization."""

    SKILL_CATEGORY_CHOICES = [
        ('unskilled', 'Unskilled'),
        ('semi_skilled', 'Semi-Skilled'),
        ('skilled', 'Skilled'),
        ('highly_skilled', 'Highly Skilled'),
        ('supervisor', 'Supervisor'),
    ]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='job_roles')
    name = models.CharField(max_length=128)
    code = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    skill_category = models.CharField(max_length=32, choices=SKILL_CATEGORY_CHOICES)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Job Role'
        verbose_name_plural = 'Job Roles'
        unique_together = [['org', 'code']]
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.skill_category})"

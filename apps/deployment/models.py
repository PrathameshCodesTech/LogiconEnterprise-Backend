"""
apps/deployment/models.py

Employee and SiteDeployment.

Employee is the post-hire identity of a worker — distinct from User (login)
and Candidate (pre-hire). Employee.user is nullable because most blue-collar
workers will not have a system login.
Employee.candidate is nullable to allow direct onboarding without a hiring pipeline.
"""

import re

from django.conf import settings
from django.db import models
from apps.core.models import Organization, TimeStampedModel


def _normalize_phone(value: str) -> str:
    digits = re.sub(r'\D+', '', value or '')
    if len(digits) == 12 and digits.startswith('91'):
        return digits[-10:]
    if len(digits) == 10:
        return digits
    return digits[-15:]


class Employee(TimeStampedModel):
    """
    A hired/onboarded worker or staff member.

    Employee is NOT the same as User. User is the login identity for
    internal staff. Employee is the operational identity for deployed workers.
    employee.user is nullable — most field workers won't have a login.
    """

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
        ('exited', 'Exited'),
    ]

    org = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='employees',
    )
    candidate = models.ForeignKey(
        'talent.Candidate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employee_records',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employee_records',
    )
    employee_code = models.CharField(max_length=50)
    first_name = models.CharField(max_length=128)
    middle_name = models.CharField(max_length=128, blank=True)
    last_name = models.CharField(max_length=128)
    phone = models.CharField(max_length=20, blank=True)
    phone_normalized = models.CharField(max_length=15, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='active')
    joined_on = models.DateField(null=True, blank=True)
    exited_on = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = 'Employee'
        verbose_name_plural = 'Employees'
        ordering = ['last_name', 'first_name']
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'employee_code'],
                name='unique_employee_code_per_org',
            ),
            models.UniqueConstraint(
                fields=['org', 'phone_normalized'],
                condition=~models.Q(phone_normalized=''),
                name='unique_employee_phone_per_org',
            ),
            models.CheckConstraint(
                check=~models.Q(employee_code=''),
                name='employee_code_not_blank',
            ),
        ]
        indexes = [
            models.Index(fields=['org', 'status']),
            models.Index(fields=['job_role', 'status']),
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f"{self.full_name} [{self.employee_code}]"

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)

    def save(self, *args, **kwargs):
        self.phone_normalized = _normalize_phone(self.phone)
        super().save(*args, **kwargs)


class SiteDeployment(TimeStampedModel):
    """
    Maps an employee to a site for a specific job role over a date range.

    Linked back to the MRF line item and hiring application that triggered
    the deployment for full traceability. An employee can have multiple
    deployments (transfers, rotations), but only one should be active at a time.
    """

    STATUS_CHOICES = [
        ('planned', 'Planned'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('transferred', 'Transferred'),
        ('cancelled', 'Cancelled'),
    ]

    BILLING_TYPE_CHOICES = [
        ('billable', 'Billable'),
        ('non_billable', 'Non-Billable'),
    ]

    org = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='site_deployments',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='deployments',
    )
    site = models.ForeignKey(
        'sites.SiteProfile',
        on_delete=models.PROTECT,
        related_name='deployments',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.PROTECT,
        related_name='deployments',
    )
    mrf_line_item = models.ForeignKey(
        'mrf.MRFLineItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deployments',
    )
    hiring_application = models.ForeignKey(
        'hiring.HiringApplication',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deployments',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='planned')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    shift_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    billing_type = models.CharField(
        max_length=16,
        choices=BILLING_TYPE_CHOICES,
        default='billable',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_deployments',
    )

    class Meta:
        verbose_name = 'Site Deployment'
        verbose_name_plural = 'Site Deployments'
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['org', 'status']),
            models.Index(fields=['site', 'status']),
            models.Index(fields=['employee', 'status']),
            models.Index(fields=['job_role', 'status']),
        ]

    def __str__(self):
        return f"{self.employee} @ {self.site} ({self.job_role}) [{self.status}]"

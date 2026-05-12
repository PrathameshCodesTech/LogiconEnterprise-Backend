"""
apps/accounts/models.py

Custom User model for Logicon ATS.

Internal users are Logicon staff (HR, Admin, Sales, Finance, etc.).
Client users are future/optional — scoped to client/site.
Candidates must NOT use this model; they live in talent.Candidate.
"""

import re

from django.contrib.auth.models import AbstractUser
from django.db import models


def normalize_phone_number(value: str) -> str:
    """
    Store a compact phone key for lookup/uniqueness.
    Indian 10-digit mobiles are stored as 10 digits; longer values are capped
    to fit the field for future non-India support.
    """
    digits = re.sub(r'\D+', '', value or '')
    if len(digits) == 12 and digits.startswith('91'):
        return digits[-10:]
    if len(digits) == 10:
        return digits
    return digits[-15:]


class User(AbstractUser):
    USER_TYPE_CHOICES = [
        ('internal', 'Internal'),
        ('client', 'Client'),
        ('field', 'Field'),
    ]

    org = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
    )
    department = models.ForeignKey(
        'core.Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
    )
    phone_number = models.CharField(max_length=20, blank=True)
    phone_normalized = models.CharField(max_length=15, blank=True, db_index=True)
    employee_code = models.CharField(max_length=50, blank=True, db_index=True)
    user_type = models.CharField(max_length=16, choices=USER_TYPE_CHOICES, default='internal')
    is_invited = models.BooleanField(default=False)
    last_invited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        indexes = [
            models.Index(fields=['org', 'user_type', 'is_active']),
            models.Index(fields=['org', 'employee_code']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'phone_normalized'],
                condition=~models.Q(phone_normalized=''),
                name='unique_user_phone_per_org',
            ),
            models.UniqueConstraint(
                fields=['org', 'employee_code'],
                condition=~models.Q(employee_code=''),
                name='unique_user_employee_code_per_org',
            ),
        ]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.user_type})"

    def save(self, *args, **kwargs):
        self.phone_normalized = normalize_phone_number(self.phone_number)
        super().save(*args, **kwargs)

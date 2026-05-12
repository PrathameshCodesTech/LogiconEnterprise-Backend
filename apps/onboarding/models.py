"""
apps/onboarding/models.py

ClientOnboardingRequest — internal setup request when Sales onboards a new client or site.
"""

from django.conf import settings
from django.db import models

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


class ClientOnboardingRequest(TimeStampedModel):
    """
    An internal request to onboard a new client or expand an existing client.
    Created by Sales; reviewed through the client_onboarding workflow.
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE,
        related_name='client_onboarding_requests',
    )
    client = models.ForeignKey(
        'sites.Client', on_delete=models.PROTECT,
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
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Client Onboarding Request'
        verbose_name_plural = 'Client Onboarding Requests'
        ordering = ['-created_at']

    def __str__(self):
        return f"Onboarding #{self.pk}: {self.client} ({self.onboarding_type}) — {self.status}"

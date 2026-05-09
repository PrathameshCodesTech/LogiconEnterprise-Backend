"""
apps/talent/models.py — skeleton

Candidate, Resume, CandidateSkill.
"""

from django.db import models
from apps.core.models import Organization, TimeStampedModel


class Candidate(TimeStampedModel):
    """A job candidate / worker profile."""

    SOURCE_CHOICES = [
        ('qr', 'QR Code'),
        ('portal', 'Portal'),
        ('manual', 'Manual'),
        ('referral', 'Referral'),
        ('import_', 'Import'),
    ]

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='candidates')
    phone = models.CharField(max_length=20)
    phone_normalized = models.CharField(max_length=20)
    first_name = models.CharField(max_length=128)
    middle_name = models.CharField(max_length=128, blank=True)
    last_name = models.CharField(max_length=128)
    email = models.EmailField(blank=True)
    current_location = models.CharField(max_length=255, blank=True)
    total_experience_years = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    current_ctc = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_ctc = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default='manual')
    is_blacklisted = models.BooleanField(default=False)
    blacklist_reason = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Candidate'
        verbose_name_plural = 'Candidates'
        unique_together = [['org', 'phone_normalized']]
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.phone})"

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)


class Resume(models.Model):
    """An uploaded resume / CV for a candidate."""

    PARSED_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('parsed', 'Parsed'),
        ('failed', 'Failed'),
    ]

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='resumes')
    file = models.FileField(upload_to='resumes/%Y/%m/')
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    parsed_status = models.CharField(max_length=16, choices=PARSED_STATUS_CHOICES, default='pending')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    view_only_note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Resume'
        verbose_name_plural = 'Resumes'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"Resume of {self.candidate} ({self.parsed_status})"


class CandidateSkill(models.Model):
    """A skill associated with a candidate (can be parsed or manually entered)."""
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='skills')
    skill_name = models.CharField(max_length=128)
    normalized_skill_name = models.CharField(max_length=128, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = 'Candidate Skill'
        verbose_name_plural = 'Candidate Skills'

    def __str__(self):
        return f"{self.candidate} — {self.skill_name}"

"""
apps/intake/models.py

QRCampaign, CampaignJobRole, FormField, IntakeSubmission,
IntakeSubmissionAnswer, IntakeDocument.
FormTemplate, FormSection, FormTemplateField for reusable form templates.
"""

import secrets
from django.db import models
from apps.core.models import Organization, TimeStampedModel
from .constants import SUPPORTED_LANGUAGES


def _default_enabled_languages():
    return ['en']


FIELD_TYPE_CHOICES = [
    ('text', 'Text'),
    ('textarea', 'Textarea'),
    ('number', 'Number'),
    ('date', 'Date'),
    ('email', 'Email'),
    ('select', 'Select'),
    ('multi_select', 'Multi Select'),
    ('boolean', 'Boolean'),
    ('file', 'File'),
]

FIELD_SOURCE_CHOICES = [
    ('campaign', 'Campaign'),
    ('template', 'Template'),
]


class FormTemplate(TimeStampedModel):
    """A reusable form template that can be attached to QR campaigns."""
    org = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='form_templates',
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, blank=True, null=True, default=None)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Form Template'
        verbose_name_plural = 'Form Templates'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'code'],
                condition=models.Q(code__isnull=False),
                name='uq_form_template_org_code',
            )
        ]

    def __str__(self):
        return self.name


class FormSection(models.Model):
    """A section within a form template, grouping related fields."""
    template = models.ForeignKey(
        FormTemplate, on_delete=models.CASCADE, related_name='sections',
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, blank=True, default='')
    description = models.TextField(blank=True, default='')
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Form Section'
        verbose_name_plural = 'Form Sections'
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['template', 'code'],
                condition=models.Q(code__gt=''),
                name='uq_form_section_template_code',
            )
        ]

    def __str__(self):
        return f"{self.template.name} / {self.name}"


class FormTemplateField(models.Model):
    """A dynamic form field belonging to a form template section."""
    section = models.ForeignKey(
        FormSection, on_delete=models.CASCADE, related_name='template_fields',
    )
    role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='template_fields',
    )
    label = models.CharField(max_length=200)
    field_key = models.CharField(max_length=100)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default='text')
    help_text = models.TextField(blank=True)
    placeholder = models.CharField(max_length=200, blank=True)
    options = models.JSONField(default=list)
    is_required = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    min_length = models.IntegerField(null=True, blank=True)
    max_length = models.IntegerField(null=True, blank=True)
    min_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    translations = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Form Template Field'
        verbose_name_plural = 'Form Template Fields'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.label} ({self.section.template.name})"


class QRCampaign(TimeStampedModel):
    """A QR-code based recruitment campaign."""
    org = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='qr_campaigns',
    )
    site = models.ForeignKey(
        'sites.SiteProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='qr_campaigns',
    )
    form_template = models.ForeignKey(
        FormTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='campaigns',
    )
    name = models.CharField(max_length=255)
    title = models.CharField(max_length=255, blank=True)
    code = models.CharField(max_length=64)
    token = models.CharField(max_length=64, unique=True, blank=True)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    allow_duplicates = models.BooleanField(default=True)
    requires_otp = models.BooleanField(default=False)
    shuffle_fields = models.BooleanField(default=True)
    default_language = models.CharField(
        max_length=10, choices=SUPPORTED_LANGUAGES, default='en',
    )
    enabled_languages = models.JSONField(default=_default_enabled_languages)

    class Meta:
        verbose_name = 'QR Campaign'
        verbose_name_plural = 'QR Campaigns'
        unique_together = [['org', 'code']]
        ordering = ['-created_at']

    def __str__(self):
        return self.title or self.name

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(16)
        super().save(*args, **kwargs)


class CampaignJobRole(models.Model):
    """Job roles associated with a campaign."""
    campaign = models.ForeignKey(
        QRCampaign, on_delete=models.CASCADE, related_name='campaign_job_roles',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole', on_delete=models.CASCADE, related_name='campaign_job_roles',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Campaign Job Role'
        verbose_name_plural = 'Campaign Job Roles'
        unique_together = [['campaign', 'job_role']]

    def __str__(self):
        return f"{self.campaign} - {self.job_role}"


class FormField(models.Model):
    """A dynamic form field for a campaign intake form."""

    FIELD_TYPE_CHOICES = FIELD_TYPE_CHOICES

    campaign = models.ForeignKey(
        QRCampaign, on_delete=models.CASCADE, related_name='form_fields',
    )
    role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='form_fields',
    )
    label = models.CharField(max_length=200)
    field_key = models.CharField(max_length=100)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default='text')
    help_text = models.TextField(blank=True)
    placeholder = models.CharField(max_length=200, blank=True)
    options = models.JSONField(default=list)
    is_required = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    min_length = models.IntegerField(null=True, blank=True)
    max_length = models.IntegerField(null=True, blank=True)
    min_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    translations = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Form Field'
        verbose_name_plural = 'Form Fields'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.label} ({self.campaign})"


class IntakeSubmission(models.Model):
    """A candidate's submission through a QR campaign."""

    STATUS_CHOICES = [
        ('new', 'New'),
        ('reviewed', 'Reviewed'),
        ('shortlisted', 'Shortlisted'),
        ('rejected', 'Rejected'),
        ('contacted', 'Contacted'),
        ('hired', 'Hired'),
        ('duplicate', 'Duplicate'),
    ]

    campaign = models.ForeignKey(
        QRCampaign, on_delete=models.CASCADE, related_name='submissions',
    )
    site = models.ForeignKey(
        'sites.SiteProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='intake_submissions',
    )
    candidate = models.ForeignKey(
        'talent.Candidate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='intake_submissions',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='intake_submissions',
    )
    first_name = models.CharField(max_length=128, blank=True)
    middle_name = models.CharField(max_length=128, blank=True)
    last_name = models.CharField(max_length=128, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    other_role_title = models.CharField(max_length=200, blank=True)
    other_role_title_normalized = models.CharField(max_length=200, blank=True)
    mobile_number = models.CharField(max_length=20, blank=True)
    mobile_number_normalized = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    language = models.CharField(max_length=10, choices=SUPPORTED_LANGUAGES, default='en')
    is_possible_duplicate = models.BooleanField(default=False)
    duplicate_reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Intake Submission'
        verbose_name_plural = 'Intake Submissions'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['mobile_number_normalized']),
            models.Index(fields=['status']),
            models.Index(fields=['campaign', 'mobile_number_normalized']),
            models.Index(fields=['campaign', 'mobile_number_normalized', 'job_role']),
            models.Index(fields=['candidate', 'campaign', 'submitted_at']),
        ]

    def __str__(self):
        return f"Submission #{self.pk} - {self.full_name or self.mobile_number_normalized}"


class IntakeSubmissionAnswer(models.Model):
    """An answer to a specific form field in an intake submission."""
    submission = models.ForeignKey(
        IntakeSubmission, on_delete=models.CASCADE, related_name='answers',
    )
    field = models.ForeignKey(
        FormField,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='answers',
    )
    template_field = models.ForeignKey(
        FormTemplateField,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='answers',
    )
    field_source = models.CharField(
        max_length=10, choices=FIELD_SOURCE_CHOICES, default='campaign',
    )
    field_label_snapshot = models.CharField(max_length=200, blank=True)
    field_type_snapshot = models.CharField(max_length=20, blank=True)
    value = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Intake Submission Answer'
        verbose_name_plural = 'Intake Submission Answers'

    def __str__(self):
        return f"Answer to {self.field_label_snapshot}"


class IntakeDocument(models.Model):
    """A document uploaded as part of an intake submission."""

    DOCUMENT_TYPE_CHOICES = [
        ('resume', 'Resume'),
        ('id_proof', 'ID Proof'),
        ('certificate', 'Certificate'),
        ('other', 'Other'),
    ]

    submission = models.ForeignKey(
        IntakeSubmission, on_delete=models.CASCADE, related_name='documents',
    )
    field = models.ForeignKey(
        FormField,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
    )
    template_field = models.ForeignKey(
        FormTemplateField,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
    )
    field_source = models.CharField(
        max_length=10, choices=FIELD_SOURCE_CHOICES, default='campaign',
    )
    document_type = models.CharField(
        max_length=20, choices=DOCUMENT_TYPE_CHOICES, default='other',
    )
    file = models.FileField(upload_to='intake-documents/%Y/%m/')
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Intake Document'
        verbose_name_plural = 'Intake Documents'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"Document for submission #{self.submission_id} ({self.document_type})"

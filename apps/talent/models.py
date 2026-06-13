"""
apps/talent/models.py

Candidate, Resume, CandidateSkill, ParsedResume,
CandidateExperience, CandidateEducation.
"""

from django.conf import settings
from django.db import models
from apps.core.models import Organization, TimeStampedModel


# ─── Choices ──────────────────────────────────────────────────────────────────

LIFECYCLE_STATUS_CHOICES = [
    ('active', 'Active'),
    ('inactive', 'Inactive'),
    ('blacklisted', 'Blacklisted'),
    ('duplicate', 'Duplicate'),
    ('employee_converted', 'Employee Converted'),
    ('do_not_contact', 'Do Not Contact'),
]

AVAILABILITY_STATUS_CHOICES = [
    ('available_now', 'Available Now'),
    ('available_from_date', 'Available From Date'),
    ('currently_deployed', 'Currently Deployed'),
    ('notice_period', 'Notice Period'),
    ('not_available', 'Not Available'),
    ('unknown', 'Unknown'),
]

RESUME_STATUS_CHOICES = [
    ('uploaded', 'Uploaded'),
    ('extracting', 'Extracting'),
    ('ocr_required', 'OCR Required'),
    ('parsing', 'Parsing'),
    ('validating', 'Validating'),
    ('indexed', 'Indexed'),
    ('manual_review', 'Manual Review'),
    ('failed', 'Failed'),
    ('duplicate_file', 'Duplicate File'),
]

RESUME_IMPORT_BATCH_STATUS_CHOICES = [
    ('queued', 'Queued'),
    ('processing', 'Processing'),
    ('completed', 'Completed'),
    ('completed_with_errors', 'Completed With Errors'),
    ('failed', 'Failed'),
]

RESUME_IMPORT_ITEM_STATUS_CHOICES = [
    ('queued', 'Queued'),
    ('processing', 'Processing'),
    ('indexed', 'Indexed'),
    ('duplicate_file', 'Duplicate File'),
    ('manual_review', 'Manual Review'),
    ('failed', 'Failed'),
]

RESUME_SOURCE_TYPE_CHOICES = [
    ('qr_intake', 'QR Intake'),
    ('manual_upload', 'Manual Upload'),
    ('recruiter_upload', 'Recruiter Upload'),
    ('portal', 'Portal'),
    ('referral', 'Referral'),
    ('import_', 'Import'),
    ('bulk_upload', 'Bulk Upload'),
    ('excel_import', 'Excel Import'),
    ('campaign', 'Campaign'),
]

DOCUMENT_TYPE_CHOICES = [
    ('pdf', 'PDF'),
    ('docx', 'DOCX'),
    ('doc', 'DOC'),
    ('txt', 'Text'),
    ('xlsx', 'Excel'),
    ('csv', 'CSV'),
    ('unknown', 'Unknown'),
]

TARGET_ROLE_SOURCE_CHOICES = [
    ('manual', 'Manual'),
    ('bulk_upload', 'Bulk Upload'),
    ('excel_import', 'Excel Import'),
    ('campaign', 'Campaign'),
    ('qr_intake', 'QR Intake'),
]

SKILL_PROFICIENCY_CHOICES = [
    ('beginner', 'Beginner'),
    ('intermediate', 'Intermediate'),
    ('advanced', 'Advanced'),
    ('expert', 'Expert'),
]


# ─── Candidate ────────────────────────────────────────────────────────────────

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

    # ── Phase Talent-Hiring-A additions ───────────────────────────────────────
    lifecycle_status = models.CharField(
        max_length=24, choices=LIFECYCLE_STATUS_CHOICES, default='active',
    )
    availability_status = models.CharField(
        max_length=24, choices=AVAILABILITY_STATUS_CHOICES, default='unknown',
    )
    preferred_location = models.CharField(max_length=255, blank=True)
    notice_period_days = models.PositiveIntegerField(null=True, blank=True)
    current_company = models.CharField(max_length=255, blank=True)
    current_role = models.CharField(max_length=255, blank=True)
    target_job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='targeted_candidates',
    )
    source_reference = models.CharField(max_length=255, blank=True)
    duplicate_of = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='duplicates',
    )
    is_duplicate = models.BooleanField(default=False)
    do_not_contact = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Candidate'
        verbose_name_plural = 'Candidates'
        unique_together = [['org', 'phone_normalized']]
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['org', 'lifecycle_status']),
            models.Index(fields=['org', 'availability_status']),
            models.Index(fields=['org', 'target_job_role']),
            models.Index(fields=['email']),
            models.Index(fields=['phone_normalized']),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.phone})"

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)


# ─── Resume ───────────────────────────────────────────────────────────────────

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
    # Legacy field — kept for backward compatibility; new code uses `status`
    parsed_status = models.CharField(
        max_length=16, choices=PARSED_STATUS_CHOICES, default='pending',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    view_only_note = models.CharField(max_length=255, blank=True)

    # ── Phase Talent-Hiring-A additions ───────────────────────────────────────
    status = models.CharField(
        max_length=20, choices=RESUME_STATUS_CHOICES, default='uploaded',
    )
    file_hash = models.CharField(max_length=64, blank=True)
    document_type = models.CharField(
        max_length=16, choices=DOCUMENT_TYPE_CHOICES, default='unknown',
    )
    raw_text = models.TextField(blank=True)
    cleaned_text = models.TextField(blank=True)
    extraction_engine = models.CharField(max_length=64, blank=True)
    ocr_used = models.BooleanField(default=False)
    extraction_confidence = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    parser_engine = models.CharField(max_length=64, blank=True)
    parser_confidence = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    error_message = models.TextField(blank=True)
    manual_review_reason = models.TextField(blank=True)
    source_type = models.CharField(
        max_length=20, choices=RESUME_SOURCE_TYPE_CHOICES, default='manual_upload',
    )
    target_job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='targeted_resumes',
    )
    target_role_source = models.CharField(
        max_length=20,
        choices=TARGET_ROLE_SOURCE_CHOICES,
        blank=True,
        default='',
    )
    import_batch_id = models.CharField(max_length=64, blank=True)
    source_intake_document = models.ForeignKey(
        'intake.IntakeDocument', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='resumes',
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='uploaded_resumes',
    )

    class Meta:
        verbose_name = 'Resume'
        verbose_name_plural = 'Resumes'
        ordering = ['-uploaded_at']
        constraints = [
            models.UniqueConstraint(
                fields=['source_intake_document'],
                condition=models.Q(source_intake_document__isnull=False),
                name='unique_resume_per_intake_document',
            )
        ]
        indexes = [
            models.Index(fields=['file_hash']),
            models.Index(fields=['document_type']),
            models.Index(fields=['candidate', 'status']),
            models.Index(fields=['target_job_role', 'status']),
            models.Index(fields=['source_type', 'target_job_role']),
            models.Index(fields=['import_batch_id']),
            models.Index(fields=['source_intake_document']),
        ]

    def __str__(self):
        return f"Resume of {self.candidate} ({self.status})"


class ResumeImportBatch(TimeStampedModel):
    """A bulk resume upload batch processed asynchronously."""

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='resume_import_batches')
    target_job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resume_import_batches',
    )
    source_type = models.CharField(
        max_length=20, choices=RESUME_SOURCE_TYPE_CHOICES, default='bulk_upload',
    )
    status = models.CharField(
        max_length=32,
        choices=RESUME_IMPORT_BATCH_STATUS_CHOICES,
        default='queued',
    )
    total_count = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    manual_review_count = models.PositiveIntegerField(default=0)
    view_only_note = models.CharField(max_length=255, blank=True)
    import_file = models.FileField(upload_to='candidate_imports/%Y/%m/', null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    document_type = models.CharField(
        max_length=16, choices=DOCUMENT_TYPE_CHOICES, default='unknown',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resume_import_batches',
    )

    class Meta:
        verbose_name = 'Resume Import Batch'
        verbose_name_plural = 'Resume Import Batches'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['org', 'status']),
            models.Index(fields=['org', 'target_job_role']),
            models.Index(fields=['org', 'document_type']),
            models.Index(fields=['created_by', 'status']),
        ]

    def __str__(self):
        return f"Resume import batch #{self.pk} ({self.status})"


class ResumeImportItem(models.Model):
    """One file within a bulk resume import batch."""

    batch = models.ForeignKey(
        ResumeImportBatch,
        on_delete=models.CASCADE,
        related_name='items',
    )
    file = models.FileField(upload_to='resume_imports/%Y/%m/', null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    file_hash = models.CharField(max_length=64, blank=True)
    document_type = models.CharField(
        max_length=16, choices=DOCUMENT_TYPE_CHOICES, default='unknown',
    )
    row_number = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=32,
        choices=RESUME_IMPORT_ITEM_STATUS_CHOICES,
        default='queued',
    )
    error_message = models.TextField(blank=True)
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resume_import_items',
    )
    resume = models.ForeignKey(
        Resume,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='import_items',
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Resume Import Item'
        verbose_name_plural = 'Resume Import Items'
        ordering = ['id']
        indexes = [
            models.Index(fields=['batch', 'status']),
            models.Index(fields=['batch', 'document_type']),
            models.Index(fields=['file_hash']),
            models.Index(fields=['candidate']),
            models.Index(fields=['resume']),
        ]

    def __str__(self):
        return f"{self.original_filename or self.pk} ({self.status})"


# ─── CandidateSkill ───────────────────────────────────────────────────────────

class CandidateSkill(models.Model):
    """A skill associated with a candidate (parsed or manually entered)."""
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='skills')
    skill_name = models.CharField(max_length=128)
    normalized_skill_name = models.CharField(max_length=128, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # ── Phase Talent-Hiring-A additions ───────────────────────────────────────
    years_experience = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
    )
    proficiency = models.CharField(
        max_length=20, choices=SKILL_PROFICIENCY_CHOICES, blank=True,
    )
    source = models.CharField(max_length=32, blank=True)
    source_resume = models.ForeignKey(
        Resume, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='skills',
    )

    class Meta:
        verbose_name = 'Candidate Skill'
        verbose_name_plural = 'Candidate Skills'

    def __str__(self):
        return f"{self.candidate} — {self.skill_name}"


# ─── ParsedResume ─────────────────────────────────────────────────────────────

class ParsedResume(models.Model):
    """Structured parser output for a resume. One-to-one with Resume."""
    resume = models.OneToOneField(
        Resume, on_delete=models.CASCADE, related_name='parsed_resume',
    )
    parsed_json = models.JSONField(default=dict, blank=True)
    normalized_json = models.JSONField(default=dict, blank=True)
    summary = models.TextField(blank=True)
    career_level = models.CharField(max_length=64, blank=True)
    primary_domain = models.CharField(max_length=128, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    missing_fields = models.JSONField(default=list, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Parsed Resume'
        verbose_name_plural = 'Parsed Resumes'

    def __str__(self):
        return f"ParsedResume for Resume #{self.resume_id}"


# ─── CandidateExperience ──────────────────────────────────────────────────────

class CandidateExperience(models.Model):
    """Work experience entry for a candidate."""
    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name='experiences',
    )
    job_title = models.CharField(max_length=255, blank=True)
    normalized_title = models.CharField(max_length=255, blank=True)
    company_name = models.CharField(max_length=255, blank=True)
    industry = models.CharField(max_length=128, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    duration_months = models.IntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    responsibilities = models.JSONField(default=list, blank=True)
    source_resume = models.ForeignKey(
        Resume, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='experiences',
    )
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Candidate Experience'
        verbose_name_plural = 'Candidate Experiences'
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.candidate} — {self.job_title} @ {self.company_name}"


# ─── CandidateEducation ───────────────────────────────────────────────────────

class CandidateEducation(models.Model):
    """Education entry for a candidate."""
    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name='educations',
    )
    degree = models.CharField(max_length=255, blank=True)
    normalized_degree = models.CharField(max_length=255, blank=True)
    specialization = models.CharField(max_length=255, blank=True)
    institute = models.CharField(max_length=255, blank=True)
    start_year = models.SmallIntegerField(null=True, blank=True)
    end_year = models.SmallIntegerField(null=True, blank=True)
    source_resume = models.ForeignKey(
        Resume, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='educations',
    )
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Candidate Education'
        verbose_name_plural = 'Candidate Educations'
        ordering = ['-end_year']

    def __str__(self):
        return f"{self.candidate} — {self.degree} ({self.institute})"


# ─── TalentResumeReview ───────────────────────────────────────────────────────

class TalentResumeReview(models.Model):
    """Audit record for every HR review action on a resume."""

    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='resume_reviews',
    )
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name='reviews')
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='resume_reviews')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='resume_reviews',
    )
    review_type = models.CharField(max_length=32, default='correction')
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)
    review_note = models.TextField(blank=True)
    correction_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Talent Resume Review'
        verbose_name_plural = 'Talent Resume Reviews'
        ordering = ['-created_at']

    def __str__(self):
        return f"Review #{self.pk} of Resume #{self.resume_id} ({self.review_type})"

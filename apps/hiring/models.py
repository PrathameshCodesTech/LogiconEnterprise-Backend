"""
apps/hiring/models.py

Hiring pipeline: HiringApplication, Interview, InterviewFeedback, Offer.

HiringApplication links a Candidate (from talent/intake) to an approved
MRF line item and tracks it through the full hiring lifecycle.
Candidate is NOT a login user — Employee is the post-hire identity.
"""

from django.conf import settings
from django.db import models
from apps.core.models import Organization, TimeStampedModel


class HiringApplication(TimeStampedModel):
    """
    Links a candidate to a specific MRF line item and tracks hiring status.

    One candidate cannot be added twice to the same MRF line item.
    The same candidate can appear in different line items (different roles/MRFs).
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('shortlisted', 'Shortlisted'),
        ('client_review', 'Client Review'),
        ('interview_scheduled', 'Interview Scheduled'),
        ('interview_in_progress', 'Interview In Progress'),
        ('selected', 'Selected'),
        ('rejected', 'Rejected'),
        ('offer_released', 'Offer Released'),
        ('offer_accepted', 'Offer Accepted'),
        ('offer_declined', 'Offer Declined'),
        ('deployed', 'Deployed'),
        ('cancelled', 'Cancelled'),
    ]

    CLIENT_DECISION_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    org = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='hiring_applications',
    )
    candidate = models.ForeignKey(
        'talent.Candidate',
        on_delete=models.PROTECT,
        related_name='hiring_applications',
    )
    mrf = models.ForeignKey(
        'mrf.ManpowerRequest',
        on_delete=models.PROTECT,
        related_name='hiring_applications',
    )
    mrf_line_item = models.ForeignKey(
        'mrf.MRFLineItem',
        on_delete=models.PROTECT,
        related_name='hiring_applications',
    )
    site = models.ForeignKey(
        'sites.SiteProfile',
        on_delete=models.PROTECT,
        related_name='hiring_applications',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.PROTECT,
        related_name='hiring_applications',
    )
    source_intake_submission = models.ForeignKey(
        'intake.IntakeSubmission',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hiring_applications',
    )
    match_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Optional AI/manual match score out of 100.',
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='draft')

    shortlisted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='shortlisted_applications',
    )
    shortlisted_at = models.DateTimeField(null=True, blank=True)

    client_visible = models.BooleanField(default=False)
    client_decision = models.CharField(
        max_length=16,
        choices=CLIENT_DECISION_CHOICES,
        null=True,
        blank=True,
    )
    client_decision_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='client_decisions',
    )
    client_decision_at = models.DateTimeField(null=True, blank=True)
    client_decision_note = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Hiring Application'
        verbose_name_plural = 'Hiring Applications'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['candidate', 'mrf_line_item'],
                name='unique_candidate_per_mrf_line_item',
            )
        ]
        indexes = [
            models.Index(fields=['org', 'status']),
            models.Index(fields=['site', 'status']),
            models.Index(fields=['candidate', 'status']),
            models.Index(fields=['mrf_line_item', 'status']),
        ]

    def __str__(self):
        return f"Application #{self.pk} — {self.candidate} → {self.job_role} ({self.status})"


class Interview(TimeStampedModel):
    """A scheduled interview round for a hiring application."""

    ROUND_TYPE_CHOICES = [
        ('hr', 'HR'),
        ('technical', 'Technical'),
        ('manager', 'Manager'),
        ('client', 'Client'),
        ('final', 'Final'),
    ]

    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('no_show', 'No Show'),
        ('rescheduled', 'Rescheduled'),
    ]

    MODE_CHOICES = [
        ('phone', 'Phone'),
        ('video', 'Video'),
        ('in_person', 'In Person'),
    ]

    hiring_application = models.ForeignKey(
        HiringApplication,
        on_delete=models.CASCADE,
        related_name='interviews',
    )
    round_type = models.CharField(max_length=16, choices=ROUND_TYPE_CHOICES)
    round_number = models.PositiveIntegerField(default=1)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduled_interviews',
    )
    interviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_interviews',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='scheduled')
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default='in_person')
    location = models.TextField(blank=True)
    meeting_link = models.URLField(blank=True)

    class Meta:
        verbose_name = 'Interview'
        verbose_name_plural = 'Interviews'
        ordering = ['-scheduled_at']
        indexes = [
            models.Index(fields=['hiring_application', 'round_number']),
            models.Index(fields=['status', 'scheduled_at']),
        ]

    def __str__(self):
        return (
            f"Interview #{self.pk} — {self.hiring_application} "
            f"Round {self.round_number} ({self.round_type}) [{self.status}]"
        )


class InterviewFeedback(TimeStampedModel):
    """Feedback submitted by an interviewer after a completed interview round."""

    RECOMMENDATION_CHOICES = [
        ('proceed', 'Proceed'),
        ('hold', 'Hold'),
        ('reject', 'Reject'),
    ]

    interview = models.ForeignKey(
        Interview,
        on_delete=models.CASCADE,
        related_name='feedbacks',
    )
    given_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interview_feedbacks',
    )
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    recommendation = models.CharField(max_length=16, choices=RECOMMENDATION_CHOICES)

    class Meta:
        verbose_name = 'Interview Feedback'
        verbose_name_plural = 'Interview Feedbacks'
        constraints = [
            models.UniqueConstraint(
                fields=['interview', 'given_by'],
                condition=models.Q(given_by__isnull=False),
                name='unique_feedback_per_interviewer',
            )
        ]

    def __str__(self):
        return f"Feedback for Interview #{self.interview_id} by {self.given_by} ({self.recommendation})"


class Offer(TimeStampedModel):
    """A formal offer letter linked to a hiring application."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('released', 'Released'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('withdrawn', 'Withdrawn'),
        ('expired', 'Expired'),
    ]

    hiring_application = models.OneToOneField(
        HiringApplication,
        on_delete=models.CASCADE,
        related_name='offer',
    )
    offered_ctc = models.DecimalField(max_digits=12, decimal_places=2)
    salary_breakup = models.JSONField(default=dict, blank=True)
    joining_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft')
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='released_offers',
    )
    released_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Offer'
        verbose_name_plural = 'Offers'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['joining_date']),
        ]

    def __str__(self):
        return f"Offer #{self.pk} — {self.hiring_application} ({self.status})"

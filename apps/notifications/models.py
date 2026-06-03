from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    """Persistent in-app notification for a single recipient."""

    TYPE_CHOICES = [
        ('workflow_task_assigned', 'Workflow Task Assigned'),
        ('workflow_completed', 'Workflow Completed'),
        ('sales_survey_assigned', 'Sales Survey Assigned'),
        ('sales_survey_completed', 'Sales Survey Completed'),
        ('mobilisation_operations_assigned', 'Mobilisation Operations Assigned'),
        ('mobilisation_setup_completed', 'Mobilisation Setup Completed'),
        ('system', 'System'),
    ]

    org = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='triggered_notifications',
    )
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    notification_type = models.CharField(
        max_length=64,
        choices=TYPE_CHOICES,
        default='system',
    )
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    target_url = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(fields=['org', '-created_at']),
            models.Index(fields=['target_type', 'target_id']),
        ]

    def __str__(self):
        return f'{self.recipient_id}: {self.title}'

    def mark_read(self):
        if self.is_read:
            return
        self.is_read = True
        self.read_at = timezone.now()
        self.save(update_fields=['is_read', 'read_at', 'updated_at'])


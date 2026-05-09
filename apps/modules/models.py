"""
apps/modules/models.py

Module activation per scope node.
"""

from django.db import models
from apps.core.models import ScopeNode, TimeStampedModel


MODULE_TYPE_CHOICES = [
    ('intake', 'Intake'),
    ('talent', 'Talent'),
    ('sites', 'Sites'),
    ('wages', 'Wages'),
    ('mrf', 'MRF'),
    ('workflow', 'Workflow'),
    ('hiring', 'Hiring'),
    ('deployment', 'Deployment'),
    ('client_portal', 'Client Portal'),
    ('field_sense', 'Field Sense'),
    ('reports', 'Reports'),
]


class ModuleActivation(TimeStampedModel):
    """Toggles a module on/off for a specific scope node."""
    module = models.CharField(max_length=32, choices=MODULE_TYPE_CHOICES)
    scope_node = models.ForeignKey(
        ScopeNode,
        on_delete=models.CASCADE,
        related_name='module_activations',
    )
    is_active = models.BooleanField(default=True)
    override_parent = models.BooleanField(
        default=False,
        help_text='If True, overrides parent scope node activation setting.',
    )

    class Meta:
        verbose_name = 'Module Activation'
        verbose_name_plural = 'Module Activations'
        unique_together = [['module', 'scope_node']]
        indexes = [
            models.Index(fields=['scope_node', 'module']),
        ]
        ordering = ['module']

    def __str__(self):
        status = 'ON' if self.is_active else 'OFF'
        return f"{self.module} @ {self.scope_node} [{status}]"

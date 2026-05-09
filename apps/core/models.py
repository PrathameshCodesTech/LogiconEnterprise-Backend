"""
apps/core/models.py

Foundation models:
  - TimeStampedModel (abstract)
  - SoftDeleteModel (abstract)
  - Organization
  - ScopeNode
"""

from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base providing created_at / updated_at timestamps."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Abstract base providing is_active soft-delete flag."""
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True


class Organization(TimeStampedModel, SoftDeleteModel):
    """Top-level tenant / organization."""
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64, unique=True)

    class Meta:
        verbose_name = 'Organization'
        verbose_name_plural = 'Organizations'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class ScopeNode(TimeStampedModel):
    """
    Generic hierarchy node.
    Represents: Organization → Client → Region → City → Site → Department → Cost Center
    The path field is a slash-separated string of ancestor codes, e.g. "logicon/west/mumbai".
    """

    NODE_TYPE_CHOICES = [
        ('company', 'Company'),
        ('client', 'Client'),
        ('region', 'Region'),
        ('city', 'City'),
        ('site', 'Site'),
        ('department', 'Department'),
        ('cost_center', 'Cost Center'),
    ]

    org = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name='scope_nodes',
    )
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='children',
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    node_type = models.CharField(max_length=32, choices=NODE_TYPE_CHOICES)
    path = models.CharField(max_length=1024, blank=True, db_index=True)
    depth = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Scope Node'
        verbose_name_plural = 'Scope Nodes'
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'code'],
                condition=models.Q(parent__isnull=True),
                name='unique_root_scope_code_per_org',
            ),
            models.UniqueConstraint(
                fields=['org', 'parent', 'code'],
                condition=models.Q(parent__isnull=False),
                name='unique_child_scope_code_per_parent',
            ),
            models.UniqueConstraint(
                fields=['org', 'path'],
                condition=~models.Q(path=''),
                name='unique_scope_path_per_org',
            ),
        ]
        indexes = [
            models.Index(fields=['path']),
            models.Index(fields=['org', 'node_type']),
            models.Index(fields=['org', 'is_active']),
        ]
        ordering = ['depth', 'name']

    def __str__(self):
        return f"{self.path} ({self.node_type})"

    def get_ancestors_from_path(self):
        """Returns cumulative ancestor paths parsed from self.path, excluding self."""
        if not self.path:
            return []
        parts = self.path.split('/')
        return ['/'.join(parts[:idx]) for idx in range(1, len(parts))]

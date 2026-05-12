"""
apps/core/models.py

Foundation models:
  - TimeStampedModel (abstract)
  - SoftDeleteModel (abstract)
  - Organization
  - ScopeNode
  - Department
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


class Department(TimeStampedModel):
    """
    Business/team grouping for users and workflow routing.

    Three scopes (most specific wins):
      org-level   — client null, site null   (e.g. HR, Finance, Sales)
      client-level — client set, site null   (e.g. Client Operations @ ABC Infra)
      site-level  — site set                 (e.g. Site Operations @ ABC Site 1)

    When site is set, client is auto-normalised to site.client on save.
    Uniqueness is enforced only among active rows (inactive rows do not block replacements).
    """
    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='departments',
    )
    client = models.ForeignKey(
        'sites.Client', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='departments',
    )
    site = models.ForeignKey(
        'sites.SiteProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='departments',
    )
    name = models.CharField(max_length=128)
    code = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'
        ordering = ['org', 'name']
        constraints = [
            # org-level: unique active code with no client/site
            models.UniqueConstraint(
                fields=['org', 'code'],
                condition=models.Q(client__isnull=True, site__isnull=True, is_active=True),
                name='unique_dept_org_code',
            ),
            # client-level: unique active code per client (no site)
            models.UniqueConstraint(
                fields=['org', 'client', 'code'],
                condition=models.Q(client__isnull=False, site__isnull=True, is_active=True),
                name='unique_dept_client_code',
            ),
            # site-level: unique active code per site
            models.UniqueConstraint(
                fields=['org', 'site', 'code'],
                condition=models.Q(site__isnull=False, is_active=True),
                name='unique_dept_site_code',
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}

        # Auto-fill and validate client from site
        if self.site_id:
            from apps.sites.models import SiteProfile
            try:
                site_obj = SiteProfile.objects.select_related('client').get(pk=self.site_id)
            except SiteProfile.DoesNotExist:
                site_obj = None

            if site_obj is not None:
                if site_obj.org_id != self.org_id:
                    errors['site'] = 'Site must belong to the same organization.'
                else:
                    if not self.client_id:
                        self.client_id = site_obj.client_id
                    elif self.client_id != site_obj.client_id:
                        errors['client'] = (
                            f'Client must match the site\'s client '
                            f'(expected client id {site_obj.client_id}).'
                        )

        # Validate client org consistency
        if self.client_id and not errors.get('client'):
            from apps.sites.models import Client
            try:
                client_obj = Client.objects.get(pk=self.client_id)
                if client_obj.org_id != self.org_id:
                    errors['client'] = 'Client must belong to the same organization.'
            except Client.DoesNotExist:
                pass

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        if self.site_id:
            return f"{self.name} (site:{self.site_id})"
        if self.client_id:
            return f"{self.name} (client:{self.client_id})"
        return f"{self.name} ({self.org})"

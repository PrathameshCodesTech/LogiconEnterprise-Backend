"""
apps/access/models.py

Access control: roles, permissions, scope assignments, role assignments.
"""

from django.conf import settings
from django.db import models

from apps.core.models import Organization, ScopeNode, TimeStampedModel


PERMISSION_ACTION_CHOICES = [
    ('create', 'Create'),
    ('read', 'Read'),
    ('update', 'Update'),
    ('delete', 'Delete'),
    ('approve', 'Approve'),
    ('reject', 'Reject'),
    ('reassign', 'Reassign'),
    ('start_workflow', 'Start Workflow'),
    ('manage_module', 'Manage Module'),
    ('manage', 'Manage'),
    ('export', 'Export'),
    ('view_resume', 'View Resume'),
    ('shortlist', 'Shortlist'),
]

PERMISSION_RESOURCE_CHOICES = [
    ('organization', 'Organization'),
    ('user', 'User'),
    ('role', 'Role'),
    ('module', 'Module'),
    ('client', 'Client'),
    ('site', 'Site'),
    ('site_role_requirement', 'Site Role Requirement'),
    ('job_role', 'Job Role'),
    ('campaign', 'Campaign'),
    ('submission', 'Submission'),
    ('candidate', 'Candidate'),
    ('resume', 'Resume'),
    ('mrf', 'MRF'),
    ('workflow', 'Workflow'),
    ('wage', 'Wage'),
    ('budget', 'Budget'),
    ('interview', 'Interview'),
    ('offer', 'Offer'),
    ('hiring_application', 'Hiring Application'),
    ('employee', 'Employee'),
    ('site_deployment', 'Site Deployment'),
    ('department', 'Department'),
    ('deployment', 'Deployment'),
    ('report', 'Report'),
    ('field_tracking', 'Field Tracking'),
    ('client_onboarding', 'Client Onboarding'),
]


class AccessRole(TimeStampedModel):
    """Organizational role with optional scope restriction."""
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='access_roles')
    name = models.CharField(max_length=128)
    code = models.CharField(max_length=64)
    node_type_scope = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Access Role'
        verbose_name_plural = 'Access Roles'
        unique_together = [['org', 'code']]
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Permission(models.Model):
    """A single action on a resource."""
    action = models.CharField(max_length=32, choices=PERMISSION_ACTION_CHOICES)
    resource = models.CharField(max_length=64, choices=PERMISSION_RESOURCE_CHOICES)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Permission'
        verbose_name_plural = 'Permissions'
        unique_together = [['action', 'resource']]
        ordering = ['resource', 'action']

    def __str__(self):
        return f"{self.resource}.{self.action}"


class AccessRolePermission(models.Model):
    """Many-to-many between AccessRole and Permission."""
    role = models.ForeignKey(AccessRole, on_delete=models.CASCADE, related_name='role_permissions')
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name='role_permissions')

    class Meta:
        verbose_name = 'Role Permission'
        verbose_name_plural = 'Role Permissions'
        unique_together = [['role', 'permission']]

    def __str__(self):
        return f"{self.role.code} → {self.permission}"


class UserScopeAssignment(models.Model):
    """Assigns a user to a scope node with an assignment type."""

    ASSIGNMENT_TYPE_CHOICES = [
        ('primary', 'Primary'),
        ('additional', 'Additional'),
        ('delegated', 'Delegated'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scope_assignments',
    )
    scope_node = models.ForeignKey(
        ScopeNode,
        on_delete=models.CASCADE,
        related_name='user_scope_assignments',
    )
    assignment_type = models.CharField(max_length=16, choices=ASSIGNMENT_TYPE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'User Scope Assignment'
        verbose_name_plural = 'User Scope Assignments'
        unique_together = [['user', 'scope_node', 'assignment_type']]

    def __str__(self):
        return f"{self.user} @ {self.scope_node} ({self.assignment_type})"


class UserRoleAssignment(models.Model):
    """Assigns a role to a user within a specific scope node."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='role_assignments',
    )
    role = models.ForeignKey(
        AccessRole,
        on_delete=models.CASCADE,
        related_name='user_assignments',
    )
    scope_node = models.ForeignKey(
        ScopeNode,
        on_delete=models.CASCADE,
        related_name='role_assignments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'User Role Assignment'
        verbose_name_plural = 'User Role Assignments'
        unique_together = [['user', 'role', 'scope_node']]
        indexes = [
            models.Index(fields=['user', 'scope_node']),
            models.Index(fields=['role', 'scope_node']),
        ]

    def __str__(self):
        return f"{self.user} → {self.role.code} @ {self.scope_node}"

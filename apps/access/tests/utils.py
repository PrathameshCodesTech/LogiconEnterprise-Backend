"""
apps/access/tests/utils.py

Shared helpers for access-layer tests.
"""

from apps.access.capabilities import ROLE_CAPABILITIES
from apps.access.models import AccessRolePermission, Permission


def capability_to_resource_action(cap: str):
    """Derive (resource, action) from a capability string."""
    parts = cap.split('.')
    if len(parts) == 2:
        return parts[0], parts[1]
    # 3-part: e.g. workflow.config.read → resource=workflow_config, action=read
    return '_'.join(parts[:-1]), parts[-1]


def get_or_create_permission(cap: str) -> Permission:
    """Return the Permission with code=cap, creating it if it doesn't exist."""
    resource, action = capability_to_resource_action(cap)
    perm, _ = Permission.objects.get_or_create(
        code=cap,
        defaults={'resource': resource, 'action': action},
    )
    return perm


def bootstrap_role_permissions(role, caps=None):
    """
    Create Permission + AccessRolePermission rows for the given role.

    If caps is None, uses ROLE_CAPABILITIES[role.code]. Pass an explicit list
    to grant only specific capabilities.
    """
    if caps is None:
        caps = ROLE_CAPABILITIES.get(role.code, [])
    for cap in caps:
        perm = get_or_create_permission(cap)
        AccessRolePermission.objects.get_or_create(role=role, permission=perm)

"""
apps/access/scope.py

Scope resolution service.

Authority model:
  UserRoleAssignment(user, role, scope_node) is the source of access truth.
  A user assigned at path "logicon/client-a" implicitly has access to all
  descendants: "logicon/client-a/*".

Superuser bypass:
  is_superuser users bypass all scope checks.

UserScopeAssignment is informational / membership only.
It does NOT grant access here unless we explicitly choose to read it.
"""

from django.db.models import Q


def get_user_role_assignments(user):
    """Return all UserRoleAssignment rows for user, with role and scope_node prefetched."""
    from apps.access.models import UserRoleAssignment
    return (
        UserRoleAssignment.objects
        .filter(user=user)
        .select_related('role', 'scope_node')
    )


def get_user_scope_assignments(user):
    """Return all UserScopeAssignment rows for user (informational)."""
    from apps.access.models import UserScopeAssignment
    return (
        UserScopeAssignment.objects
        .filter(user=user)
        .select_related('scope_node')
    )


def get_user_scope_nodes(user):
    """Return ScopeNode objects the user is role-assigned to."""
    assignments = get_user_role_assignments(user)
    return [a.scope_node for a in assignments if a.scope_node]


def get_user_scope_paths(user) -> set:
    """Return the exact scope paths the user is directly role-assigned to."""
    from apps.access.models import UserRoleAssignment
    paths = (
        UserRoleAssignment.objects
        .filter(user=user)
        .exclude(scope_node__path='')
        .values_list('scope_node__path', flat=True)
        .distinct()
    )
    return set(paths)


def get_descendant_scope_paths(scope_node):
    """
    Return all scope node paths that are descendants of scope_node (inclusive).
    Uses path prefix matching — no recursive DB traversal needed.
    """
    from apps.core.models import ScopeNode
    path = scope_node.path
    if not path:
        return []
    qs = ScopeNode.objects.filter(
        Q(path=path) | Q(path__startswith=path + '/')
    ).values_list('path', flat=True)
    return list(qs)


def get_accessible_scope_paths(user) -> set:
    """
    Return the set of scope paths this user can access.

    For superuser: returns {'*'} sentinel — callers must check this.
    For normal users: returns exact assigned paths. Descendant matching
    is handled by query-time prefix matching, not by expanding here.
    Returns empty set if user has no role assignments.
    """
    if user.is_superuser:
        return {'*'}
    return get_user_scope_paths(user)


def user_has_capability(user, capability: str) -> bool:
    """
    Return True if user has the given capability via any of their role assignments.
    Superuser always returns True.
    """
    if user.is_superuser:
        return True
    from apps.access.capabilities import get_user_capabilities
    return capability in get_user_capabilities(user)


def user_has_any_capability(user, capabilities) -> bool:
    """
    Return True if user has at least one of the given capabilities.
    Superuser always returns True.
    """
    if user.is_superuser:
        return True
    from apps.access.capabilities import get_user_capabilities
    user_caps = set(get_user_capabilities(user))
    return bool(user_caps.intersection(capabilities))


def actor_can_access_scope(user, scope_node) -> bool:
    """
    Return True if the user's accessible scope paths cover the given scope_node.

    "Cover" means: scope_node.path equals one of the user's assigned paths,
    or scope_node.path starts with an assigned path + '/'.

    Superuser always returns True.
    """
    if user.is_superuser:
        return True
    paths = get_accessible_scope_paths(user)
    if not paths:
        return False
    node_path = scope_node.path
    for p in paths:
        if node_path == p or node_path.startswith(p + '/'):
            return True
    return False

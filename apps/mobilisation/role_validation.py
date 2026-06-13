"""
Role eligibility rules for mobilisation client-user setup.
"""

from apps.access.capabilities import CLIENT_FACING_ROLE_CODES


CLIENT_SCOPE_ROLE_CODES = frozenset({'client_admin', 'client_user'})
SITE_SCOPE_ROLE_CODES = frozenset({'client_site_user', 'site_supervisor'})


def eligible_client_user_role_codes(scope_level):
    """Return client-facing access role codes allowed for a proposed user scope."""
    if scope_level == 'site':
        return SITE_SCOPE_ROLE_CODES
    return CLIENT_SCOPE_ROLE_CODES


def is_eligible_client_user_role(access_role, scope_level):
    """
    True when an AccessRole may be assigned to a mobilisation proposed user.

    Mobilisation creates client portal users only. Internal roles must never be
    selectable or accepted here, even if they belong to the same organization.
    """
    if access_role is None or not access_role.is_active:
        return False

    code = access_role.code
    if code not in CLIENT_FACING_ROLE_CODES:
        return False
    if code not in eligible_client_user_role_codes(scope_level):
        return False

    expected_node_type = 'site' if scope_level == 'site' else 'client'
    node_type_scope = access_role.node_type_scope or ''
    return node_type_scope in ('', expected_node_type)


def validate_client_user_access_role(access_role, scope_level):
    """Raise ValueError when the role is not valid for mobilisation client users."""
    if is_eligible_client_user_role(access_role, scope_level):
        return
    expected = 'site-level client role' if scope_level == 'site' else 'client-level portal role'
    raise ValueError(
        f"Access role must be a {expected} for mobilisation client users."
    )

"""
apps/access/permissions.py

DRF permission classes for capability enforcement.

Usage — static capability on a ViewSet:
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = "site.read"

Usage — per-action mapping (with ActionCapabilityMixin):
    permission_classes = [IsAuthenticated, HasCapability]
    action_required_capabilities = {
        'list': 'client.read',
        'create': 'client.create',
        ...
    }
    # Implement get_required_capability() via ActionCapabilityMixin

Design rules:
  - Superuser always passes.
  - No capability declared → deny (safe default).
  - Missing capability → 403.
  - Has capability but no scope match → 200 empty (handled by queryset).
"""

from rest_framework.permissions import BasePermission

from apps.access.scope import user_has_capability, user_has_any_capability


class HasCapability(BasePermission):
    """
    Grants access if the user has the required capability for the current action.

    Resolution order:
    1. view.get_required_capability() — from ActionCapabilityMixin (per-action map)
    2. view.required_capability      — static string fallback
    3. None → deny
    """

    message = 'You do not have permission to perform this action.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True

        if hasattr(view, 'get_required_capability'):
            capability = view.get_required_capability()
        else:
            capability = getattr(view, 'required_capability', None)

        if not capability:
            return False
        return user_has_capability(request.user, capability)


class HasAnyCapability(BasePermission):
    """
    Grants access if the user has ANY of view.required_capabilities.
    Superuser bypasses. Deny if no capabilities declared.
    """

    message = 'You do not have permission to perform this action.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        capabilities = getattr(view, 'required_capabilities', None)
        if not capabilities:
            return False
        return user_has_any_capability(request.user, capabilities)

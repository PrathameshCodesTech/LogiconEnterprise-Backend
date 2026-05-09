"""
apps/audit/services.py

Audit logging service. Call log_audit() after every significant mutation.
"""

from .models import AuditLog


def _get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def log_audit(actor, action, obj, org=None, scope_node=None, metadata=None, request=None):
    """
    Create an AuditLog record.

    actor       — the User performing the action (may be None for system actions)
    action      — string like 'client.create', 'site.delete', 'role_assignment.create'
    obj         — the model instance that was acted upon
    org         — Organization FK; defaults to actor.org if not provided
    scope_node  — optional ScopeNode for context
    metadata    — dict of extra context (field changes, etc.)
    request     — DRF/Django request; used to extract IP and user-agent
    """
    ip = None
    ua = ''
    if request is not None:
        ip = _get_client_ip(request)
        ua = (request.META.get('HTTP_USER_AGENT') or '')[:512]

    resolved_org = org
    if resolved_org is None and actor is not None:
        resolved_org = getattr(actor, 'org', None)

    AuditLog.objects.create(
        actor=actor,
        org=resolved_org,
        scope_node=scope_node,
        action=action,
        object_type=type(obj).__name__,
        object_id=str(obj.pk) if obj.pk is not None else '',
        metadata=metadata or {},
        ip_address=ip,
        user_agent=ua,
    )

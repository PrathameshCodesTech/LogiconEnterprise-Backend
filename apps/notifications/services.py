import logging

logger = logging.getLogger(__name__)


def create_notification(
    *,
    recipient,
    title,
    message='',
    notification_type='system',
    actor=None,
    org=None,
    target_type='',
    target_id=None,
    target_url='',
    metadata=None,
):
    """
    Best-effort persistent notification creation.

    Returns the Notification instance or None when the recipient/org is invalid.
    Callers should not fail business flows because notification creation failed.
    """
    if recipient is None or not getattr(recipient, 'is_active', False):
        return None
    resolved_org = org or getattr(recipient, 'org', None)
    if resolved_org is None:
        return None
    try:
        from .models import Notification
        return Notification.objects.create(
            org=resolved_org,
            recipient=recipient,
            actor=actor if getattr(actor, 'pk', None) else None,
            title=title,
            message=message or '',
            notification_type=notification_type,
            target_type=target_type or '',
            target_id=target_id,
            target_url=target_url or '',
            metadata=metadata or {},
        )
    except Exception:
        logger.exception('Failed to create notification title=%r recipient=%r', title, recipient)
        return None


def create_notifications(recipients, **kwargs):
    created = []
    seen = set()
    for recipient in recipients or []:
        rid = getattr(recipient, 'pk', None)
        if not rid or rid in seen:
            continue
        seen.add(rid)
        item = create_notification(recipient=recipient, **kwargs)
        if item is not None:
            created.append(item)
    return created


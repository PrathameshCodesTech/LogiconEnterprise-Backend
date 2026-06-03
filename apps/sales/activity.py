"""
apps/sales/activity.py

Single entry-point for writing SalesLeadActivity records.
Call from services — never directly from views.
"""

import json
import logging

logger = logging.getLogger(__name__)


def log_sales_activity(
    lead,
    activity_type,
    title,
    message='',
    actor=None,
    proposal_version=None,
    site=None,
    metadata=None,
):
    """
    Append one activity entry to the lead's timeline.

    Metadata serialisation failures are silently reduced to {} so a bad
    metadata dict never rolls back the surrounding business transaction.
    Real DB errors propagate normally.
    """
    from apps.sales.models import SalesLeadActivity

    safe_meta = metadata or {}
    try:
        json.dumps(safe_meta)
    except (TypeError, ValueError):
        logger.warning(
            'log_sales_activity: non-serialisable metadata for type=%s lead_pk=%s; '
            'dropping metadata.',
            activity_type, lead.pk,
        )
        safe_meta = {}

    SalesLeadActivity.objects.create(
        org_id=lead.org_id,
        lead=lead,
        proposal_version=proposal_version,
        site=site,
        activity_type=activity_type,
        title=title,
        message=message or '',
        actor=actor,
        metadata=safe_meta,
    )

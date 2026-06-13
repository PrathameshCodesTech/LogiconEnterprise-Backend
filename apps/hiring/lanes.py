
"""
Hiring lane helpers.

The lane is derived from the source MRF. Keep this centralized so API
serializers and lifecycle guards do not guess independently.
"""

LANE_CLIENT_BILLABLE = 'client_billable'
LANE_INTERNAL_NON_BILLABLE = 'internal_non_billable'

LANE_LABELS = {
    LANE_CLIENT_BILLABLE: 'Client-site billable',
    LANE_INTERNAL_NON_BILLABLE: 'Internal non-billable',
}


def hiring_lane_for_mrf(mrf):
    if getattr(mrf, 'billing_type', None) == 'non_billable':
        return LANE_INTERNAL_NON_BILLABLE
    return LANE_CLIENT_BILLABLE


def hiring_lane_label(lane):
    return LANE_LABELS.get(lane, lane)


def requires_client_review_for_mrf(mrf):
    return hiring_lane_for_mrf(mrf) == LANE_CLIENT_BILLABLE


def hiring_lane_for_application(application):
    return hiring_lane_for_mrf(application.mrf)


def requires_client_review_for_application(application):
    return requires_client_review_for_mrf(application.mrf)

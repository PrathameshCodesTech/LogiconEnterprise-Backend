"""
apps/hiring/offer_services.py

Offer lifecycle state machine — all transitions go through here.
"""

from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Offer
from .lifecycle import (
    STAGE_CLIENT_APPROVED,
    STAGE_OFFER,
    STAGE_OFFER_ACCEPTED,
    STAGE_REJECTED_CLOSED,
    transition_application,
)


_OFFER_CREATE_ALLOWED_STATUSES = {'selected'}
_OFFER_IMMUTABLE_STATUSES = {'accepted', 'declined', 'withdrawn', 'expired'}


def _record_app_history(application, actor, old_status, new_status, comment, stage_code=None):
    transition_application(
        application,
        actor=actor,
        status=new_status,
        stage_code=stage_code,
        comment=comment,
    )


def create_or_update_offer(application, actor, payload: dict) -> Offer:
    """
    Create (or update a draft) offer for a selected application.

    payload keys: offered_ctc, salary_breakup, joining_date, notes
    """
    if application.status not in _OFFER_CREATE_ALLOWED_STATUSES:
        raise ValidationError({
            'non_field_errors': (
                f"Offers can only be created for applications with status 'selected'. "
                f"Current: '{application.status}'."
            )
        })

    if application.client_visible and application.client_decision != 'approved':
        raise ValidationError({
            'non_field_errors': 'Client approval is required before creating an offer.'
        })

    existing = _get_offer(application)
    if existing is not None and existing.status in _OFFER_IMMUTABLE_STATUSES:
        raise ValidationError({
            'non_field_errors': f"Cannot update offer in status '{existing.status}'."
        })

    offered_ctc = payload.get('offered_ctc')
    salary_breakup = payload.get('salary_breakup', {})
    joining_date = payload.get('joining_date')
    notes = payload.get('notes', '')

    if offered_ctc is not None and offered_ctc <= 0:
        raise ValidationError({'offered_ctc': 'offered_ctc must be greater than 0.'})

    if salary_breakup is not None and not isinstance(salary_breakup, dict):
        raise ValidationError({'salary_breakup': 'salary_breakup must be a JSON object.'})

    if joining_date is not None and joining_date < date.today():
        raise ValidationError({'joining_date': 'joining_date cannot be in the past.'})

    with transaction.atomic():
        if existing is None:
            offer = Offer.objects.create(
                hiring_application=application,
                offered_ctc=offered_ctc,
                salary_breakup=salary_breakup or {},
                joining_date=joining_date,
                notes=notes,
                status='draft',
            )
        else:
            offer = existing
            update_fields = []
            if offered_ctc is not None:
                offer.offered_ctc = offered_ctc
                update_fields.append('offered_ctc')
            if salary_breakup is not None:
                offer.salary_breakup = salary_breakup
                update_fields.append('salary_breakup')
            if joining_date is not None:
                offer.joining_date = joining_date
                update_fields.append('joining_date')
            if notes is not None:
                offer.notes = notes
                update_fields.append('notes')
            if update_fields:
                offer.save(update_fields=update_fields)

    return offer


def release_offer(offer, actor, note='') -> Offer:
    """Release a draft offer: draft → released; app → offer_released."""
    if offer.status != 'draft':
        raise ValidationError({
            'non_field_errors': f"Only draft offers can be released. Current: '{offer.status}'."
        })

    application = offer.hiring_application
    if application.status != 'selected':
        raise ValidationError({
            'non_field_errors': (
                f"Application must be 'selected' to release an offer. "
                f"Current: '{application.status}'."
            )
        })

    if application.client_visible and application.client_decision != 'approved':
        raise ValidationError({
            'non_field_errors': 'Client approval is required before releasing an offer.'
        })

    old_status = application.status

    with transaction.atomic():
        offer.status = 'released'
        offer.released_by = actor
        offer.released_at = timezone.now()
        offer.save(update_fields=['status', 'released_by', 'released_at'])

        _record_app_history(
            application, actor, old_status, 'offer_released',
            note or 'Offer released to candidate.',
            stage_code=STAGE_OFFER,
        )

    return offer


def accept_offer(offer, actor=None, note='') -> Offer:
    """Record candidate acceptance: released → accepted; app → offer_accepted."""
    if offer.status != 'released':
        raise ValidationError({
            'non_field_errors': f"Only released offers can be accepted. Current: '{offer.status}'."
        })

    application = offer.hiring_application
    old_status = application.status

    with transaction.atomic():
        offer.status = 'accepted'
        offer.accepted_at = timezone.now()
        offer.save(update_fields=['status', 'accepted_at'])

        _record_app_history(
            application, actor, old_status, 'offer_accepted',
            note or 'Candidate accepted the offer.',
            stage_code=STAGE_OFFER_ACCEPTED,
        )

    return offer


def decline_offer(offer, actor=None, note='') -> Offer:
    """Record candidate declination: released → declined; app → offer_declined."""
    if offer.status != 'released':
        raise ValidationError({
            'non_field_errors': f"Only released offers can be declined. Current: '{offer.status}'."
        })

    application = offer.hiring_application
    old_status = application.status

    with transaction.atomic():
        offer.status = 'declined'
        offer.declined_at = timezone.now()
        offer.save(update_fields=['status', 'declined_at'])

        _record_app_history(
            application, actor, old_status, 'offer_declined',
            note or 'Candidate declined the offer.',
            stage_code=STAGE_REJECTED_CLOSED,
        )

    return offer


def withdraw_offer(offer, actor, note='') -> Offer:
    """Withdraw draft or released offer. Released → app back to selected."""
    if offer.status not in ('draft', 'released'):
        raise ValidationError({
            'non_field_errors': (
                f"Only draft or released offers can be withdrawn. Current: '{offer.status}'."
            )
        })

    application = offer.hiring_application
    old_status = application.status

    with transaction.atomic():
        offer.status = 'withdrawn'
        offer.save(update_fields=['status'])

        if old_status == 'offer_released':
            _record_app_history(
                application, actor, old_status, 'selected',
                note or 'Offer withdrawn; application returned to selected.',
                stage_code=STAGE_CLIENT_APPROVED,
            )

    return offer


def expire_offer(offer, actor=None, note='') -> Offer:
    """Expire a released offer; app returns to selected for re-offer."""
    if offer.status != 'released':
        raise ValidationError({
            'non_field_errors': f"Only released offers can be expired. Current: '{offer.status}'."
        })

    application = offer.hiring_application
    old_status = application.status

    with transaction.atomic():
        offer.status = 'expired'
        offer.save(update_fields=['status'])

        _record_app_history(
            application, actor, old_status, 'selected',
            note or 'Offer expired; candidate remains selected for re-offer or manual action.',
            stage_code=STAGE_CLIENT_APPROVED,
        )

    return offer


def _get_offer(application):
    """Return the offer for an application, or None."""
    try:
        return application.offer
    except Offer.DoesNotExist:
        return None

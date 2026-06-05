"""
apps/hiring/lifecycle.py

Shared helpers for keeping HiringApplication.status, current_stage, and
ApplicationStageHistory in sync.
"""

from __future__ import annotations

from typing import Any

from .models import ApplicationStageHistory, PipelineStage


STAGE_SHORTLISTED = 'shortlisted'
STAGE_CLIENT_REVIEW = 'client_review'
STAGE_CLIENT_APPROVED = 'client_approved'
STAGE_INTERVIEW = 'interview'
STAGE_OFFER = 'offer'
STAGE_OFFER_ACCEPTED = 'offer_accepted'
STAGE_DEPLOYED = 'deployed'
STAGE_REJECTED_CLOSED = 'rejected_closed'


DEFAULT_PIPELINE_STAGES = [
    (STAGE_SHORTLISTED, 'Shortlisted', 10, 'sourcing', False),
    (STAGE_CLIENT_REVIEW, 'Client Review', 20, 'screening', False),
    (STAGE_CLIENT_APPROVED, 'Client Approved', 30, 'screening', False),
    (STAGE_INTERVIEW, 'Interview / Verification', 40, 'interview', False),
    (STAGE_OFFER, 'Offer', 50, 'offer', False),
    (STAGE_OFFER_ACCEPTED, 'Offer Accepted', 60, 'onboarding', False),
    (STAGE_DEPLOYED, 'Deployed', 70, 'onboarding', True),
    (STAGE_REJECTED_CLOSED, 'Rejected / Closed', 80, 'onboarding', True),
]


def ensure_default_pipeline_stages(org) -> list[PipelineStage]:
    """Create/update the standard hiring stages for an organization."""
    stages = []
    for code, name, order, stage_type, is_terminal in DEFAULT_PIPELINE_STAGES:
        stage, _ = PipelineStage.objects.update_or_create(
            org=org,
            code=code,
            defaults={
                'name': name,
                'order': order,
                'stage_type': stage_type,
                'is_terminal': is_terminal,
                'is_active': True,
            },
        )
        stages.append(stage)
    return stages


def resolve_pipeline_stage(org, code: str | None, fallback=None):
    """Return an active stage by code, or fallback when not configured."""
    if code:
        stage = (
            PipelineStage.objects
            .filter(org=org, code=code, is_active=True)
            .first()
        )
        if stage is not None:
            return stage
    return fallback


def default_initial_stage(org):
    """Prefer the standard Shortlisted stage; fall back to first active stage."""
    return (
        resolve_pipeline_stage(org, STAGE_SHORTLISTED)
        or PipelineStage.objects.filter(org=org, is_active=True).order_by('order').first()
    )


def transition_application(
    application,
    *,
    actor=None,
    status: str | None = None,
    stage_code: str | None = None,
    comment: str = '',
    extra_attrs: dict[str, Any] | None = None,
):
    """
    Atomically apply status/stage changes and write one history row.

    If a requested stage code is not configured for the org, the current stage
    is preserved. This keeps older test fixtures and partially configured orgs
    working while still using configured stages when they exist.
    """
    old_stage = application.current_stage
    old_status = application.status

    update_fields: list[str] = []
    history_needed = False

    if extra_attrs:
        for field, value in extra_attrs.items():
            setattr(application, field, value)
            update_fields.append(field)

    if status is not None and status != application.status:
        application.status = status
        update_fields.append('status')
        history_needed = True

    new_stage = resolve_pipeline_stage(application.org, stage_code, fallback=application.current_stage)
    if new_stage != application.current_stage:
        application.current_stage = new_stage
        update_fields.append('current_stage')
        history_needed = True

    if update_fields:
        if hasattr(application, 'updated_at'):
            update_fields.append('updated_at')
        application.save(update_fields=list(dict.fromkeys(update_fields)))

    if history_needed or comment:
        ApplicationStageHistory.objects.create(
            hiring_application=application,
            from_stage=old_stage,
            to_stage=application.current_stage,
            from_status=old_status,
            to_status=application.status,
            moved_by=actor,
            comment=comment,
        )

    return application

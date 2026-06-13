from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.access.capabilities import INTERVIEW_MANAGE, get_user_capabilities
from .lifecycle import (
    STAGE_CLIENT_APPROVED,
    STAGE_INTERVIEW,
    STAGE_REJECTED_CLOSED,
    transition_application,
)
from .models import Interview, InterviewFeedback


BLOCKED_APPLICATION_STATUSES = {
    'rejected', 'cancelled', 'deployed', 'offer_released',
    'offer_accepted', 'offer_declined',
}


def required_plan_rounds(application):
    plan = application.interview_plan
    if plan is None:
        return []
    return list(
        plan.rounds
        .filter(is_required=True, is_active=True)
        .order_by('round_number', 'id')
    )


def required_plan_round_ids(application):
    return {round_obj.id for round_obj in required_plan_rounds(application)}


def passed_required_plan_round_ids(application):
    return set(
        Interview.objects
        .filter(
            hiring_application=application,
            planned_round__isnull=False,
            status='completed',
            feedbacks__recommendation='proceed',
        )
        .values_list('planned_round_id', flat=True)
        .distinct()
    )


def all_required_plan_rounds_passed(application):
    required_ids = required_plan_round_ids(application)
    if not required_ids:
        return True
    return required_ids.issubset(passed_required_plan_round_ids(application))


def latest_feedback_for_interview(interview):
    feedbacks = list(getattr(interview, '_prefetched_objects_cache', {}).get('feedbacks', []))
    if not feedbacks:
        feedbacks = list(interview.feedbacks.all())
    if not feedbacks:
        return None
    return sorted(feedbacks, key=lambda item: item.created_at, reverse=True)[0]


def next_required_round(application):
    passed_ids = passed_required_plan_round_ids(application)
    for round_obj in required_plan_rounds(application):
        if round_obj.id not in passed_ids:
            return round_obj
    return None


def validate_planned_round_can_be_scheduled(application, planned_round, *, current_interview=None):
    if planned_round is None:
        return
    if planned_round.plan.org_id != application.org_id:
        raise ValidationError({
            'planned_round': 'Planned round does not belong to this application organization.'
        })
    if application.interview_plan_id and planned_round.plan_id != application.interview_plan_id:
        raise ValidationError({
            'planned_round': 'Planned round is not part of the selected interview plan.'
        })

    next_round = next_required_round(application)
    if next_round is None:
        return
    if planned_round.id == next_round.id:
        return
    raise ValidationError({
        'planned_round': (
            f"Complete round {next_round.round_number} ({next_round.get_round_type_display()}) "
            "before scheduling a later round."
        )
    })


def can_create_offer_for_application(application):
    if application.status != 'selected':
        return False
    if application.interview_plan_id:
        return all_required_plan_rounds_passed(application)
    return True


def validate_offer_ready(application):
    if application.status != 'selected':
        raise ValidationError({
            'non_field_errors': (
                f"Offers can only be created for applications with status 'selected'. "
                f"Current: '{application.status}'."
            )
        })
    if application.interview_plan_id and not all_required_plan_rounds_passed(application):
        next_round = next_required_round(application)
        if next_round is not None:
            raise ValidationError({
                'non_field_errors': (
                    f"Cannot create offer yet. Required interview round "
                    f"{next_round.round_number} ({next_round.get_round_type_display()}) "
                    "has not been cleared."
                )
            })


def apply_interview_feedback_effect(feedback, actor):
    interview = feedback.interview
    application = interview.hiring_application

    with transaction.atomic():
        if interview.status != 'completed':
            interview.status = 'completed'
            interview.save(update_fields=['status', 'updated_at'])

        if feedback.recommendation == 'reject':
            transition_application(
                application,
                actor=actor,
                status='rejected',
                stage_code=STAGE_REJECTED_CLOSED,
                comment='Interview feedback recommendation: reject.',
            )
            return application

        if feedback.recommendation == 'hold':
            transition_application(
                application,
                actor=actor,
                status='interview_in_progress',
                stage_code=STAGE_INTERVIEW,
                comment='Interview feedback recommendation: hold.',
            )
            return application

        if feedback.recommendation == 'proceed':
            if all_required_plan_rounds_passed(application):
                transition_application(
                    application,
                    actor=actor,
                    status='selected',
                    stage_code=STAGE_CLIENT_APPROVED,
                    comment='All required interview rounds passed.',
                )
            else:
                transition_application(
                    application,
                    actor=actor,
                    status='interview_in_progress',
                    stage_code=STAGE_INTERVIEW,
                    comment='Interview feedback recommendation: proceed. More rounds remain.',
                )
        return application


def user_can_manage_interviews(user):
    return bool(
        getattr(user, 'is_superuser', False)
        or INTERVIEW_MANAGE in get_user_capabilities(user)
    )


def interview_assignment_state(interview):
    feedback = latest_feedback_for_interview(interview)
    if interview.status == 'completed' and feedback is None:
        return 'pending_feedback'
    if feedback and feedback.recommendation == 'hold':
        return 'held'
    if feedback and feedback.recommendation == 'reject':
        return 'rejected'
    if interview.status in {'pending', 'scheduled', 'rescheduled', 'no_show'}:
        return 'upcoming'
    if interview.status == 'completed':
        return 'completed'
    return interview.status


def notify_interview_scheduled(interview, actor):
    if interview.interviewer_id is None:
        return
    try:
        from apps.notifications.services import create_notification
        app = interview.hiring_application
        candidate_name = app.candidate.full_name
        create_notification(
            recipient=interview.interviewer,
            actor=actor,
            org=app.org,
            title=f'Interview assigned: {candidate_name}',
            message=(
                f"Round {interview.round_number} ({interview.get_round_type_display()}) "
                f"for {app.job_role.name}."
            ),
            notification_type='system',
            target_type='hiring_interview',
            target_id=interview.pk,
            target_url=f'/hiring/applications/{app.pk}',
            metadata={
                'interview_id': interview.pk,
                'application_id': app.pk,
                'round_number': interview.round_number,
                'round_type': interview.round_type,
            },
        )
    except Exception:
        # Notification failures must never block interview scheduling.
        return

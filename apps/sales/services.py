"""
apps/sales/services.py

Sales lead lifecycle transitions and proposal generation/versioning.
"""

from django.db import transaction
from django.utils import timezone

from apps.sales.activity import log_sales_activity


# ─── Lead Transition Services ─────────────────────────────────────────────────

def get_operations_department(org):
    """Return the org-level Operations department used for survey ownership."""
    from apps.core.models import Department

    return (
        Department.objects
        .filter(
            org=org,
            code__iexact='operations',
            client__isnull=True,
            site__isnull=True,
            is_active=True,
        )
        .first()
    )


def validate_operations_survey_owner(org, user):
    """
    Survey owners must be active internal users in the org-level Operations
    department. Do not fall back to all internal users.
    """
    operations_department = get_operations_department(org)
    if operations_department is None:
        raise ValueError(
            'Operations department is not configured for this organization. '
            'Create an active org-level department with code "operations".'
        )
    if user.org_id != org.id:
        raise ValueError('Operations owner not found in this org.')
    if user.user_type != 'internal' or not user.is_active:
        raise ValueError('Operations owner must be an active internal user.')
    if user.department_id != operations_department.id:
        raise ValueError(
            'Operations owner must be assigned to the org-level Operations department.'
        )
    return user


def submit_to_operations(lead, user, operations_owner=None):
    """Sales submits lead to operations; creates pending SiteSurvey per active site."""
    if lead.current_stage != 'draft':
        raise ValueError(f"Cannot submit from stage '{lead.current_stage}'.")
    from apps.sales.models import SalesLeadSite, SiteSurvey
    if operations_owner is not None:
        validate_operations_survey_owner(lead.org, operations_owner)
    now = timezone.now()
    update_fields = ['current_stage', 'current_status', 'submitted_to_operations_at',
                     'submitted_to_operations_by', 'updated_at']
    lead.current_stage = 'submitted_to_operations'
    lead.current_status = 'active'
    lead.submitted_to_operations_at = now
    lead.submitted_to_operations_by = user
    if operations_owner is not None:
        lead.operations_owner = operations_owner
        update_fields.append('operations_owner')
    lead.save(update_fields=update_fields)
    for site in SalesLeadSite.objects.filter(lead=lead, is_active=True):
        defaults = {'status': 'pending'}
        if operations_owner is not None:
            defaults.update({
                'assigned_to': operations_owner,
                'assigned_at': now,
            })
        survey, created = SiteSurvey.objects.get_or_create(
            lead=lead,
            site=site,
            defaults=defaults,
        )
        if operations_owner is not None and survey.assigned_to_id is None:
            survey.assigned_to = operations_owner
            survey.assigned_at = now
            survey.save(update_fields=['assigned_to', 'assigned_at', 'updated_at'])
        seed_default_survey_lines(survey)
        if operations_owner is not None:
            try:
                from apps.notifications.services import create_notification
                create_notification(
                    recipient=operations_owner,
                    actor=user,
                    org=lead.org,
                    title=f'Site survey assigned: {site.site_name}',
                    message=f'{lead.client_name} is ready for operations survey.',
                    notification_type='sales_survey_assigned',
                    target_type='site_survey',
                    target_id=survey.pk,
                    target_url=f'/sales/operations-surveys/{survey.pk}',
                    metadata={'lead_id': lead.pk, 'site_id': site.pk},
                )
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    'Failed to notify survey assignment survey_id=%s', survey.pk,
                )
    log_sales_activity(
        lead=lead,
        activity_type='submitted_to_operations',
        title='Lead submitted to operations',
        actor=user,
    )
    return lead


def assign_survey_owner(survey, assigned_to, actor):
    """Operations assigns a survey to a team member."""
    validate_operations_survey_owner(survey.lead.org, assigned_to)
    survey.assigned_to = assigned_to
    survey.assigned_at = timezone.now()
    survey.save(update_fields=['assigned_to', 'assigned_at', 'updated_at'])
    log_sales_activity(
        lead=survey.lead,
        activity_type='survey_assigned',
        title=f'Survey assigned: {survey.site.site_name}',
        message=f'Assigned to {assigned_to.username}',
        actor=actor,
        site=survey.site,
        metadata={'survey_id': survey.pk, 'assigned_to_id': assigned_to.pk},
    )
    try:
        from apps.notifications.services import create_notification
        create_notification(
            recipient=assigned_to,
            actor=actor,
            org=survey.lead.org,
            title=f'Site survey assigned: {survey.site.site_name}',
            message=f'{survey.lead.client_name} is waiting for your survey input.',
            notification_type='sales_survey_assigned',
            target_type='site_survey',
            target_id=survey.pk,
            target_url=f'/sales/operations-surveys/{survey.pk}',
            metadata={'lead_id': survey.lead_id, 'site_id': survey.site_id},
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'Failed to notify survey owner survey_id=%s', survey.pk,
        )
    return survey


def mark_survey_started(survey, actor):
    """Survey owner marks this individual survey as started."""
    from apps.sales.models import LEAD_STAGE_CHOICES
    survey.status = 'in_progress'
    survey.started_at = timezone.now()
    survey.save(update_fields=['status', 'started_at', 'updated_at'])
    lead = survey.lead
    if lead.current_stage == 'submitted_to_operations':
        lead.current_stage = 'site_survey_in_progress'
        lead.save(update_fields=['current_stage', 'updated_at'])
    log_sales_activity(
        lead=lead,
        activity_type='survey_started',
        title=f'Survey started: {survey.site.site_name}',
        actor=actor,
        site=survey.site,
        metadata={'survey_id': survey.pk},
    )
    return survey


def mark_survey_completed(survey, actor):
    """Mark an individual survey completed; advances lead stage when all surveys done."""
    _validate_survey_can_be_completed(survey)

    survey.status = 'completed'
    survey.completed_at = timezone.now()
    survey.save(update_fields=['status', 'completed_at', 'updated_at'])
    lead = survey.lead
    all_done = not lead.surveys.filter(status__in=('pending', 'in_progress')).exists()
    if all_done and lead.current_stage in ('submitted_to_operations', 'site_survey_in_progress'):
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage', 'updated_at'])
    log_sales_activity(
        lead=lead,
        activity_type='survey_completed',
        title=f'Survey completed: {survey.site.site_name}',
        actor=actor,
        site=survey.site,
        metadata={'survey_id': survey.pk, 'all_surveys_done': all_done},
    )
    recipient = lead.sales_person or lead.submitted_to_operations_by or lead.created_by
    if recipient is not None:
        try:
            from apps.notifications.services import create_notification
            create_notification(
                recipient=recipient,
                actor=actor,
                org=lead.org,
                title=f'Site survey completed: {survey.site.site_name}',
                message=f'Operations completed the survey for {lead.client_name}.',
                notification_type='sales_survey_completed',
                target_type='sales_lead',
                target_id=lead.pk,
                target_url=f'/sales/leads/{lead.pk}',
                metadata={'survey_id': survey.pk, 'all_surveys_done': all_done},
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                'Failed to notify survey completion survey_id=%s', survey.pk,
            )
    return survey


def _validate_survey_can_be_completed(survey):
    """
    A survey is complete only after operations has converted applicable
    deployment/headcount rows into proposal-ready role requirements.
    """
    from apps.sales.models import SalesRoleRequirement, SiteSurveyShiftDeployment

    generated_count = SalesRoleRequirement.objects.filter(
        lead=survey.lead,
        site=survey.site,
        survey=survey,
        is_active=True,
        created_from_survey=True,
    ).count()
    if generated_count <= 0:
        raise ValueError(
            'Generate role requirements before marking the survey as completed.'
        )

    org = survey.lead.org
    rows = SiteSurveyShiftDeployment.objects.select_related('job_role').filter(
        survey=survey,
        line_type='item',
        is_applicable=True,
    ).order_by('sort_order', 'id')

    missing = []
    outdated = []
    for row in rows:
        normalized = _normalize_role_description(row.description)
        if not normalized:
            continue
        if normalized in SURVEY_ROLE_DENYLIST_DESCRIPTIONS:
            continue
        if _headcount_for_shift_row(row) <= 0:
            continue
        mapping, _reason = _resolve_survey_role_defaults(org, row)
        if mapping is None:
            missing.append(row.description)
            continue
        exists = SalesRoleRequirement.objects.filter(
            lead=survey.lead,
            site=survey.site,
            survey=survey,
            job_role=mapping.job_role,
            is_active=True,
            created_from_survey=True,
        ).first()
        if not exists:
            missing.append(row.description)
        elif _role_requirement_needs_sync(exists, survey, mapping, _headcount_for_shift_row(row)):
            outdated.append(row.description)

    if missing:
        preview = ', '.join(missing[:5])
        suffix = f' Missing rows: {preview}.'
        if len(missing) > 5:
            suffix = f' Missing rows: {preview}, +{len(missing) - 5} more.'
        raise ValueError(
            'Generate role requirements for every applicable deployment row before completion.'
            + suffix
        )
    if outdated:
        preview = ', '.join(outdated[:5])
        suffix = f' Outdated rows: {preview}.'
        if len(outdated) > 5:
            suffix = f' Outdated rows: {preview}, +{len(outdated) - 5} more.'
        raise ValueError(
            'Regenerate role requirements before marking the survey as completed.'
            + suffix
        )


def mark_survey_in_progress(lead, user):
    """Backward-compat: mark lead as survey in progress (also starts any pending surveys)."""
    if lead.current_stage != 'submitted_to_operations':
        raise ValueError(f"Cannot start survey from stage '{lead.current_stage}'.")
    now = timezone.now()
    lead.current_stage = 'site_survey_in_progress'
    lead.save(update_fields=['current_stage', 'updated_at'])
    lead.surveys.filter(status='pending').update(status='in_progress', started_at=now)
    return lead


def complete_site_survey(lead, user):
    """Backward-compat: complete all surveys for a lead."""
    if lead.current_stage not in ('submitted_to_operations', 'site_survey_in_progress'):
        raise ValueError(f"Cannot complete survey from stage '{lead.current_stage}'.")
    pending_surveys = list(lead.surveys.filter(status__in=('pending', 'in_progress')))
    for survey in pending_surveys:
        _validate_survey_can_be_completed(survey)
    now = timezone.now()
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage', 'updated_at'])
    lead.surveys.filter(pk__in=[survey.pk for survey in pending_surveys]).update(
        status='completed', completed_at=now,
    )
    return lead


def approve_sales_role_requirement(role_requirement, user):
    """Operations approves a role requirement captured from a survey."""
    now = timezone.now()
    role_requirement.approved_by_operations = True
    role_requirement.approved_at = now
    role_requirement.approved_by = user
    role_requirement.save(update_fields=['approved_by_operations', 'approved_at', 'approved_by', 'updated_at'])
    log_sales_activity(
        lead=role_requirement.lead,
        activity_type='role_requirement_approved',
        title=f'Role requirement approved: {role_requirement.job_role.name}',
        actor=user,
        site=role_requirement.site,
        metadata={'role_requirement_id': role_requirement.pk, 'job_role': role_requirement.job_role.name},
    )
    return role_requirement


def mark_sales_review(lead, proposal, user):
    """Move lead and proposal to sales review stage."""
    lead.current_stage = 'sales_review'
    lead.save(update_fields=['current_stage', 'updated_at'])
    proposal.status = 'sales_review'
    proposal.save(update_fields=['status', 'updated_at'])
    return lead


def submit_proposal_for_internal_approval(proposal, user, approval_route=None):
    """
    Sales submits proposal to internal approval workflow (canonical path).

    Delegates to start_sales_proposal_workflow; raises ValueError on configuration errors.
    """
    from apps.workflow.exceptions import WorkflowConfigurationError
    from apps.workflow.services import start_sales_proposal_workflow

    _assert_proposal_breakup_lines_mapped(proposal)

    try:
        start_sales_proposal_workflow(proposal, user, approval_route=approval_route)
    except WorkflowConfigurationError as exc:
        raise ValueError(str(exc)) from exc
    proposal.refresh_from_db()
    log_sales_activity(
        lead=proposal.lead,
        activity_type='proposal_submitted_internal',
        title=f'Proposal v{proposal.version_number} submitted for internal approval',
        actor=user,
        proposal_version=proposal,
        metadata={'proposal_version_id': proposal.pk},
    )
    return proposal


def mark_proposal_internally_approved(proposal, user):
    """Internal approver marks proposal approved."""
    if proposal.status != 'submitted_internal':
        raise ValueError(f"Cannot approve from proposal status '{proposal.status}'.")
    _assert_proposal_breakup_lines_mapped(proposal)
    now = timezone.now()
    proposal.status = 'internally_approved'
    proposal.internal_approval_status = 'approved'
    proposal.internally_approved_at = now
    proposal.save(update_fields=['status', 'internal_approval_status', 'internally_approved_at', 'updated_at'])
    lead = proposal.lead
    lead.current_stage = 'internally_approved'
    lead.save(update_fields=['current_stage', 'updated_at'])
    return proposal


def _assert_proposal_internally_approved_for_client_send(proposal):
    if proposal.internal_approval_status != 'approved':
        raise ValueError(
            'Proposal must be internally approved before sending to the client.'
        )


def _assert_proposal_breakup_lines_mapped(proposal):
    if not proposal.breakup_lines.exists():
        raise ValueError('Proposal has no salary breakup lines. Regenerate the proposal before continuing.')
    missing_count = proposal.breakup_lines.filter(role_requirement__isnull=True).count()
    if missing_count:
        raise ValueError(
            'Salary breakup is missing role mapping. '
            'Regenerate this proposal so every salary component is linked to a role requirement.'
        )


def _revoke_active_client_tokens(proposal):
    from apps.sales.models import SalesProposalClientToken
    SalesProposalClientToken.objects.filter(
        proposal_version=proposal,
        is_revoked=False,
        used_at__isnull=True,
    ).update(is_revoked=True)


def _redact_client_proposal_token(email_body, raw_token):
    if not email_body:
        return ''
    if not raw_token:
        return str(email_body)
    return str(email_body).replace(str(raw_token), '[secure-token-redacted]')


@transaction.atomic
def create_client_proposal_token(proposal, recipient_email, actor, expires_days=7):
    """
    Create a hashed client access token for an internally approved proposal.

    Revokes prior unused active tokens for this proposal (latest token wins on resend).
    Returns (token_record, raw_token).
    """
    from datetime import timedelta
    from apps.sales.models import SalesProposalClientToken
    from apps.sales.token_utils import generate_raw_client_proposal_token, hash_client_proposal_token

    _assert_proposal_internally_approved_for_client_send(proposal)
    _assert_proposal_breakup_lines_mapped(proposal)
    if not recipient_email:
        raise ValueError('recipient_email is required.')

    recipient_email = recipient_email.strip().lower()
    _revoke_active_client_tokens(proposal)

    raw_token = generate_raw_client_proposal_token()
    expires_at = timezone.now() + timedelta(days=expires_days)
    token_record = SalesProposalClientToken.objects.create(
        proposal_version=proposal,
        lead=proposal.lead,
        token_hash=hash_client_proposal_token(raw_token),
        recipient_email=recipient_email,
        expires_at=expires_at,
        created_by=actor,
    )
    return token_record, raw_token


def validate_client_proposal_token(raw_token, *, touch_access=False):
    """
    Resolve raw token to (SalesProposalClientToken, ProposalVersion).

    Raises ClientProposalTokenError when invalid, expired, revoked, or used.
    """
    from apps.sales.exceptions import ClientProposalTokenError
    from apps.sales.models import SalesProposalClientToken
    from apps.sales.token_utils import hash_client_proposal_token

    if not raw_token or not str(raw_token).strip():
        raise ClientProposalTokenError('Token is required.', code='invalid_token')

    token_hash = hash_client_proposal_token(str(raw_token).strip())
    token_record = (
        SalesProposalClientToken.objects
        .select_related('proposal_version', 'proposal_version__lead', 'lead')
        .filter(token_hash=token_hash)
        .first()
    )
    if token_record is None:
        raise ClientProposalTokenError('Invalid or unknown token.', code='invalid_token')
    if token_record.is_revoked:
        raise ClientProposalTokenError('This link has been revoked.', code='revoked')
    if token_record.used_at is not None:
        raise ClientProposalTokenError('This link has already been used.', code='used')
    if token_record.expires_at <= timezone.now():
        raise ClientProposalTokenError('This link has expired.', code='expired')

    proposal = token_record.proposal_version
    if proposal.internal_approval_status != 'approved':
        raise ClientProposalTokenError(
            'This proposal is not available for client review.',
            code='not_approved',
        )

    if touch_access:
        token_record.last_accessed_at = timezone.now()
        token_record.save(update_fields=['last_accessed_at'])

    return token_record, proposal


def _proposal_already_responded(proposal):
    return proposal.client_approval_status not in ('not_sent', 'pending')


@transaction.atomic
def send_proposal_to_client(
    proposal, actor, *,
    recipient_email=None,
    recipient_name=None,
    expires_days=7,
    email_subject='',
    email_body='',
):
    """
    Email a secure client response link and mark the proposal as sent.

    Does not update proposal status if email delivery fails.
    Returns dict with proposal, token_expires_at, email_sent, recipient_email.
    """
    from apps.sales.email_services import send_proposal_client_link_email
    from apps.sales.models import ClientProposalResponse

    _assert_proposal_internally_approved_for_client_send(proposal)
    if proposal.status not in ('internally_approved', 'sent_to_client'):
        raise ValueError(f"Cannot send from proposal status '{proposal.status}'.")
    if _proposal_already_responded(proposal):
        raise ValueError('Client has already responded to this proposal version.')

    lead = proposal.lead
    email = (recipient_email or lead.client_email or '').strip().lower()
    if not email:
        raise ValueError('recipient_email is required (lead has no client_email).')

    name = recipient_name or lead.client_contact_person or ''

    token_record, raw_token = create_client_proposal_token(
        proposal, email, actor, expires_days=expires_days,
    )
    if name and not token_record.recipient_name:
        token_record.recipient_name = name
        token_record.save(update_fields=['recipient_name'])

    try:
        email_result = send_proposal_client_link_email(
            proposal=proposal,
            recipient_email=email,
            recipient_name=name,
            raw_token=raw_token,
            expires_at=token_record.expires_at,
            email_subject=email_subject,
            email_body=email_body,
        )
    except Exception as exc:
        token_record.is_revoked = True
        token_record.save(update_fields=['is_revoked'])
        raise ValueError(f'Failed to send proposal email: {exc}') from exc

    token_record.email_subject = email_result['subject']
    token_record.email_body = _redact_client_proposal_token(email_result['body'], raw_token)
    token_record.save(update_fields=['email_subject', 'email_body'])

    now = timezone.now()
    proposal.status = 'sent_to_client'
    proposal.client_approval_status = 'pending'
    proposal.sent_to_client_at = now
    proposal.save(update_fields=['status', 'client_approval_status', 'sent_to_client_at', 'updated_at'])
    lead.current_stage = 'sent_to_client'
    lead.save(update_fields=['current_stage', 'updated_at'])

    cpr = ClientProposalResponse.objects.filter(
        proposal_version=proposal,
    ).order_by('-created_at').first()
    if cpr is None or cpr.client_response != 'pending':
        ClientProposalResponse.objects.create(
            lead=lead,
            proposal_version=proposal,
            sent_to_client_by=actor,
            sent_to_client_at=now,
            client_response='pending',
        )
    else:
        cpr.sent_to_client_by = actor
        cpr.sent_to_client_at = now
        cpr.save(update_fields=['sent_to_client_by', 'sent_to_client_at', 'updated_at'])

    proposal.refresh_from_db()
    log_sales_activity(
        lead=lead,
        activity_type='proposal_sent_to_client',
        title=f'Proposal v{proposal.version_number} sent to client',
        actor=actor,
        proposal_version=proposal,
        metadata={
            'recipient_email': email,
            'proposal_version_id': proposal.pk,
            'email_subject': token_record.email_subject,
            'email_body': token_record.email_body,
        },
    )
    return {
        'proposal': proposal,
        'token_expires_at': token_record.expires_at,
        'email_sent': True,
        'recipient_email': email,
        'email_subject': token_record.email_subject,
        'email_body': token_record.email_body,
    }


_RESPONSE_TO_APPROVAL = {
    'approved': 'approved',
    'rejected': 'rejected',
    'negotiation_required': 'negotiation',
    'revision_required': 'revision_required',
    'pending': 'pending',
}

_RESPONSE_TO_PROPOSAL_STATUS = {
    'approved': 'client_approved',
    'rejected': 'client_rejected',
    'negotiation_required': 'client_negotiation',
    'revision_required': 'client_revision_required',
}

_RESPONSE_TO_STAGE = {
    'approved': ('client_approved', 'approved'),
    'rejected': ('client_rejected', 'rejected'),
    'negotiation_required': ('client_negotiation', 'active'),
    'revision_required': ('client_revision_required', 'active'),
}


def record_client_response(
    proposal, response, remarks, user,
    responded_by_name='', responded_by_email='',
    next_action_due_date=None, meeting_notes='',
):
    """Record client's decision on a sent proposal (authenticated sales path)."""
    if _proposal_already_responded(proposal):
        raise ValueError('Client has already responded to this proposal version.')
    return _apply_client_response(
        proposal, response, remarks,
        responded_by_name=responded_by_name,
        responded_by_email=responded_by_email,
        next_action_due_date=next_action_due_date,
        meeting_notes=meeting_notes,
    )


@transaction.atomic
def record_public_client_response(
    raw_token, response, remarks,
    respondent_name='', respondent_email='',
):
    """
    Record external client response via secure token (no auth).

    Marks token used_at and updates proposal/lead; does not mutate budget lines.
    """
    from apps.sales.exceptions import ClientProposalTokenError

    token_record, proposal = validate_client_proposal_token(raw_token)
    if _proposal_already_responded(proposal):
        raise ClientProposalTokenError(
            'A response has already been recorded for this proposal.',
            code='already_responded',
        )

    _apply_client_response(
        proposal, response, remarks,
        responded_by_name=respondent_name,
        responded_by_email=respondent_email,
    )
    now = timezone.now()
    token_record.used_at = now
    token_record.last_accessed_at = now
    token_record.save(update_fields=['used_at', 'last_accessed_at'])

    return {
        'proposal_version_id': proposal.pk,
        'client_response': response,
        'client_approval_status': proposal.client_approval_status,
        'proposal_status': proposal.status,
        'responded_at': now,
    }


def _apply_client_response(
    proposal, response, remarks,
    responded_by_name='', responded_by_email='',
    next_action_due_date=None, meeting_notes='',
):
    from apps.sales.models import ClientProposalResponse

    valid = {'approved', 'rejected', 'negotiation_required', 'revision_required'}
    if response not in valid:
        raise ValueError(f'Invalid client response "{response}".')

    now = timezone.now()
    cpr = ClientProposalResponse.objects.filter(
        proposal_version=proposal, client_response='pending',
    ).order_by('-created_at').first()
    if cpr is None:
        cpr = ClientProposalResponse.objects.create(
            lead=proposal.lead,
            proposal_version=proposal,
            client_response='pending',
            sent_to_client_at=proposal.sent_to_client_at,
        )
    cpr.client_response = response
    cpr.client_remarks = remarks
    cpr.responded_at = now
    cpr.responded_by_name = responded_by_name
    cpr.responded_by_email = responded_by_email
    cpr.next_action_due_date = next_action_due_date
    cpr.meeting_notes = meeting_notes
    cpr.save(update_fields=[
        'client_response', 'client_remarks', 'responded_at',
        'responded_by_name', 'responded_by_email',
        'next_action_due_date', 'meeting_notes', 'updated_at',
    ])

    proposal_update_fields = [
        'client_approval_status', 'client_remarks', 'status', 'updated_at',
    ]
    proposal.client_approval_status = _RESPONSE_TO_APPROVAL[response]
    proposal.client_remarks = remarks
    proposal.status = _RESPONSE_TO_PROPOSAL_STATUS[response]
    if response == 'approved':
        proposal.client_approved_at = now
        proposal.is_final_approved_version = True
        proposal_update_fields.extend(['client_approved_at', 'is_final_approved_version'])
    proposal.save(update_fields=proposal_update_fields)

    lead = proposal.lead
    stage, lead_status = _RESPONSE_TO_STAGE[response]
    lead.current_stage = stage
    lead.current_status = lead_status
    lead.save(update_fields=['current_stage', 'current_status', 'updated_at'])
    log_sales_activity(
        lead=lead,
        activity_type='client_response_received',
        title=f'Client response: {response}',
        message=remarks or '',
        proposal_version=proposal,
        metadata={'response': response, 'proposal_version_id': proposal.pk},
    )
    return proposal


def mark_lead_won_from_client_approval(lead, proposal, user):
    """Lock proposal and mark lead as won after client approval."""
    if proposal.client_approval_status != 'approved':
        raise ValueError("Can only mark won when client has approved a proposal.")
    now = timezone.now()
    proposal.status = 'locked'
    proposal.locked_at = now
    proposal.is_final_approved_version = True
    proposal.save(update_fields=['status', 'locked_at', 'is_final_approved_version', 'updated_at'])
    lead.final_approved_proposal = proposal
    lead.current_stage = 'won'
    lead.current_status = 'won'
    lead.converted_on = now
    lead.save(update_fields=[
        'final_approved_proposal', 'current_stage', 'current_status', 'converted_on', 'updated_at',
    ])
    log_sales_activity(
        lead=lead,
        activity_type='lead_won',
        title='Lead marked as won',
        actor=user,
        proposal_version=proposal,
        metadata={'proposal_version_id': proposal.pk},
    )
    return lead


# ─── Proposal Generation ─────────────────────────────────────────────────────

_PROPOSAL_ALLOWED_STAGES = frozenset([
    'site_survey_completed', 'sales_review', 'budget_generated',
    'internal_approval', 'internally_approved',
])


@transaction.atomic
def generate_proposal_version(lead, user, source_version=None):
    """
    Create a new ProposalVersion from active SalesRoleRequirements with calculated
    budget/breakup lines (see apps.sales.proposal_calculation).

    Guardrails:
    - Lead must be past site_survey stage.
    - Every active SalesLeadSite must have a completed SiteSurvey.
    - Each active site must have at least one active SalesRoleRequirement with manpower_count > 0.
    - Each requirement needs site.location_area + wage_category with a matching MinimumWageRate.
    """
    from apps.sales.models import (
        ProposalVersion, SalesRoleRequirement, SalesLeadSite, SiteSurvey,
    )
    from apps.sales.proposal_calculation import (
        calculate_proposal_for_lead,
        DEFAULT_MANAGEMENT_FEE_PERCENT,
    )

    if lead.current_stage not in _PROPOSAL_ALLOWED_STAGES:
        raise ValueError(
            f"Cannot generate proposal from stage '{lead.current_stage}'. "
            f"Lead must be past site survey stage."
        )

    active_sites = list(SalesLeadSite.objects.filter(lead=lead, is_active=True))
    if not active_sites:
        raise ValueError("Lead has no active sites; cannot generate a proposal.")

    for site in active_sites:
        survey = SiteSurvey.objects.filter(lead=lead, site=site).first()
        if not survey or survey.status != 'completed':
            raise ValueError(
                f"Site '{site.site_name}' does not have a completed survey. "
                f"All active sites must be surveyed before generating a proposal."
            )
        has_rr = SalesRoleRequirement.objects.filter(
            lead=lead, site=site, is_active=True, manpower_count__gt=0,
        ).exists()
        if not has_rr:
            raise ValueError(
                f"Site '{site.site_name}' has no active role requirements. "
                f"Add at least one role requirement per site."
            )

    last = ProposalVersion.objects.filter(lead=lead).order_by('-version_number').first()
    next_version_number = (last.version_number + 1) if last else 1

    mgmt_pct = DEFAULT_MANAGEMENT_FEE_PERCENT
    gst_applicable = False
    if source_version is not None:
        mgmt_pct = source_version.management_fee_percent or mgmt_pct
        gst_applicable = source_version.gst_applicable

    proposal = ProposalVersion.objects.create(
        lead=lead,
        version_number=next_version_number,
        created_by=user,
        source_version=source_version,
        status='generated',
        internal_approval_status='not_started',
        client_approval_status='not_sent',
        grand_total=0,
        subtotal_amount=0,
        management_fee_amount=0,
        gst_amount=0,
        manpower_total=0,
        management_fee_percent=mgmt_pct,
        gst_applicable=gst_applicable,
    )

    calculate_proposal_for_lead(lead, proposal, force=True)

    lead.current_stage = 'budget_generated'
    lead.save(update_fields=['current_stage', 'updated_at'])
    log_sales_activity(
        lead=lead,
        activity_type='proposal_generated',
        title=f'Proposal v{proposal.version_number} generated',
        actor=user,
        proposal_version=proposal,
        metadata={'proposal_version_id': proposal.pk, 'version_number': proposal.version_number},
    )
    return proposal


# ─── Proposal Versioning ─────────────────────────────────────────────────────

def validate_proposal_ready_for_mobilisation_conversion(lead, proposal):
    """Guardrails before sales lead → mobilisation conversion."""
    from apps.sales.models import ProposalVersion

    if proposal.internal_approval_status != 'approved':
        raise ValueError('Proposal must be internally approved.')
    if proposal.client_approval_status != 'approved':
        raise ValueError('Proposal must be client-approved.')
    if not proposal.is_final_approved_version:
        raise ValueError('Proposal must be marked as the final approved version.')
    latest = (
        ProposalVersion.objects.filter(lead=lead).order_by('-version_number').first()
    )
    if latest is None or latest.pk != proposal.pk:
        raise ValueError('Only the latest proposal version can be converted to mobilisation.')


SUPPORTED_LEAD_TYPES = frozenset({'new_client', 'site_expansion', 'scope_expansion', 'renewal'})


def _validate_lead_type_constraints(lead):
    """Validate lead_type/existing_client consistency before conversion."""
    if lead.lead_type not in SUPPORTED_LEAD_TYPES:
        raise ValueError(
            f"Unsupported lead_type '{lead.lead_type}'. "
            f"Expected one of: {sorted(SUPPORTED_LEAD_TYPES)}."
        )
    if lead.lead_type == 'renewal':
        raise ValueError(
            'renewal conversion is not implemented yet. '
            'Clone or extend existing contract workflow required.'
        )
    if lead.lead_type == 'new_client' and lead.existing_client_id is not None:
        raise ValueError('new_client lead must not have an existing_client set.')
    if lead.lead_type == 'site_expansion':
        if lead.existing_client_id is None:
            raise ValueError('site_expansion lead requires existing_client to be set.')
        if lead.existing_client.org_id != lead.org_id:
            raise ValueError('existing_client must belong to the same org as the lead.')
    if lead.lead_type == 'scope_expansion':
        if lead.existing_client_id is None:
            raise ValueError('scope_expansion lead requires existing_client to be set.')
        if lead.existing_client.org_id != lead.org_id:
            raise ValueError('existing_client must belong to the same org as the lead.')


def convert_won_sales_lead_to_onboarding_setup(lead, user, proposal=None, operations_owner=None):
    """
    Atomically convert a won SalesLead into a MobilisationSetupRequest.

    For new_client: creates Client, SiteProfiles, SRRs, BudgetPlan, then request.
    For site_expansion: reuses existing_client, creates new SiteProfiles/SRRs/BudgetPlan only.
    For scope_expansion: reuses existing_client and matching SiteProfiles by name; creates
        only new SRRs/BudgetPlan for the additional scope.
    For renewal: raises ValueError (not implemented).

    Idempotent: returns the existing request if one already exists for this lead.
    Raises ValueError if conversion guardrails are not met.
    """
    from apps.mobilisation.models import MobilisationSetupRequest
    from apps.mobilisation.services import (
        assign_operations_owner,
        ensure_primary_contact_proposed_user,
    )

    existing = MobilisationSetupRequest.objects.filter(source_sales_lead=lead).first()
    if existing:
        if operations_owner is not None and existing.assigned_operations_owner_id is None:
            assign_operations_owner(existing, operations_owner, actor=user)
        return existing

    proposal = proposal or lead.final_approved_proposal
    if proposal is None:
        raise ValueError('Lead has no final approved proposal to convert.')

    validate_proposal_ready_for_mobilisation_conversion(lead, proposal)

    if lead.current_stage not in ('won', 'client_approved'):
        raise ValueError(
            'Lead must be client-approved or won before converting to mobilisation.'
        )

    _validate_lead_type_constraints(lead)

    with transaction.atomic():
        if lead.lead_type == 'new_client':
            client = _create_client_from_lead(lead, user, proposal)
            site_map = _create_sites_from_lead(lead, client, user, proposal)
            _create_srrs_from_lead(lead, site_map, proposal)
            _create_budget_from_proposal_explicit(
                lead, proposal, client, user,
                code=f"bp-new-client-l{lead.pk}-p{proposal.pk}",
                name=f"New Client Budget — {client.name} — Lead #{lead.pk} (Proposal v{proposal.version_number})",
            )
            mob_type = 'new_client'
        elif lead.lead_type == 'site_expansion':
            client = lead.existing_client
            _guard_no_duplicate_site_codes(lead, client)
            site_map = _create_sites_from_lead(lead, client, user, proposal)
            _create_srrs_from_lead(lead, site_map, proposal)
            _create_budget_from_proposal_explicit(
                lead, proposal, client, user,
                code=f"bp-site-exp-l{lead.pk}-p{proposal.pk}",
                name=f"Site Expansion Budget — {client.name} — Lead #{lead.pk} (Proposal v{proposal.version_number})",
            )
            mob_type = 'new_site_expansion'
        else:
            # scope_expansion: reuse existing client + existing sites where name matches
            client = lead.existing_client
            site_map = _resolve_or_create_sites_for_scope_expansion(lead, client, user, proposal)
            _create_srrs_from_lead(lead, site_map, proposal)
            _create_budget_from_proposal_explicit(
                lead, proposal, client, user,
                code=f"bp-scope-exp-l{lead.pk}-p{proposal.pk}",
                name=f"Scope Expansion Budget — {client.name} — Lead #{lead.pk} (Proposal v{proposal.version_number})",
            )
            mob_type = 'scope_expansion'

        onboarding_request = MobilisationSetupRequest.objects.create(
            org=lead.org,
            client=client,
            requested_by=user,
            mobilisation_type=mob_type,
            source_sales_lead=lead,
            source_proposal_version=proposal,
            status='draft',
        )
        ensure_primary_contact_proposed_user(onboarding_request)
        if operations_owner is not None:
            assign_operations_owner(onboarding_request, operations_owner, actor=user)

        log_sales_activity(
            lead=lead,
            activity_type='converted',
            title='Lead converted to mobilisation',
            actor=user,
            proposal_version=proposal,
            metadata={'mobilisation_request_id': onboarding_request.pk, 'mob_type': mob_type},
        )
        log_sales_activity(
            lead=lead,
            activity_type='mobilisation_created',
            title=f'Mobilisation setup request created ({mob_type})',
            actor=user,
            proposal_version=proposal,
            metadata={'mobilisation_request_id': onboarding_request.pk},
        )

    return onboarding_request


def _guard_no_duplicate_site_codes(lead, client):
    """Block site_expansion if any generated site code already exists under the client."""
    from apps.sales.models import SalesLeadSite
    from apps.sites.models import SiteProfile

    for lead_site in SalesLeadSite.objects.filter(lead=lead, is_active=True):
        code = f"sts{lead_site.pk}"
        if SiteProfile.objects.filter(client=client, code=code).exists():
            raise ValueError(
                f"Site code '{code}' already exists under client '{client.name}'. "
                f"Cannot create duplicate site during site_expansion conversion."
            )


def _create_client_from_lead(lead, actor, proposal=None):
    from apps.core.models import ScopeNode
    from apps.sites.models import Client

    code = f"cls{lead.pk}"
    client = Client.objects.create(
        org=lead.org,
        name=lead.client_name,
        code=code,
        contact_name=lead.client_contact_person or '',
        contact_email=lead.client_email or '',
        contact_phone=lead.client_phone or '',
        created_by=actor,
        owner_sales_user=lead.sales_person or actor,
        is_active=True,
        source_type='sales_conversion',
        source_sales_lead=lead,
        source_proposal_version=proposal,
    )

    org_root = ScopeNode.objects.filter(
        org=lead.org, node_type='company', is_active=True,
    ).first()
    path = f"{org_root.path}/{code}" if org_root else code
    depth = (org_root.depth + 1) if org_root else 0

    scope_node = ScopeNode.objects.create(
        org=lead.org,
        parent=org_root,
        name=client.name,
        code=code,
        node_type='client',
        path=path,
        depth=depth,
        is_active=True,
    )
    client.scope_node = scope_node
    client.save(update_fields=['scope_node'])
    return client


def _create_sites_from_lead(lead, client, actor, proposal=None):
    from apps.core.models import ScopeNode
    from apps.sites.models import SiteProfile
    from apps.sales.models import SalesLeadSite

    site_map = {}
    client_node = client.scope_node

    for lead_site in SalesLeadSite.objects.filter(lead=lead, is_active=True):
        code = f"sts{lead_site.pk}"
        site = SiteProfile.objects.create(
            org=lead.org,
            client=client,
            name=lead_site.site_name,
            code=code,
            address=lead_site.site_address or '',
            city=lead_site.city or '',
            state=lead_site.state or '',
            created_by=actor,
            is_active=True,
            source_type='sales_conversion',
            source_sales_lead=lead,
            source_proposal_version=proposal,
        )

        if client_node:
            path = f"{client_node.path}/{code}"
            depth = client_node.depth + 1
            parent = client_node
        else:
            path = code
            depth = 0
            parent = None

        scope_node = ScopeNode.objects.create(
            org=lead.org,
            parent=parent,
            name=site.name,
            code=code,
            node_type='site',
            path=path,
            depth=depth,
            is_active=True,
        )
        site.scope_node = scope_node
        site.save(update_fields=['scope_node'])
        site_map[lead_site.pk] = site

    return site_map


def _create_srrs_from_lead(lead, site_map, proposal=None):
    from apps.sites.models import SiteRoleRequirement
    from apps.sales.models import ProposalBudgetLine, SalesRoleRequirement
    from datetime import date

    budget_line_by_role_requirement = {}
    if proposal is not None:
        budget_line_by_role_requirement = {
            line.role_requirement_id: line
            for line in ProposalBudgetLine.objects.filter(
                proposal_version=proposal,
                role_requirement__isnull=False,
            )
        }

    for req in SalesRoleRequirement.objects.filter(lead=lead, is_active=True).select_related(
        'job_role', 'wage_category', 'site',
    ):
        real_site = site_map.get(req.site_id)
        if real_site is None:
            continue
        budget_line = budget_line_by_role_requirement.get(req.pk)
        SiteRoleRequirement.objects.create(
            site=real_site,
            job_role=req.job_role,
            approved_headcount=req.manpower_count,
            billing_rate=budget_line.unit_cost if budget_line is not None else None,
            shift_hours=req.shift_hours,
            wage_category=req.wage_category,
            effective_from=date.today(),
            billing_type='billable',
            is_active=True,
            source_type='sales_conversion',
            source_sales_lead=lead,
            source_proposal_version=proposal,
        )


def _create_budget_from_proposal_explicit(lead, proposal, client, actor, *, code, name):
    """Create a BudgetPlan with an explicit code/name and full source audit stamps.

    Single budget-creation path used by all lead conversion branches (new_client,
    site_expansion, scope_expansion). Callers MUST pass an explicit, traceable
    `code` and `name` rather than relying on a generic default.
    """
    from apps.budgets.models import BudgetPlan
    from datetime import date

    return BudgetPlan.objects.create(
        org=lead.org,
        name=name,
        code=code,
        budget_nature='billable',
        budget_type='onboarding',
        client=client,
        amount=proposal.grand_total,
        currency='INR',
        period_start=date.today(),
        status='active',
        is_active=True,
        created_by=actor,
        source_type='sales_conversion',
        source_sales_lead=lead,
        source_proposal_version=proposal,
    )


def _resolve_or_create_sites_for_scope_expansion(lead, client, actor, proposal):
    """For scope_expansion: reuse an existing SiteProfile under `client` when the
    lead-site's name matches (case-insensitive); otherwise create a new SiteProfile.

    Returns a {SalesLeadSite.pk: SiteProfile} map ready for `_create_srrs_from_lead`.
    """
    from apps.core.models import ScopeNode
    from apps.sites.models import SiteProfile
    from apps.sales.models import SalesLeadSite

    site_map = {}
    client_node = client.scope_node

    for lead_site in SalesLeadSite.objects.filter(lead=lead, is_active=True):
        existing_site = SiteProfile.objects.filter(
            client=client, name__iexact=lead_site.site_name, is_active=True,
        ).first()
        if existing_site is not None:
            site_map[lead_site.pk] = existing_site
            continue

        # No matching site under this client → create a new SiteProfile
        code = f"sts{lead_site.pk}"
        site = SiteProfile.objects.create(
            org=lead.org,
            client=client,
            name=lead_site.site_name,
            code=code,
            address=lead_site.site_address or '',
            city=lead_site.city or '',
            state=lead_site.state or '',
            created_by=actor,
            is_active=True,
            source_type='sales_conversion',
            source_sales_lead=lead,
            source_proposal_version=proposal,
        )

        if client_node:
            path = f"{client_node.path}/{code}"
            depth = client_node.depth + 1
            parent = client_node
        else:
            path = code
            depth = 0
            parent = None

        scope_node = ScopeNode.objects.create(
            org=lead.org,
            parent=parent,
            name=site.name,
            code=code,
            node_type='site',
            path=path,
            depth=depth,
            is_active=True,
        )
        site.scope_node = scope_node
        site.save(update_fields=['scope_node'])
        site_map[lead_site.pk] = site

    return site_map


def clone_proposal_for_revision(proposal, user):
    """
    Clone an existing proposal into a new revision version.
    The source proposal is never mutated; locked/sent proposals are safe.
    """
    from apps.sales.models import ProposalVersion, ProposalBudgetLine, ProposalBreakupLine

    last = ProposalVersion.objects.filter(lead=proposal.lead).order_by('-version_number').first()
    next_version_number = (last.version_number + 1) if last else 1

    new_proposal = ProposalVersion.objects.create(
        lead=proposal.lead,
        version_number=next_version_number,
        created_by=user,
        source_version=proposal,
        status='draft',
        internal_approval_status='not_started',
        client_approval_status='not_sent',
        grand_total=proposal.grand_total,
        subtotal_amount=proposal.subtotal_amount,
        management_fee_amount=proposal.management_fee_amount,
        gst_amount=proposal.gst_amount,
        manpower_total=proposal.manpower_total,
        management_fee_percent=proposal.management_fee_percent,
        gst_applicable=proposal.gst_applicable,
        sales_remarks=proposal.sales_remarks,
    )

    for line in proposal.budget_lines.all():
        ProposalBudgetLine.objects.create(
            proposal_version=new_proposal,
            site=line.site,
            role_requirement=line.role_requirement,
            service_category=line.service_category,
            job_role=line.job_role,
            description=line.description,
            manpower_count=line.manpower_count,
            unit_cost=line.unit_cost,
            total_cost=line.total_cost,
            remarks=line.remarks,
            sort_order=line.sort_order,
            is_manual_override=line.is_manual_override,
            override_reason=line.override_reason,
            overridden_by=line.overridden_by,
            overridden_at=line.overridden_at,
        )

    for line in proposal.breakup_lines.all():
        ProposalBreakupLine.objects.create(
            proposal_version=new_proposal,
            site=line.site,
            role_requirement=line.role_requirement,
            job_role=line.job_role,
            component_name=line.component_name,
            component_type=line.component_type,
            percentage=line.percentage,
            amount=line.amount,
            remarks=line.remarks,
            sort_order=line.sort_order,
            is_manual_override=line.is_manual_override,
            override_reason=line.override_reason,
            overridden_by=line.overridden_by,
            overridden_at=line.overridden_at,
        )

    log_sales_activity(
        lead=proposal.lead,
        activity_type='proposal_revision_created',
        title=f'Proposal v{new_proposal.version_number} created as revision',
        actor=user,
        proposal_version=new_proposal,
        metadata={
            'new_version_id': new_proposal.pk,
            'source_version_id': proposal.pk,
            'version_number': new_proposal.version_number,
        },
    )
    return new_proposal


# ─── Phase H: Survey template seeding ─────────────────────────────────────────

# Fields on each child model that are TEMPLATE METADATA — safe to refresh when
# overwrite=True. Everything else is user-entered data and is never touched
# after creation.
_SCOPE_TEMPLATE_FIELDS = ('field_label', 'sort_order')
_SHIFT_TEMPLATE_FIELDS = ('line_type', 'sort_order')
_LOCATION_TEMPLATE_FIELDS = ('line_type', 'sort_order')
_EQUIPMENT_TEMPLATE_FIELDS = ('line_type', 'amortisation_months', 'sort_order')
_ISSUE_TEMPLATE_FIELDS = ('sort_order',)


def _refresh_template_fields(instance, expected, fields):
    """
    For each (name, value) in `expected`, if `name` is in `fields` and the
    stored value differs, update it. Returns True if anything changed.
    Never touches fields outside `fields` (i.e. user-entered data).
    """
    changed = False
    for name, value in expected.items():
        if name in fields and getattr(instance, name) != value:
            setattr(instance, name, value)
            changed = True
    return changed


@transaction.atomic
def seed_default_survey_lines(survey, overwrite=False):
    """
    Idempotently create the Excel-aligned default rows for `survey` across all
    5 child tables. Returns counts {table_key: {created, updated, existing}}.

    Rules:
      - `overwrite=False`: create only missing rows; never modify existing.
      - `overwrite=True` : also refresh template metadata (labels, sort_order,
        line_type, amortisation_months) on existing rows. User-entered fields
        (value_text, counts, amounts, totals, remarks, improvement_details)
        are NEVER overwritten, regardless of the flag.
    """
    from apps.sales.models import (
        SiteSurveyEquipmentLine,
        SiteSurveyIssueLine,
        SiteSurveyLocationLine,
        SiteSurveyScopeAnswer,
        SiteSurveyShiftDeployment,
    )
    from apps.sales.survey_templates import (
        ISSUE_ROWS,
        LOCATION_ROWS,
        MAJOR_EQUIPMENT_ROWS,
        MINOR_EQUIPMENT_ROWS,
        SCOPE_FIELDS,
        SHIFT_DEPLOYMENT_ROWS,
    )

    counts = {}

    # 1. Scope answers (5 categories)
    scope_created = scope_updated = scope_existing = 0
    for category, fields in SCOPE_FIELDS.items():
        for sort_order, (field_key, field_label) in enumerate(fields, start=1):
            expected = {'field_label': field_label, 'sort_order': sort_order}
            obj, was_created = SiteSurveyScopeAnswer.objects.get_or_create(
                survey=survey, category=category, field_key=field_key,
                defaults=expected,
            )
            if was_created:
                scope_created += 1
            elif overwrite and _refresh_template_fields(
                obj, expected, _SCOPE_TEMPLATE_FIELDS,
            ):
                obj.save(update_fields=list(_SCOPE_TEMPLATE_FIELDS) + ['updated_at'])
                scope_updated += 1
            else:
                scope_existing += 1
    counts['scope_answers'] = {
        'created': scope_created, 'updated': scope_updated, 'existing': scope_existing,
    }

    # 2. Shift / deployment rows
    sd_created = sd_updated = sd_existing = 0
    for sort_order, (description, line_type) in enumerate(SHIFT_DEPLOYMENT_ROWS, start=1):
        expected = {'line_type': line_type, 'sort_order': sort_order}
        obj, was_created = SiteSurveyShiftDeployment.objects.get_or_create(
            survey=survey, description=description, defaults=expected,
        )
        if was_created:
            sd_created += 1
        elif overwrite and _refresh_template_fields(
            obj, expected, _SHIFT_TEMPLATE_FIELDS,
        ):
            obj.save(update_fields=list(_SHIFT_TEMPLATE_FIELDS) + ['updated_at'])
            sd_updated += 1
        else:
            sd_existing += 1
    counts['shift_deployments'] = {
        'created': sd_created, 'updated': sd_updated, 'existing': sd_existing,
    }

    # 3. Location lines
    loc_created = loc_updated = loc_existing = 0
    for sort_order, (location_name, line_type) in enumerate(LOCATION_ROWS, start=1):
        expected = {'line_type': line_type, 'sort_order': sort_order}
        obj, was_created = SiteSurveyLocationLine.objects.get_or_create(
            survey=survey, location_name=location_name, defaults=expected,
        )
        if was_created:
            loc_created += 1
        elif overwrite and _refresh_template_fields(
            obj, expected, _LOCATION_TEMPLATE_FIELDS,
        ):
            obj.save(update_fields=list(_LOCATION_TEMPLATE_FIELDS) + ['updated_at'])
            loc_updated += 1
        else:
            loc_existing += 1
    counts['location_lines'] = {
        'created': loc_created, 'updated': loc_updated, 'existing': loc_existing,
    }

    # 4. Equipment lines (major + minor)
    eq_created = eq_updated = eq_existing = 0
    for category, rows in (('major', MAJOR_EQUIPMENT_ROWS), ('minor', MINOR_EQUIPMENT_ROWS)):
        for sort_order, (description, line_type, amort_months) in enumerate(rows, start=1):
            expected = {
                'line_type': line_type,
                'amortisation_months': amort_months,
                'sort_order': sort_order,
            }
            obj, was_created = SiteSurveyEquipmentLine.objects.get_or_create(
                survey=survey, equipment_category=category, description=description,
                defaults=expected,
            )
            if was_created:
                eq_created += 1
            elif overwrite and _refresh_template_fields(
                obj, expected, _EQUIPMENT_TEMPLATE_FIELDS,
            ):
                obj.save(update_fields=list(_EQUIPMENT_TEMPLATE_FIELDS) + ['updated_at'])
                eq_updated += 1
            else:
                eq_existing += 1
    counts['equipment_lines'] = {
        'created': eq_created, 'updated': eq_updated, 'existing': eq_existing,
    }

    # 5. Issue lines
    is_created = is_updated = is_existing = 0
    for sort_order, (issue, improvement_details) in enumerate(ISSUE_ROWS, start=1):
        expected = {'sort_order': sort_order}
        # improvement_details is template default at create time only; never refreshed.
        obj, was_created = SiteSurveyIssueLine.objects.get_or_create(
            survey=survey, issue=issue,
            defaults={**expected, 'improvement_details': improvement_details},
        )
        if was_created:
            is_created += 1
        elif overwrite and _refresh_template_fields(
            obj, expected, _ISSUE_TEMPLATE_FIELDS,
        ):
            obj.save(update_fields=list(_ISSUE_TEMPLATE_FIELDS) + ['updated_at'])
            is_updated += 1
        else:
            is_existing += 1
    counts['issue_lines'] = {
        'created': is_created, 'updated': is_updated, 'existing': is_existing,
    }

    return counts


# ─── Phase I: Survey → SalesRoleRequirement helper ────────────────────────────

SURVEY_ROLE_DENYLIST_DESCRIPTIONS = frozenset({
    'site management team',
    'technical(8 hrs x 6 days)',
    'machinery',
    'hk consumables',
    'housekeeping consumables',
    'sub total',
    'subtotal',
    'total',
    'grand total',
})


def _normalize_role_description(desc):
    return ' '.join((desc or '').strip().lower().split())


def _resolve_job_role_for_survey_row(org, description):
    """Try to resolve a JobRole by name (case-insensitive), then by code.

    Code lookup tries both the description as-is (lowercased, spaces -> '_') and
    the same with hyphens. Returns None when no match.
    """
    from apps.jobs.models import JobRole

    desc = (description or '').strip()
    if not desc:
        return None
    by_name = JobRole.objects.filter(
        org=org, name__iexact=desc, is_active=True,
    ).first()
    if by_name is not None:
        return by_name
    candidates = {
        desc.lower(),
        desc.lower().replace(' ', '_'),
        desc.lower().replace(' ', '-'),
    }
    return JobRole.objects.filter(
        org=org, code__in=list(candidates), is_active=True,
    ).first()


def _resolve_survey_role_mapping(org, description):
    from apps.sales.models import SurveyRoleMapping

    desc = (description or '').strip()
    if not desc:
        return None
    return SurveyRoleMapping.objects.select_related(
        'job_role', 'wage_category',
    ).filter(
        org=org,
        description_text__iexact=desc,
        is_active=True,
        job_role__is_active=True,
    ).first()


def _resolve_survey_role_defaults(org, row):
    """
    Resolve proposal-ready role defaults for a survey deployment row.

    Structured rows created from a JobRole do not need a description mapping.
    They still require a configured WageCategory matching the role's
    skill_category; otherwise generation fails with a clear reason.
    """
    if row.job_role_id:
        from types import SimpleNamespace
        from apps.sales.models import SurveyRoleMapping
        from apps.wages.models import WageCategory

        job_role = row.job_role
        if job_role is None or job_role.org_id != org.id or not job_role.is_active:
            return None, 'job_role_not_available'

        mapping = SurveyRoleMapping.objects.select_related(
            'job_role', 'wage_category',
        ).filter(
            org=org,
            job_role=job_role,
            is_active=True,
        ).first()
        if mapping is not None:
            return mapping, None

        wage_category = WageCategory.objects.filter(
            code=job_role.skill_category,
        ).first()
        if wage_category is None:
            return None, 'wage_category_not_found'

        return SimpleNamespace(
            job_role=job_role,
            wage_category=wage_category,
            service_category='',
            shift_hours=None,
            working_days=None,
        ), None

    mapping = _resolve_survey_role_mapping(org, row.description)
    if mapping is None:
        return None, 'role_mapping_not_found'
    return mapping, None


def _headcount_for_shift_row(row):
    """Prefer total_count; fall back to the sum of all shift buckets."""
    total = int(row.total_count or 0)
    if total > 0:
        return total
    sum_shifts = (
        int(row.general_count or 0)
        + int(row.first_shift_count or 0)
        + int(row.second_shift_count or 0)
        + int(getattr(row, 'night_shift_count', 0) or 0)
    )
    return sum_shifts


def _role_requirement_needs_sync(role_requirement, survey, mapping, headcount):
    return any((
        role_requirement.lead_id != survey.lead_id,
        role_requirement.site_id != survey.site_id,
        role_requirement.survey_id != survey.id,
        role_requirement.wage_category_id != mapping.wage_category_id,
        (role_requirement.service_category or '') != (mapping.service_category or ''),
        role_requirement.manpower_count != headcount,
        role_requirement.shift_hours != mapping.shift_hours,
        role_requirement.working_days != mapping.working_days,
        role_requirement.is_active is not True,
        role_requirement.created_from_survey is not True,
    ))


def _sync_generated_role_requirement(role_requirement, survey, mapping, headcount):
    role_requirement.site = survey.site
    role_requirement.wage_category = mapping.wage_category
    role_requirement.service_category = mapping.service_category
    role_requirement.manpower_count = headcount
    role_requirement.shift_hours = mapping.shift_hours
    role_requirement.working_days = mapping.working_days
    role_requirement.is_active = True
    role_requirement.created_from_survey = True
    role_requirement.save(update_fields=[
        'site',
        'wage_category',
        'service_category',
        'manpower_count',
        'shift_hours',
        'working_days',
        'is_active',
        'created_from_survey',
        'updated_at',
    ])
    return role_requirement


@transaction.atomic
def generate_role_requirements_from_survey(survey, actor):
    """
    Convert SiteSurveyShiftDeployment manpower rows into SalesRoleRequirement
    rows for the survey's lead and site.

    Returns: {'created': [...], 'updated': [...], 'skipped': [...], 'errors': [...]}.

    Each created entry is a dict with sales_role_requirement_id, description,
    job_role_id, manpower_count. Skipped entries include a 'reason' string.
    Errors entries include a 'reason' string and the offending description.

    Idempotent: rerunning with the same survey state leaves unchanged rows in
    `skipped` with reason 'already_exists'. If deployment values changed, the
    generated requirement is updated to match the current survey row.
    """
    from apps.sales.models import SalesRoleRequirement, SiteSurveyShiftDeployment

    created = []
    updated = []
    skipped = []
    errors = []

    rows = SiteSurveyShiftDeployment.objects.select_related('job_role').filter(
        survey=survey, line_type='item',
    ).order_by('sort_order', 'id')

    org = survey.lead.org

    for row in rows:
        normalized = _normalize_role_description(row.description)
        if not normalized:
            skipped.append({
                'description': row.description,
                'reason': 'empty_description',
            })
            continue

        if not row.is_applicable:
            skipped.append({
                'description': row.description,
                'reason': 'not_applicable',
            })
            continue

        if normalized in SURVEY_ROLE_DENYLIST_DESCRIPTIONS:
            skipped.append({
                'description': row.description,
                'reason': 'ignored_template_row',
            })
            continue

        mapping, error_reason = _resolve_survey_role_defaults(org, row)
        if mapping is None:
            errors.append({
                'description': row.description,
                'reason': error_reason,
            })
            continue
        job_role = mapping.job_role

        headcount = _headcount_for_shift_row(row)
        if headcount <= 0:
            skipped.append({
                'description': row.description,
                'reason': 'zero_headcount',
            })
            continue

        existing = SalesRoleRequirement.objects.filter(
            lead=survey.lead, survey=survey, job_role=job_role,
        ).first()
        if existing is not None:
            if _role_requirement_needs_sync(existing, survey, mapping, headcount):
                existing = _sync_generated_role_requirement(existing, survey, mapping, headcount)
                updated.append({
                    'description': row.description,
                    'sales_role_requirement_id': existing.pk,
                    'job_role_id': job_role.pk,
                    'wage_category_id': existing.wage_category_id,
                    'service_category': existing.service_category,
                    'manpower_count': headcount,
                    'used_mapping': True,
                })
                continue
            skipped.append({
                'description': row.description,
                'reason': 'already_exists',
                'sales_role_requirement_id': existing.pk,
            })
            continue

        srr = SalesRoleRequirement.objects.create(
            lead=survey.lead,
            site=survey.site,
            survey=survey,
            job_role=job_role,
            wage_category=mapping.wage_category,
            service_category=mapping.service_category,
            manpower_count=headcount,
            shift_hours=mapping.shift_hours,
            working_days=mapping.working_days,
            is_active=True,
            created_from_survey=True,
        )
        created.append({
            'description': row.description,
            'sales_role_requirement_id': srr.pk,
            'job_role_id': job_role.pk,
            'wage_category_id': srr.wage_category_id,
            'service_category': srr.service_category,
            'manpower_count': headcount,
            'used_mapping': True,
        })

    return {'created': created, 'updated': updated, 'skipped': skipped, 'errors': errors}

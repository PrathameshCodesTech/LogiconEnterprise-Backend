"""
apps/mobilisation/services.py

Finalization service for MobilisationSetupRequest.

All mobilisation is sales-led. Client/Sites/SRRs/BudgetPlan already exist
(created during convert_won_sales_lead_to_onboarding_setup). Finalization creates
client users from proposed setup records. Proposed departments remain as legacy
configuration data but are not required for client onboarding.
"""

import logging

from django.db import connection as _db_connection, transaction
from django.utils.text import slugify
from django.utils import timezone

from .exceptions import MobilisationFinalizationError

logger = logging.getLogger(__name__)


# ─── Readiness ────────────────────────────────────────────────────────────────

def check_mobilisation_readiness(request):
    """
    Return (ok, errors, warnings) for the given MobilisationSetupRequest.

    ok:       True iff there are no blocking errors.
    errors:   list of blocking issue strings.
    warnings: list of non-blocking advisory strings.
    """
    errors = []
    warnings = []
    _check_sales_led_mobilisation(request, errors, warnings)
    return not bool(errors), errors, warnings


def _check_sales_led_mobilisation(req, errors, warnings):
    if req.client_id is None:
        errors.append("Sales-led mobilisation requires a client to be set (created during lead conversion).")

    for dept in req.proposed_departments.filter(is_active=True, scope_level='site'):
        if dept.real_site_id is None:
            warnings.append(
                f"Legacy proposed department '{dept.code}' is site-level but has no real_site assigned."
            )
        elif req.client_id and dept.real_site.client_id != req.client_id:
            warnings.append(
                f"Legacy proposed department '{dept.code}' references a site from a different client."
            )

    user_count = req.proposed_users.filter(is_active=True).count()
    if user_count == 0:
        errors.append("Add at least one active proposed user.")
    else:
        has_primary = req.proposed_users.filter(is_active=True, is_primary_contact=True).exists()
        if not has_primary:
            warnings.append("No primary contact has been designated among proposed users.")
        for p_user in req.proposed_users.filter(is_active=True, scope_level='site'):
            if p_user.real_site_id is None:
                errors.append(
                    f"Proposed user '{p_user.email}' is site-level but has no real_site assigned."
                )
            elif req.client_id and p_user.real_site.client_id != req.client_id:
                errors.append(
                    f"Proposed user '{p_user.email}' references a site from a different client."
                )

    if req.client_id is not None:
        from apps.sites.models import SiteProfile
        site_count = SiteProfile.objects.filter(client_id=req.client_id, is_active=True).count()
        if site_count == 0:
            warnings.append("The client has no active sites yet.")



# ─── Preflight ────────────────────────────────────────────────────────────────

def validate_mobilisation_finalization_preflight(request) -> list:
    """
    Return a list of blocking error strings that would cause finalization to fail.

    Checks user emails, access roles, and real_site ownership.
    Returns [] when no blocking issues are found.
    """
    from apps.access.models import AccessRole
    from apps.accounts.models import User as UserModel

    errors = []

    for p_user in request.proposed_users.filter(is_active=True):
        if p_user.created_user_id is not None:
            continue
        email = (p_user.email or '').strip().lower()
        if email and UserModel.objects.filter(email=email).exists():
            errors.append(f"User email '{email}' already exists.")
        if p_user.access_role_id is not None:
            if not AccessRole.objects.filter(pk=p_user.access_role_id, is_active=True).exists():
                errors.append(f"Proposed user '{email}' has an inactive access role.")
        if p_user.scope_level == 'site':
            if p_user.real_site_id is None:
                errors.append(f"Proposed user '{email}' is site-level but has no real_site.")
            elif request.client_id and p_user.real_site.client_id != request.client_id:
                errors.append(f"Proposed user '{email}' references a site outside this client.")
    return errors


# ─── Public ───────────────────────────────────────────────────────────────────

def finalize_mobilisation_request(request, actor=None):
    """
    Create real client User records from the proposed setup data.

    Safe to call inside an existing transaction — uses a raw SAVEPOINT so that a
    finalization failure rolls back only the finalization work; the outer transaction
    (workflow approval, request status update) continues and commits normally.

    Idempotent: if already finalized, returns immediately.
    On failure: stores finalization_status='failed' and finalization_error on the request,
    logs the error, then raises MobilisationFinalizationError.
    """
    from .models import MobilisationSetupRequest

    if request.finalization_status == 'finalized':
        return

    sid = transaction.savepoint()
    try:
        _do_finalize(request, actor)
        transaction.savepoint_commit(sid)
    except Exception as exc:
        _db_connection.needs_rollback = False
        transaction.savepoint_rollback(sid)
        MobilisationSetupRequest.objects.filter(pk=request.pk).update(
            finalization_status='failed',
            finalization_error=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        logger.error(
            'Mobilisation finalization failed for request pk=%s after workflow approval: %s',
            request.pk,
            exc,
            exc_info=True,
        )
        raise MobilisationFinalizationError(str(exc)) from exc


def retry_finalize_mobilisation_request(request, actor=None):
    """
    Re-attempt finalization for a request whose finalization_status is 'failed'.
    Raises ValueError if the request is not in a retriable state.
    """
    if request.finalization_status == 'finalized':
        return
    if request.status != 'approved':
        raise ValueError(
            f"Cannot retry finalization: request status is '{request.status}', expected 'approved'."
        )
    from .models import MobilisationSetupRequest
    MobilisationSetupRequest.objects.filter(pk=request.pk).update(
        finalization_status='not_finalized',
        finalization_error='',
        updated_at=timezone.now(),
    )
    request.finalization_status = 'not_finalized'
    finalize_mobilisation_request(request, actor=actor)


# ─── Internal ─────────────────────────────────────────────────────────────────

def _do_finalize(request, actor):
    """
    Core finalization logic. Must be called inside a savepoint / transaction.
    Client/Sites/SRRs/BudgetPlan already exist (created during lead conversion).
    Creates only client users from proposed records. Departments and SRR
    department mappings are legacy setup data and are not materialized here.
    """
    client = request.client
    if client is None:
        raise MobilisationFinalizationError(
            "Mobilisation has no client attached. Ensure lead conversion completed successfully."
        )

    for p_user in request.proposed_users.filter(is_active=True).select_related(
        'access_role', 'real_site__scope_node',
    ):
        _create_proposed_user(p_user, request.org, client)

    request.finalization_status = 'finalized'
    request.finalized_at = timezone.now()
    request.finalized_by = actor
    request.save(update_fields=['finalization_status', 'finalized_at', 'finalized_by', 'updated_at'])


def _create_department(proposed_dept, client, real_site, org):
    from apps.core.models import Department

    return Department.objects.create(
        org=org,
        client=client,
        site=real_site,
        name=proposed_dept.name,
        code=proposed_dept.code,
        description=proposed_dept.description,
        is_active=True,
    )


def _generate_unique_username(email):
    base = email.split('@')[0][:140]
    from apps.accounts.models import User
    username = base
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base[:136]}_{counter}"
        counter += 1
    return username


def _create_proposed_user(p_user, org, client):
    from apps.accounts.models import User
    from apps.access.models import UserRoleAssignment

    if p_user.created_user_id is not None:
        return

    email = p_user.email.strip().lower()
    username = _generate_unique_username(email)
    parts = p_user.full_name.strip().split(' ', 1)
    first_name = parts[0][:150]
    last_name = parts[1][:150] if len(parts) > 1 else ''

    user = User(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        user_type='client',
        org=org,
        is_active=True,
    )
    user.set_unusable_password()
    user.save()

    if p_user.scope_level == 'site' and p_user.real_site_id:
        scope_node = p_user.real_site.scope_node
    else:
        scope_node = client.scope_node

    if scope_node is None:
        raise MobilisationFinalizationError(
            f"Cannot assign role for proposed user pk={p_user.pk}: scope node is not set."
        )

    UserRoleAssignment.objects.get_or_create(
        user=user,
        role=p_user.access_role,
        scope_node=scope_node,
    )

    if p_user.send_invite_on_finalization:
        try:
            from apps.accounts.services import send_user_invite_email
            send_user_invite_email(user, context_label='Client portal')
            invite_status = 'sent'
            invite_error = ''
        except Exception as exc:
            logger.warning(
                'Invite email failed for proposed user pk=%s (user pk=%s); '
                'finalization continues.',
                p_user.pk, user.pk,
                exc_info=True,
            )
            invite_status = 'failed'
            invite_error = str(exc)[:2000]
    else:
        invite_status = 'not_required'
        invite_error = ''

    from .models import MobilisationProposedUser
    MobilisationProposedUser.objects.filter(pk=p_user.pk).update(
        created_user=user,
        invite_status=invite_status,
        invite_error=invite_error,
    )


def resend_mobilisation_proposed_user_invite(proposed_user, actor=None):
    """
    Re-send the invite email for a finalized proposed user.

    Raises ValueError for invalid states; raises on SMTP failure so the caller
    can surface the error to the API consumer.
    """
    from apps.accounts.services import send_user_invite_email

    if proposed_user.created_user_id is None:
        raise ValueError("Cannot resend invite: user has not been finalized yet.")

    user = proposed_user.created_user
    from .models import MobilisationProposedUser
    try:
        send_user_invite_email(user, context_label='Client portal')
    except Exception as exc:
        MobilisationProposedUser.objects.filter(pk=proposed_user.pk).update(
            invite_status='failed',
            invite_error=str(exc)[:2000],
        )
        raise

    MobilisationProposedUser.objects.filter(pk=proposed_user.pk).update(
        invite_status='sent',
        invite_error='',
    )
    logger.info(
        'Resent invite email for proposed user pk=%s (user pk=%s), actor pk=%s',
        proposed_user.pk, user.pk, getattr(actor, 'pk', None),
    )


def validate_operations_owner(org, owner):
    """Validate an explicit operations owner chosen for mobilisation setup."""
    if owner is None:
        raise ValueError("operations_owner is required.")
    if owner.org_id != org.id:
        raise ValueError("Operations owner must belong to the same organization.")
    if not owner.is_active:
        raise ValueError("Operations owner is inactive.")
    if owner.user_type != 'internal':
        raise ValueError("Operations owner must be an internal user.")


@transaction.atomic
def assign_operations_owner(request, owner, actor=None):
    """
    Explicitly hand a mobilisation setup request to operations.

    Nothing is auto-assigned from the sales lead. The caller must pass the user
    selected by sales/management.
    """
    validate_operations_owner(request.org, owner)
    if request.status not in ('draft', 'operations_setup', 'rejected'):
        raise ValueError(
            f"Cannot assign operations owner when request status is '{request.status}'."
        )

    now = timezone.now()
    request.assigned_operations_owner = owner
    request.status = 'operations_setup'
    if request.submitted_to_operations_at is None:
        request.submitted_to_operations_at = now
    request.save(update_fields=[
        'assigned_operations_owner', 'status', 'submitted_to_operations_at', 'updated_at',
    ])
    try:
        from apps.notifications.services import create_notification
        create_notification(
            recipient=owner,
            actor=actor,
            org=request.org,
            title=f'Mobilisation assigned: {request.client.name}',
            message='Complete client users and setup readiness.',
            notification_type='mobilisation_operations_assigned',
            target_type='mobilisation',
            target_id=request.pk,
            target_url=f'/mobilisation/{request.pk}',
            metadata={'client_id': request.client_id},
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'Failed to notify mobilisation owner request_id=%s', request.pk,
        )
    return request


def _requires_setup_completion(request):
    """Sales-led mobilisation must pass operations setup before finalization."""
    return request.source_sales_lead_id is not None


def assert_setup_completed_for_finalization(request):
    if _requires_setup_completion(request) and request.status != 'setup_completed':
        raise ValueError(
            "Mobilisation setup must be completed by operations before finalization."
        )


@transaction.atomic
def mark_mobilisation_setup_completed(request, actor):
    if request.status != 'operations_setup':
        raise ValueError(
            f"Cannot mark setup completed when request status is '{request.status}'."
        )
    if request.assigned_operations_owner_id is None:
        raise ValueError("Assign an operations owner before completing setup.")
    if (
        actor is not None
        and not getattr(actor, 'is_superuser', False)
        and request.assigned_operations_owner_id != getattr(actor, 'id', None)
    ):
        raise ValueError("Only the assigned operations owner can complete this setup.")

    ok, errors, warnings = check_mobilisation_readiness(request)
    if not ok:
        raise ValueError(
            f"Mobilisation setup is incomplete: {'; '.join(errors)}"
        )

    now = timezone.now()
    request.status = 'setup_completed'
    request.setup_completed_at = now
    request.setup_completed_by = actor
    request.save(update_fields=[
        'status', 'setup_completed_at', 'setup_completed_by', 'updated_at',
    ])
    recipient = request.requested_by
    if recipient is not None:
        try:
            from apps.notifications.services import create_notification
            create_notification(
                recipient=recipient,
                actor=actor,
                org=request.org,
                title=f'Mobilisation setup completed: {request.client.name}',
                message='Operations setup is ready for approval or finalization.',
                notification_type='mobilisation_setup_completed',
                target_type='mobilisation',
                target_id=request.pk,
                target_url=f'/mobilisation/{request.pk}',
                metadata={'client_id': request.client_id},
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                'Failed to notify mobilisation setup completion request_id=%s',
                request.pk,
            )
    return request, warnings


def _clean_code(value, *, fallback):
    code = slugify(value or '')[:64].strip('-')
    return code or fallback


def _site_department_code(prefix, site):
    base = _clean_code(getattr(site, 'code', '') or getattr(site, 'name', ''), fallback=str(site.pk))
    return f"{prefix}-{base}"[:64].strip('-')


def _department_exists(request, *, scope_level, code, real_site=None):
    qs = request.proposed_departments.filter(
        is_active=True,
        scope_level=scope_level,
        code=code,
    )
    if real_site is None:
        qs = qs.filter(real_site__isnull=True)
    else:
        qs = qs.filter(real_site=real_site)
    return qs.exists()


def _user_exists(request, *, email):
    return bool(email) and request.proposed_users.filter(
        is_active=True,
        email__iexact=email,
    ).exists()


def _department_suggestion(request, *, key, scope_level, name, code, description, real_site=None):
    exists = _department_exists(
        request,
        scope_level=scope_level,
        code=code,
        real_site=real_site,
    )
    return {
        'key': key,
        'scope_level': scope_level,
        'real_site': real_site.pk if real_site else None,
        'real_site_name': real_site.name if real_site else None,
        'name': name,
        'code': code,
        'description': description,
        'exists': exists,
        'can_apply': not exists,
        'reason': 'already_exists' if exists else '',
    }


def _client_admin_role(request):
    from apps.access.models import AccessRole
    return AccessRole.objects.filter(
        org=request.org,
        code='client_admin',
        is_active=True,
    ).first()


def ensure_primary_contact_proposed_user(request):
    """
    Ensure the converted lead/client contact appears as the default proposed
    client portal user. Returns the proposed user, or None when required contact
    data or the client_admin role is missing.
    """
    if request.client_id is None:
        return None

    from .models import MobilisationProposedUser
    from apps.accounts.models import User

    lead = request.source_sales_lead
    full_name = ''
    email = ''
    phone = ''
    if lead is not None:
        full_name = lead.client_contact_person or request.client.contact_name or request.client.name
        email = lead.client_email or request.client.contact_email
        phone = lead.client_phone or request.client.contact_phone
    else:
        full_name = request.client.contact_name or request.client.name
        email = request.client.contact_email
        phone = request.client.contact_phone

    email = (email or '').strip().lower()
    if not email:
        return None

    access_role = _client_admin_role(request)
    if access_role is None:
        return None

    existing_user = User.objects.filter(org=request.org, email__iexact=email).first()
    if existing_user is not None:
        return None

    has_primary = request.proposed_users.filter(
        is_active=True,
        is_primary_contact=True,
    ).exists()
    proposed_user, created = MobilisationProposedUser.objects.get_or_create(
        request=request,
        email=email,
        defaults={
            'full_name': full_name or 'Client Primary Contact',
            'phone': phone or '',
            'user_type': 'client',
            'access_role': access_role,
            'scope_level': 'client',
            'real_site': None,
            'is_primary_contact': not has_primary,
            'send_invite_on_finalization': True,
            'is_active': True,
        },
    )
    changed_fields = []
    updates = {
        'full_name': full_name or proposed_user.full_name or 'Client Primary Contact',
        'phone': phone or proposed_user.phone or '',
        'access_role': access_role,
        'scope_level': 'client',
        'real_site': None,
        'is_active': True,
    }
    if created:
        return proposed_user
    for field, value in updates.items():
        current = getattr(proposed_user, f'{field}_id') if hasattr(value, 'pk') else getattr(proposed_user, field)
        expected = value.pk if hasattr(value, 'pk') else value
        if current != expected:
            setattr(proposed_user, field, value)
            changed_fields.append(field)
    if not has_primary and not proposed_user.is_primary_contact:
        proposed_user.is_primary_contact = True
        changed_fields.append('is_primary_contact')
    if changed_fields:
        proposed_user.save(update_fields=changed_fields + ['updated_at'])
    return proposed_user


def _user_suggestion(request, *, key, full_name, email, phone, access_role, scope_level='client', real_site=None):
    from apps.accounts.models import User

    email = (email or '').strip().lower()
    exists = _user_exists(request, email=email)
    reason = ''
    can_apply = True
    if exists:
        can_apply = False
        reason = 'already_exists'
    elif not email:
        can_apply = False
        reason = 'missing_email'
    elif access_role is None:
        can_apply = False
        reason = 'client_admin_role_missing'
    elif User.objects.filter(org=request.org, email__iexact=email).exists():
        can_apply = False
        reason = 'user_email_already_exists'

    return {
        'key': key,
        'full_name': full_name or 'Client Primary Contact',
        'email': email,
        'phone': phone or '',
        'user_type': 'client',
        'access_role': access_role.pk if access_role else None,
        'access_role_code': access_role.code if access_role else None,
        'access_role_name': access_role.name if access_role else None,
        'scope_level': scope_level,
        'real_site': real_site.pk if real_site else None,
        'real_site_name': real_site.name if real_site else None,
        'is_primary_contact': True,
        'send_invite_on_finalization': False,
        'exists': exists,
        'can_apply': can_apply,
        'reason': reason,
    }


def _department_kind_for_role(role):
    text = f"{getattr(role, 'code', '')} {getattr(role, 'name', '')}".lower()
    if any(token in text for token in ('housekeeping', 'hk', 'janitor')):
        return 'housekeeping', 'Housekeeping'
    if any(token in text for token in ('security', 'guard')):
        return 'security', 'Security'
    if any(token in text for token in ('tech', 'electrician', 'plumber', 'stp')):
        return 'technical', 'Technical'
    return None, None


def get_mobilisation_setup_suggestions(request):
    """
    Return backend-generated setup suggestions for operations.

    The suggestions are derived from the converted client, real sites, source
    sales lead, and converted site role requirements.
    """
    departments = []
    users = []

    if request.client_id is None:
        return {'departments': departments, 'users': users}

    departments.append(_department_suggestion(
        request,
        key='client-administration',
        scope_level='client',
        real_site=None,
        name='Client Administration',
        code='client-admin',
        description='Client-level administration, reporting, and account coordination.',
    ))

    sites = list(request.client.sites.filter(is_active=True).order_by('name', 'id'))
    for site in sites:
        departments.append(_department_suggestion(
            request,
            key=f'site-operations-{site.pk}',
            scope_level='site',
            real_site=site,
            name='Operations',
            code=_site_department_code('ops', site),
            description=f'Operations department for {site.name}.',
        ))

        seen_kinds = set()
        for srr in site.role_requirements.filter(is_active=True).select_related('job_role'):
            kind, label = _department_kind_for_role(srr.job_role)
            if not kind or kind in seen_kinds:
                continue
            seen_kinds.add(kind)
            departments.append(_department_suggestion(
                request,
                key=f'site-{kind}-{site.pk}',
                scope_level='site',
                real_site=site,
                name=label,
                code=_site_department_code(kind, site),
                description=f'{label} supervision and manpower coordination for {site.name}.',
            ))

    lead = request.source_sales_lead
    access_role = _client_admin_role(request)
    if lead is not None:
        users.append(_user_suggestion(
            request,
            key='client-primary-contact',
            full_name=lead.client_contact_person or request.client.contact_name,
            email=lead.client_email or request.client.contact_email,
            phone=lead.client_phone or request.client.contact_phone,
            access_role=access_role,
        ))
    elif request.client.contact_email:
        users.append(_user_suggestion(
            request,
            key='client-primary-contact',
            full_name=request.client.contact_name,
            email=request.client.contact_email,
            phone=request.client.contact_phone,
            access_role=access_role,
        ))

    return {'departments': departments, 'users': users}


def _active_site_role_requirements(request):
    if request.client_id is None:
        return []
    from apps.sites.models import SiteRoleRequirement
    return list(
        SiteRoleRequirement.objects.filter(
            site__client_id=request.client_id,
            site__is_active=True,
            is_active=True,
        ).select_related(
            'site', 'job_role', 'wage_category',
        ).order_by('site__name', 'job_role__name', 'id')
    )


def _role_requirement_payload(srr, assigned_department_id=None):
    return {
        'id': srr.pk,
        'site': srr.site_id,
        'site_name': srr.site.name if srr.site_id else None,
        'job_role': srr.job_role_id,
        'job_role_name': srr.job_role.name if srr.job_role_id else None,
        'job_role_code': srr.job_role.code if srr.job_role_id else None,
        'wage_category': srr.wage_category_id,
        'wage_category_name': srr.wage_category.name if srr.wage_category_id else None,
        'service_category': getattr(srr, 'service_category', '') or '',
        'approved_headcount': srr.approved_headcount,
        'shift_hours': str(srr.shift_hours) if srr.shift_hours is not None else None,
        'billing_type': srr.billing_type,
        'assigned_department': assigned_department_id,
    }


def get_mobilisation_setup_builder(request):
    """Return editable setup-builder payload with departments, users, and SRR assignments."""
    roles = _active_site_role_requirements(request)
    mapping_by_srr = {
        m.site_role_requirement_id: m
        for m in request.proposed_department_roles.filter(
            is_active=True,
            proposed_department__is_active=True,
        ).select_related('proposed_department')
    }
    departments = []
    for dept in request.proposed_departments.filter(is_active=True).select_related('real_site').order_by('sort_order', 'name', 'id'):
        dept_mappings = list(dept.role_mappings.filter(is_active=True).select_related(
            'site_role_requirement__site',
            'site_role_requirement__job_role',
            'site_role_requirement__wage_category',
        ).order_by('sort_order', 'id'))
        departments.append({
            'id': dept.pk,
            'scope_level': dept.scope_level,
            'real_site': dept.real_site_id,
            'real_site_name': dept.real_site.name if dept.real_site_id else None,
            'name': dept.name,
            'code': dept.code,
            'description': dept.description,
            'is_locked': dept.is_locked,
            'source_key': dept.source_key,
            'sort_order': dept.sort_order,
            'role_requirement_ids': [m.site_role_requirement_id for m in dept_mappings],
            'roles': [
                _role_requirement_payload(m.site_role_requirement, assigned_department_id=dept.pk)
                for m in dept_mappings
            ],
        })

    assigned_ids = set(mapping_by_srr.keys())
    return {
        'request': request.pk,
        'setup_strategy': request.setup_strategy,
        'departments': departments,
        'users': [
            {
                'id': u.pk,
                'full_name': u.full_name,
                'email': u.email,
                'phone': u.phone,
                'user_type': u.user_type,
                'access_role': u.access_role_id,
                'access_role_code': u.access_role.code if u.access_role_id else None,
                'access_role_name': u.access_role.name if u.access_role_id else None,
                'scope_level': u.scope_level,
                'real_site': u.real_site_id,
                'real_site_name': u.real_site.name if u.real_site_id else None,
                'is_primary_contact': u.is_primary_contact,
                'send_invite_on_finalization': u.send_invite_on_finalization,
                'is_active': u.is_active,
            }
            for u in request.proposed_users.filter(is_active=True).select_related('access_role', 'real_site').order_by('-is_primary_contact', 'full_name', 'id')
        ],
        'available_roles': [
            _role_requirement_payload(
                srr,
                assigned_department_id=(
                    mapping_by_srr[srr.pk].proposed_department_id
                    if srr.pk in mapping_by_srr else None
                ),
            )
            for srr in roles
        ],
        'unassigned_roles': [
            _role_requirement_payload(srr)
            for srr in roles
            if srr.pk not in assigned_ids
        ],
        'validation': {
            'ok': check_mobilisation_readiness(request)[0],
            'errors': check_mobilisation_readiness(request)[1],
            'warnings': check_mobilisation_readiness(request)[2],
        },
    }


def _ensure_department(request, *, source_key, scope_level, name, code, description='', real_site=None, is_locked=False, sort_order=0):
    from .models import MobilisationProposedDepartment

    dept = MobilisationProposedDepartment.objects.filter(
        request=request,
        source_key=source_key,
    ).first()
    if dept is None:
        dept = MobilisationProposedDepartment(
            request=request,
            source_key=source_key,
        )
    dept.scope_level = scope_level
    dept.real_site = real_site if scope_level == 'site' else None
    dept.name = name
    dept.code = code
    dept.description = description
    dept.is_locked = is_locked
    dept.is_active = True
    dept.sort_order = sort_order
    dept.save()
    return dept


def _ensure_client_admin_department(request):
    return _ensure_department(
        request,
        source_key='client-administration',
        scope_level='client',
        real_site=None,
        name='Client Administration',
        code='client-admin',
        description='Client-level administration, reporting, and account coordination.',
        is_locked=True,
        sort_order=10,
    )


def _department_for_role(request, *, strategy, site, srr):
    if strategy == 'simple':
        return _ensure_department(
            request,
            source_key=f'site-operations-{site.pk}',
            scope_level='site',
            real_site=site,
            name='Operations',
            code=_site_department_code('ops', site),
            description=f'Operations department for {site.name}.',
            sort_order=100 + site.pk,
        )

    kind, label = _department_kind_for_role(srr.job_role)
    if not kind:
        kind = 'ops'
        label = 'Operations'
    return _ensure_department(
        request,
        source_key=f'site-{kind}-{site.pk}',
        scope_level='site',
        real_site=site,
        name=label,
        code=_site_department_code(kind, site),
        description=(
            f'{label} supervision and manpower coordination for {site.name}.'
            if kind != 'ops'
            else f'Operations department for {site.name}.'
        ),
        sort_order=100 + site.pk,
    )


@transaction.atomic
def apply_mobilisation_setup_builder_template(request, strategy='role_grouped', actor=None):
    """Create/update proposed departments/users and SRR assignments from a setup strategy."""
    if request.status not in ('draft', 'operations_setup', 'rejected'):
        raise ValueError(
            f"Setup builder cannot be changed when the mobilisation request status is '{request.status}'."
        )
    if strategy not in ('simple', 'role_grouped', 'custom'):
        raise ValueError("setup_strategy must be one of: simple, role_grouped, custom.")

    from .models import MobilisationProposedDepartmentRole, MobilisationProposedUser
    from apps.sites.models import SiteProfile

    _ensure_client_admin_department(request)
    if strategy in ('simple', 'role_grouped'):
        MobilisationProposedDepartmentRole.objects.filter(request=request).delete()
        for idx, srr in enumerate(_active_site_role_requirements(request), start=1):
            dept = _department_for_role(
                request,
                strategy=strategy,
                site=srr.site,
                srr=srr,
            )
            MobilisationProposedDepartmentRole.objects.create(
                request=request,
                proposed_department=dept,
                site_role_requirement=srr,
                sort_order=idx,
                is_active=True,
            )

    access_role = _client_admin_role(request)
    lead = request.source_sales_lead
    if lead is not None:
        user_row = _user_suggestion(
            request,
            key='client-primary-contact',
            full_name=lead.client_contact_person or request.client.contact_name,
            email=lead.client_email or request.client.contact_email,
            phone=lead.client_phone or request.client.contact_phone,
            access_role=access_role,
        )
    elif request.client and request.client.contact_email:
        user_row = _user_suggestion(
            request,
            key='client-primary-contact',
            full_name=request.client.contact_name,
            email=request.client.contact_email,
            phone=request.client.contact_phone,
            access_role=access_role,
        )
    else:
        user_row = None

    if user_row and user_row['can_apply']:
        real_site = None
        if user_row['real_site'] is not None:
            real_site = SiteProfile.objects.get(pk=user_row['real_site'])
        MobilisationProposedUser.objects.get_or_create(
            request=request,
            email=user_row['email'],
            defaults={
                'full_name': user_row['full_name'],
                'phone': user_row['phone'],
                'user_type': user_row['user_type'],
                'access_role_id': user_row['access_role'],
                'scope_level': user_row['scope_level'],
                'real_site': real_site,
                'is_primary_contact': user_row['is_primary_contact'],
                'send_invite_on_finalization': user_row['send_invite_on_finalization'],
                'is_active': True,
            },
        )

    request.setup_strategy = strategy
    request.save(update_fields=['setup_strategy', 'updated_at'])
    return get_mobilisation_setup_builder(request)


@transaction.atomic
def save_mobilisation_setup_builder(request, payload, actor=None):
    """Persist a complete editable setup-builder payload."""
    if request.status not in ('draft', 'operations_setup', 'rejected'):
        raise ValueError(
            f"Setup builder cannot be changed when the mobilisation request status is '{request.status}'."
        )
    from .models import (
        MobilisationProposedDepartment,
        MobilisationProposedDepartmentRole,
        MobilisationProposedUser,
    )
    from apps.access.models import AccessRole
    from apps.sites.models import SiteProfile, SiteRoleRequirement

    strategy = payload.get('setup_strategy') or payload.get('strategy') or 'custom'
    if strategy not in ('simple', 'role_grouped', 'custom'):
        raise ValueError("setup_strategy must be one of: simple, role_grouped, custom.")

    locked_client_dept = _ensure_client_admin_department(request)
    kept_department_ids = {locked_client_dept.pk}
    role_assignments = {}

    for idx, row in enumerate(payload.get('departments') or [], start=1):
        row_id = row.get('id')
        if row_id:
            try:
                dept = MobilisationProposedDepartment.objects.get(request=request, pk=row_id)
            except MobilisationProposedDepartment.DoesNotExist as exc:
                raise ValueError(f"Unknown proposed department id {row_id}.") from exc
            if dept.is_locked:
                kept_department_ids.add(dept.pk)
                continue
        else:
            dept = MobilisationProposedDepartment(request=request)

        scope_level = row.get('scope_level') or dept.scope_level or 'site'
        real_site = None
        if scope_level == 'site':
            site_id = row.get('real_site')
            if not site_id:
                raise ValueError(f"Department '{row.get('name') or row.get('code')}' requires real_site.")
            real_site = SiteProfile.objects.get(pk=site_id)
            if request.client_id and real_site.client_id != request.client_id:
                raise ValueError("Department site must belong to the mobilisation client.")
        dept.scope_level = scope_level
        dept.real_site = real_site
        dept.name = (row.get('name') or '').strip()
        dept.code = (row.get('code') or '').strip()
        dept.description = (row.get('description') or '').strip()
        dept.is_active = bool(row.get('is_active', True))
        dept.is_locked = False
        dept.source_key = (row.get('source_key') or dept.source_key or '').strip()
        dept.sort_order = int(row.get('sort_order') or idx)
        if not dept.name or not dept.code:
            raise ValueError("Every department requires name and code.")
        dept.save()
        kept_department_ids.add(dept.pk)

        for srr_id in row.get('role_requirement_ids') or []:
            if srr_id in role_assignments:
                raise ValueError(f"Site role requirement {srr_id} is assigned more than once.")
            role_assignments[int(srr_id)] = dept

    MobilisationProposedDepartment.objects.filter(
        request=request,
        is_locked=False,
    ).exclude(pk__in=kept_department_ids).update(is_active=False, updated_at=timezone.now())

    MobilisationProposedDepartmentRole.objects.filter(request=request).delete()
    for idx, (srr_id, dept) in enumerate(role_assignments.items(), start=1):
        srr = SiteRoleRequirement.objects.select_related('site').get(pk=srr_id)
        if request.client_id and srr.site.client_id != request.client_id:
            raise ValueError(f"Site role requirement {srr_id} does not belong to this client.")
        if dept.scope_level != 'site' or dept.real_site_id != srr.site_id:
            raise ValueError(
                f"Department '{dept.code}' must be site-level and match SRR {srr_id}'s site."
            )
        MobilisationProposedDepartmentRole.objects.create(
            request=request,
            proposed_department=dept,
            site_role_requirement=srr,
            sort_order=idx,
            is_active=True,
        )

    kept_user_ids = set()
    for row in payload.get('users') or []:
        row_id = row.get('id')
        if row_id:
            try:
                p_user = MobilisationProposedUser.objects.get(request=request, pk=row_id)
            except MobilisationProposedUser.DoesNotExist as exc:
                raise ValueError(f"Unknown proposed user id {row_id}.") from exc
        else:
            p_user = MobilisationProposedUser(request=request)

        role_id = row.get('access_role')
        if not role_id:
            raise ValueError("Every proposed user requires access_role.")
        access_role = AccessRole.objects.get(pk=role_id)
        if access_role.org_id != request.org_id:
            raise ValueError("User access_role must belong to the mobilisation organization.")

        scope_level = row.get('scope_level') or 'client'
        real_site = None
        if scope_level == 'site':
            site_id = row.get('real_site')
            if not site_id:
                raise ValueError("Site-level proposed user requires real_site.")
            real_site = SiteProfile.objects.get(pk=site_id)
            if request.client_id and real_site.client_id != request.client_id:
                raise ValueError("User site must belong to the mobilisation client.")

        p_user.full_name = (row.get('full_name') or '').strip()
        p_user.email = (row.get('email') or '').strip().lower()
        p_user.phone = (row.get('phone') or '').strip()
        p_user.user_type = row.get('user_type') or 'client'
        p_user.access_role = access_role
        p_user.scope_level = scope_level
        p_user.real_site = real_site
        p_user.is_primary_contact = bool(row.get('is_primary_contact', False))
        p_user.send_invite_on_finalization = bool(row.get('send_invite_on_finalization', True))
        p_user.is_active = bool(row.get('is_active', True))
        if not p_user.full_name or not p_user.email:
            raise ValueError("Every proposed user requires full_name and email.")
        p_user.save()
        kept_user_ids.add(p_user.pk)

    MobilisationProposedUser.objects.filter(request=request).exclude(
        pk__in=kept_user_ids,
    ).update(is_active=False, updated_at=timezone.now())

    request.setup_strategy = strategy
    request.save(update_fields=['setup_strategy', 'updated_at'])
    return get_mobilisation_setup_builder(request)


@transaction.atomic
def apply_mobilisation_setup_suggestions(request, actor=None):
    """Backward-compatible shortcut: apply the role-grouped setup template."""
    before_depts = set(request.proposed_departments.values_list('id', flat=True))
    before_users = set(request.proposed_users.values_list('id', flat=True))

    builder = apply_mobilisation_setup_builder_template(
        request,
        strategy='role_grouped',
        actor=actor,
    )

    after_depts = set(request.proposed_departments.values_list('id', flat=True))
    after_users = set(request.proposed_users.values_list('id', flat=True))
    return {
        'created': {
            'departments': sorted(after_depts - before_depts),
            'users': sorted(after_users - before_users),
        },
        'skipped': {
            'departments': [],
            'users': [],
        },
        'suggestions': get_mobilisation_setup_suggestions(request),
        'builder': builder,
    }


def finalize_mobilisation_directly(request, actor, *, override_approval_required=False):
    """
    Bypass the approval workflow.

    Runs preflight checks, marks the request approved, then finalizes.
    Raises ValueError if approval is required and the caller did not explicitly
    choose the direct-finalize override, or if the request is not in a valid state.
    """
    if request.mobilisation_requires_approval and not override_approval_required:
        raise ValueError(
            "This mobilisation request requires workflow approval. "
            "Pass override_approval_required=true to finalize directly."
        )
    assert_setup_completed_for_finalization(request)
    if request.status not in ('draft', 'submitted', 'setup_completed'):
        raise ValueError(
            f"Cannot directly finalize a request in status '{request.status}'."
        )
    if request.finalization_status == 'finalized':
        return

    preflight_errors = validate_mobilisation_finalization_preflight(request)
    if preflight_errors:
        raise ValueError(
            f"Preflight failed: {'; '.join(preflight_errors)}"
        )

    from .models import MobilisationSetupRequest
    now = timezone.now()
    MobilisationSetupRequest.objects.filter(pk=request.pk).update(
        status='approved',
        approved_at=now,
        updated_at=now,
    )
    request.status = 'approved'

    finalize_mobilisation_request(request, actor=actor)


# ─── Sales Context ─────────────────────────────────────────────────────────────

def get_mobilisation_sales_context(request):
    """
    Build comprehensive sales context for a MobilisationSetupRequest.

    Returns a dict containing:
    - mobilisation: basic mobilisation info
    - lead: source sales lead info (or None)
    - proposal: source proposal version info (or None)
    - sites: list of sites with headcount and roles
    - budget_lines: list of budget line items
    - proposal_versions: list of all proposal versions with client remarks
    - readiness: client user setup counts vs expected

    Safe for missing source_sales_lead or source_proposal_version.
    """
    # ─── Mobilisation info ─────────────────────────────────────────────────
    mobilisation_data = {
        'id': request.id,
        'status': request.status,
        'mobilisation_type': request.mobilisation_type,
        'client_name': request.client.name if request.client else None,
        'created_at': request.created_at.isoformat() if request.created_at else None,
        'finalization_status': request.finalization_status,
        'mobilisation_requires_approval': request.mobilisation_requires_approval,
        'assigned_operations_owner': request.assigned_operations_owner_id,
        'assigned_operations_owner_username': (
            request.assigned_operations_owner.username
            if request.assigned_operations_owner_id else None
        ),
        'submitted_to_operations_at': (
            request.submitted_to_operations_at.isoformat()
            if request.submitted_to_operations_at else None
        ),
        'setup_completed_at': (
            request.setup_completed_at.isoformat() if request.setup_completed_at else None
        ),
        'setup_completed_by': request.setup_completed_by_id,
        'setup_completed_by_username': (
            request.setup_completed_by.username if request.setup_completed_by_id else None
        ),
    }

    # ─── Source lead info ──────────────────────────────────────────────────
    lead = request.source_sales_lead
    lead_data = None
    if lead:
        lead_data = {
            'id': lead.id,
            'client_name': lead.client_name,
            'lead_type': lead.lead_type,
            'current_stage': lead.current_stage,
            'sales_person_name': lead.sales_person.username if lead.sales_person else None,
            'client_contact_person': lead.client_contact_person,
            'client_email': lead.client_email,
            'client_phone': lead.client_phone,
        }

    # ─── Source proposal info ──────────────────────────────────────────────
    proposal = request.source_proposal_version
    proposal_data = None
    if proposal:
        proposal_data = {
            'id': proposal.id,
            'version_number': proposal.version_number,
            'status': proposal.status,
            'subtotal_amount': str(proposal.subtotal_amount) if proposal.subtotal_amount else '0.00',
            'management_fee_amount': str(proposal.management_fee_amount) if proposal.management_fee_amount else '0.00',
            'gst_amount': str(proposal.gst_amount) if proposal.gst_amount else '0.00',
            'grand_total': str(proposal.grand_total) if proposal.grand_total else '0.00',
            'manpower_total': proposal.manpower_total,
            'client_approval_status': proposal.client_approval_status,
            'client_remarks': proposal.client_remarks,
            'client_approved_at': proposal.client_approved_at.isoformat() if proposal.client_approved_at else None,
        }

    # ─── Sites with roles ──────────────────────────────────────────────────
    sites_data = []
    if lead:
        from apps.sales.models import SalesLeadSite, SalesRoleRequirement

        sites = SalesLeadSite.objects.filter(
            lead=lead, is_active=True
        ).select_related('location_area').order_by('site_name')

        for site in sites:
            # Get role requirements for this site
            role_reqs = SalesRoleRequirement.objects.filter(
                site=site, is_active=True
            ).select_related('job_role', 'wage_category').order_by('job_role__name')

            roles_list = []
            site_headcount = 0
            for rr in role_reqs:
                site_headcount += rr.manpower_count or 0
                roles_list.append({
                    'id': rr.id,
                    'job_role_name': rr.job_role.name if rr.job_role else None,
                    'wage_category_name': rr.wage_category.name if rr.wage_category else None,
                    'service_category': rr.service_category,
                    'manpower_count': rr.manpower_count,
                    'shift_hours': str(rr.shift_hours) if rr.shift_hours else None,
                    'working_days': str(rr.working_days) if rr.working_days else None,
                })

            sites_data.append({
                'id': site.id,
                'site_name': site.site_name,
                'site_address': site.site_address,
                'city': site.city,
                'state': site.state,
                'location_area_name': site.location_area.name if site.location_area else None,
                'headcount': site_headcount,
                'roles': roles_list,
            })

    # ─── Budget lines ──────────────────────────────────────────────────────
    budget_lines_data = []
    if proposal:
        from apps.sales.models import ProposalBudgetLine

        budget_lines = ProposalBudgetLine.objects.filter(
            proposal_version=proposal
        ).select_related('site', 'job_role').order_by('sort_order', 'id')

        for bl in budget_lines:
            budget_lines_data.append({
                'id': bl.id,
                'site_name': bl.site.site_name if bl.site else None,
                'job_role_name': bl.job_role.name if bl.job_role else None,
                'description': bl.description,
                'manpower_count': bl.manpower_count,
                'unit_cost': str(bl.unit_cost) if bl.unit_cost else '0.00',
                'total_cost': str(bl.total_cost) if bl.total_cost else '0.00',
            })

    # ─── Proposal versions history ─────────────────────────────────────────
    proposal_versions_data = []
    if lead:
        from apps.sales.models import ProposalVersion

        versions = ProposalVersion.objects.filter(
            lead=lead
        ).order_by('version_number')

        for v in versions:
            proposal_versions_data.append({
                'id': v.id,
                'version_number': v.version_number,
                'status': v.status,
                'grand_total': str(v.grand_total) if v.grand_total else '0.00',
                'client_approval_status': v.client_approval_status,
                'client_remarks': v.client_remarks,
            })

    # ─── Readiness stats ───────────────────────────────────────────────────
    # Calculate expected vs created client users. Departments are no longer a
    # required client-side setup artifact; sites and SRRs already exist from the
    # sales conversion flow.
    expected_departments = 0
    expected_users = 0

    if request.client_id:
        # At least one client user/contact is needed to use the client portal.
        expected_users = 1

    # Created counts from proposed records
    created_departments = 0
    created_users = request.proposed_users.filter(is_active=True).count()

    readiness_data = {
        'expected_departments': expected_departments,
        'created_departments': created_departments,
        'expected_users': expected_users,
        'created_users': created_users,
        'missing_departments': max(0, expected_departments - created_departments),
        'missing_users': max(0, expected_users - created_users),
        'ready_to_finalize': (
            created_users >= expected_users
            and expected_users > 0
        ),
    }

    return {
        'mobilisation': mobilisation_data,
        'lead': lead_data,
        'proposal': proposal_data,
        'sites': sites_data,
        'budget_lines': budget_lines_data,
        'proposal_versions': proposal_versions_data,
        'readiness': readiness_data,
    }

"""
apps/onboarding/services.py

Finalization service for ClientOnboardingRequest.

Called after workflow approval to create real Client/SiteProfile/Department/SRR records
from the proposed setup data captured during the onboarding workflow.
"""

import logging

from django.db import connection as _db_connection, transaction
from django.utils import timezone

from .exceptions import OnboardingFinalizationError

logger = logging.getLogger(__name__)


# ─── Readiness ────────────────────────────────────────────────────────────────

def check_onboarding_readiness(onboarding_request):
    """
    Return (ok, errors, warnings) for the given ClientOnboardingRequest.

    ok:       True iff there are no blocking errors.
    errors:   list of blocking issue strings.
    warnings: list of non-blocking advisory strings.

    Rules applied per onboarding_type. Unknown types are treated as ready.
    """
    errors = []
    warnings = []

    ob_type = onboarding_request.onboarding_type

    if ob_type == 'new_client':
        _check_new_client(onboarding_request, errors, warnings)
    elif ob_type == 'new_site_expansion':
        _check_new_site_expansion(onboarding_request, errors, warnings)

    return not bool(errors), errors, warnings


def _check_new_client(req, errors, warnings):
    if not req.proposed_client_name:
        errors.append("Proposed client name is required.")
    if not req.proposed_client_code:
        errors.append("Proposed client code is required.")

    active_sites = list(
        req.proposed_sites.filter(is_active=True)
        .only('id', 'code', 'contact_person', 'contact_phone', 'contact_email')
    )
    site_count = len(active_sites)

    if site_count == 0:
        errors.append("Add at least one active proposed site.")
    else:
        if req.expected_site_count and site_count < req.expected_site_count:
            errors.append(
                f"Expected {req.expected_site_count} proposed site(s), "
                f"but only {site_count} active site(s) found."
            )
        for site in active_sites:
            if not site.contact_person or not site.contact_phone or not site.contact_email:
                warnings.append(
                    f"Proposed site '{site.code}' is missing contact person, phone, or email."
                )

    dept_count = req.proposed_departments.filter(is_active=True).count()
    if dept_count == 0:
        errors.append("Add at least one active proposed department.")

    active_srrs = list(
        req.proposed_role_requirements.filter(is_active=True)
        .only('id', 'billing_rate', 'wage_min', 'wage_max')
    )
    if not active_srrs:
        errors.append("Add at least one active proposed role requirement.")
    else:
        for srr in active_srrs:
            if srr.billing_rate is None or srr.wage_min is None or srr.wage_max is None:
                warnings.append(
                    "A proposed role requirement is missing wage or billing information."
                )
                break

    budget_count = req.proposed_budgets.filter(is_active=True).count()
    if budget_count == 0:
        errors.append("Add at least one active proposed budget.")

    user_count = req.proposed_users.filter(is_active=True).count()
    if user_count == 0:
        errors.append("Add at least one active proposed user.")
    else:
        has_primary = req.proposed_users.filter(is_active=True, is_primary_contact=True).exists()
        if not has_primary:
            warnings.append("No primary contact has been designated among proposed users.")


def _check_new_site_expansion(req, errors, warnings):
    if req.client_id is None:
        errors.append("An existing client is required for site expansion.")

    active_sites = list(
        req.proposed_sites.filter(is_active=True)
        .only('id', 'code', 'contact_person', 'contact_phone', 'contact_email')
    )
    site_count = len(active_sites)

    if site_count == 0:
        errors.append("Add at least one active proposed site.")
    else:
        for site in active_sites:
            if not site.contact_person or not site.contact_phone or not site.contact_email:
                warnings.append(
                    f"Proposed site '{site.code}' is missing contact person, phone, or email."
                )

    dept_count = req.proposed_departments.filter(is_active=True).count()
    if dept_count == 0:
        errors.append("Add at least one active proposed department.")

    active_srrs = list(
        req.proposed_role_requirements.filter(is_active=True)
        .only('id', 'billing_rate', 'wage_min', 'wage_max')
    )
    if not active_srrs:
        errors.append("Add at least one active proposed role requirement.")
    else:
        for srr in active_srrs:
            if srr.billing_rate is None or srr.wage_min is None or srr.wage_max is None:
                warnings.append(
                    "A proposed role requirement is missing wage or billing information."
                )
                break

    budget_count = req.proposed_budgets.filter(is_active=True).count()
    if budget_count == 0:
        errors.append("Add at least one active proposed budget.")

    if active_sites:
        has_site_users = req.proposed_users.filter(is_active=True, scope_level='site').exists()
        if not has_site_users:
            warnings.append("Proposed sites exist but no site-level users have been added.")


# ─── Public ───────────────────────────────────────────────────────────────────

def finalize_client_onboarding_request(request, actor=None):
    """
    Create real Client/Site/Department/SRR records from a proposed onboarding request.

    Safe to call inside an existing transaction — uses a raw SAVEPOINT so that a
    finalization failure rolls back only the finalization work; the outer transaction
    (workflow approval, request status update) continues and commits normally.

    Idempotent: if already finalized, returns immediately.
    On failure: stores finalization_status='failed' and finalization_error on the request,
    logs the error, then raises OnboardingFinalizationError.
    """
    from .models import ClientOnboardingRequest

    if request.finalization_status == 'finalized':
        return

    sid = transaction.savepoint()
    try:
        _do_finalize(request, actor)
        transaction.savepoint_commit(sid)
    except Exception as exc:
        # Django's mark_for_rollback_on_error (inside Model.save()) sets
        # needs_rollback=True on any DB error when inside an atomic block.
        # Reset it here so we can issue the ROLLBACK TO SAVEPOINT, which
        # restores the outer transaction to a consistent state.
        _db_connection.needs_rollback = False
        transaction.savepoint_rollback(sid)
        # Persist failure state on the outer transaction (not rolled back above)
        ClientOnboardingRequest.objects.filter(pk=request.pk).update(
            finalization_status='failed',
            finalization_error=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        logger.error(
            'Onboarding finalization failed for request pk=%s after workflow approval: %s',
            request.pk,
            exc,
            exc_info=True,
        )
        raise OnboardingFinalizationError(str(exc)) from exc


def retry_finalize_client_onboarding_request(request, actor=None):
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
    # Clear previous error so the idempotency check passes
    from .models import ClientOnboardingRequest
    ClientOnboardingRequest.objects.filter(pk=request.pk).update(
        finalization_status='not_finalized',
        finalization_error='',
        updated_at=timezone.now(),
    )
    request.finalization_status = 'not_finalized'
    finalize_client_onboarding_request(request, actor=actor)


# ─── Internal ─────────────────────────────────────────────────────────────────

def _do_finalize(request, actor):
    """
    Core finalization logic. Must be called inside a savepoint / transaction.
    Sets finalization_status='finalized' on success.
    """
    from apps.core.models import ScopeNode, Department
    from apps.sites.models import Client, SiteProfile, SiteRoleRequirement

    if request.onboarding_type == 'new_client':
        client = _create_client(request, actor)
    else:
        client = request.client
        if client is None:
            raise OnboardingFinalizationError(
                "new_site_expansion onboarding has no client attached."
            )

    # Build map: proposed_site.pk → real SiteProfile
    site_map = {}
    for p_site in request.proposed_sites.filter(is_active=True).select_related('location_area'):
        site = _create_site(p_site, client, request.org, actor)
        site_map[p_site.pk] = site

    # Build map: proposed_department.pk → real Department
    dept_map = {}
    for p_dept in request.proposed_departments.filter(is_active=True):
        real_site = site_map.get(p_dept.proposed_site_id) if p_dept.proposed_site_id else None
        dept = _create_department(p_dept, client, real_site, request.org)
        dept_map[p_dept.pk] = dept

    for p_srr in request.proposed_role_requirements.filter(is_active=True).select_related(
        'job_role', 'wage_category',
    ):
        real_site = site_map.get(p_srr.proposed_site_id)
        if real_site is None:
            raise OnboardingFinalizationError(
                f"Proposed SRR pk={p_srr.pk} references proposed_site_id={p_srr.proposed_site_id} "
                f"which was not created (inactive or missing)."
            )
        real_dept = dept_map.get(p_srr.proposed_department_id) if p_srr.proposed_department_id else None
        _create_srr(p_srr, real_site, department=real_dept)

    for p_budget in request.proposed_budgets.filter(is_active=True):
        _create_budget(p_budget, client, site_map, dept_map, request.org, actor)

    for p_user in request.proposed_users.filter(is_active=True).select_related(
        'access_role', 'proposed_site',
    ):
        _create_proposed_user(p_user, request.org, client, site_map)

    request.created_client = client if request.onboarding_type == 'new_client' else None
    request.finalization_status = 'finalized'
    request.finalized_at = timezone.now()
    request.finalized_by = actor
    request.save(update_fields=[
        'created_client', 'finalization_status', 'finalized_at', 'finalized_by', 'updated_at',
    ])


def _create_client(request, actor):
    from apps.core.models import ScopeNode
    from apps.sites.models import Client

    client = Client.objects.create(
        org=request.org,
        name=request.proposed_client_name,
        code=request.proposed_client_code,
        contact_name=request.proposed_contact_name,
        contact_email=request.proposed_contact_email,
        contact_phone=request.proposed_contact_phone,
        industry=request.proposed_industry,
        billing_address=request.proposed_billing_address,
        gst_number=request.proposed_gst_number,
        created_by=actor,
        is_active=True,
    )

    org_root = ScopeNode.objects.filter(
        org=request.org, node_type='company', is_active=True,
    ).first()

    if org_root:
        path = f"{org_root.path}/{client.code}"
        depth = org_root.depth + 1
    else:
        path = client.code
        depth = 0

    scope_node = ScopeNode.objects.create(
        org=request.org,
        parent=org_root,
        name=client.name,
        code=client.code,
        node_type='client',
        path=path,
        depth=depth,
        is_active=True,
    )
    client.scope_node = scope_node
    client.save(update_fields=['scope_node'])
    return client


def _create_site(proposed_site, client, org, actor):
    from apps.core.models import ScopeNode
    from apps.sites.models import SiteProfile

    site = SiteProfile.objects.create(
        org=org,
        client=client,
        name=proposed_site.name,
        code=proposed_site.code,
        address=proposed_site.address,
        city=proposed_site.city,
        state=proposed_site.state,
        pincode=proposed_site.pincode,
        contact_person=proposed_site.contact_person,
        contact_phone=proposed_site.contact_phone,
        contact_email=proposed_site.contact_email,
        location_area=proposed_site.location_area,
        created_by=actor,
        is_active=True,
    )

    client_node = client.scope_node
    if client_node:
        path = f"{client_node.path}/{site.code}"
        depth = client_node.depth + 1
        parent = client_node
    else:
        path = site.code
        depth = 0
        parent = None

    scope_node = ScopeNode.objects.create(
        org=org,
        parent=parent,
        name=site.name,
        code=site.code,
        node_type='site',
        path=path,
        depth=depth,
        is_active=True,
    )
    site.scope_node = scope_node
    site.save(update_fields=['scope_node'])
    return site


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


def _create_srr(proposed_srr, real_site, department=None):
    from apps.sites.models import SiteRoleRequirement
    from datetime import date

    return SiteRoleRequirement.objects.create(
        site=real_site,
        department=department,
        job_role=proposed_srr.job_role,
        approved_headcount=proposed_srr.approved_headcount,
        billing_rate=proposed_srr.billing_rate,
        wage_min=proposed_srr.wage_min,
        wage_max=proposed_srr.wage_max,
        shift_hours=proposed_srr.shift_hours,
        billing_type=proposed_srr.billing_type,
        wage_category=proposed_srr.wage_category,
        effective_from=proposed_srr.effective_from or date.today(),
        effective_to=proposed_srr.effective_to,
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


def _create_proposed_user(p_user, org, client, site_map):
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

    if p_user.scope_level == 'site' and p_user.proposed_site_id:
        real_site = site_map.get(p_user.proposed_site_id)
        if real_site is None:
            raise OnboardingFinalizationError(
                f"Proposed user pk={p_user.pk} references proposed_site_id="
                f"{p_user.proposed_site_id} which was not finalized (inactive or missing)."
            )
        scope_node = real_site.scope_node
    else:
        scope_node = client.scope_node

    if scope_node is None:
        raise OnboardingFinalizationError(
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

    from .models import ClientOnboardingProposedUser
    ClientOnboardingProposedUser.objects.filter(pk=p_user.pk).update(
        created_user=user,
        invite_status=invite_status,
        invite_error=invite_error,
    )


def resend_onboarding_proposed_user_invite(proposed_user, actor=None):
    """
    Re-send the invite email for a finalized proposed user.

    Raises ValueError for invalid states; raises on SMTP failure so the caller
    can surface the error to the API consumer.
    """
    from apps.accounts.services import send_user_invite_email

    if proposed_user.created_user_id is None:
        raise ValueError("Cannot resend invite: user has not been finalized yet.")

    user = proposed_user.created_user
    from .models import ClientOnboardingProposedUser
    try:
        send_user_invite_email(user, context_label='Client portal')
    except Exception as exc:
        ClientOnboardingProposedUser.objects.filter(pk=proposed_user.pk).update(
            invite_status='failed',
            invite_error=str(exc)[:2000],
        )
        raise

    ClientOnboardingProposedUser.objects.filter(pk=proposed_user.pk).update(
        invite_status='sent',
        invite_error='',
    )
    logger.info(
        'Resent invite email for proposed user pk=%s (user pk=%s), actor pk=%s',
        proposed_user.pk, user.pk, getattr(actor, 'pk', None),
    )


def _create_budget(proposed_budget, client, site_map, dept_map, org, actor):
    from apps.budgets.models import BudgetPlan

    real_site = site_map.get(proposed_budget.proposed_site_id) if proposed_budget.proposed_site_id else None
    real_dept = dept_map.get(proposed_budget.proposed_department_id) if proposed_budget.proposed_department_id else None

    scope_level = proposed_budget.scope_level
    if scope_level == 'site':
        bp_client, bp_site, bp_dept = client, real_site, None
    elif scope_level == 'department':
        bp_client, bp_site, bp_dept = client, real_site, real_dept
    else:  # client
        bp_client, bp_site, bp_dept = client, None, None

    return BudgetPlan.objects.create(
        org=org,
        name=proposed_budget.name,
        code=proposed_budget.code,
        budget_nature=proposed_budget.budget_nature,
        budget_type=proposed_budget.budget_type,
        client=bp_client,
        site=bp_site,
        department=bp_dept,
        amount=proposed_budget.amount,
        currency=proposed_budget.currency,
        period_start=proposed_budget.period_start,
        period_end=proposed_budget.period_end,
        notes=proposed_budget.notes,
        status='active',
        is_active=True,
        created_by=actor,
    )

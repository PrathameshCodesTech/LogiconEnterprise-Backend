"""
apps/deployment/services.py

Conversion service: HiringApplication → Employee + SiteDeployment.
"""

from datetime import date

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import Employee, SiteDeployment


_ALLOWED_STATUSES = {'offer_accepted'}

_BLOCKED_STATUSES = {
    'draft', 'shortlisted', 'client_review',
    'interview_scheduled', 'interview_in_progress',
    'rejected', 'offer_declined', 'cancelled', 'deployed',
}


def _generate_employee_code(org, candidate):
    base = f"EMP-{org.code.upper()}-{candidate.pk}"
    if not Employee.objects.filter(org=org, employee_code=base).exists():
        return base
    suffix = 1
    while Employee.objects.filter(org=org, employee_code=f"{base}-{suffix}").exists():
        suffix += 1
    return f"{base}-{suffix}"


def convert_hiring_application_to_deployment(
    application,
    actor,
    employee_code=None,
    joined_on=None,
    deployment_start_date=None,
    deployment_status='planned',
    shift_hours=None,
    billing_type=None,
    allow_existing_employee=False,
):
    """
    Convert an offer-accepted HiringApplication into an Employee +
    SiteDeployment, updating the application status and candidate lifecycle.

    Returns:
        {
          'employee': Employee,
          'deployment': SiteDeployment,
          'created_employee': bool,
          'created_deployment': bool,
        }
    """
    # ── Idempotency: return existing deployment if already converted ──────────
    existing_deployment = (
        SiteDeployment.objects.select_related('employee')
        .filter(hiring_application=application)
        .first()
    )
    if existing_deployment:
        return {
            'employee': existing_deployment.employee,
            'deployment': existing_deployment,
            'created_employee': False,
            'created_deployment': False,
        }

    # ── Guard: client approval (only when candidate was sent to client) ──────
    if application.client_visible and application.client_decision != 'approved':
        raise ValidationError({
            'non_field_errors': (
                'Client approval is required before deployment. '
                f"Current client decision: '{application.client_decision or 'none'}'."
            )
        })

    # ── Guard: offer must be accepted if one exists ───────────────────────────
    try:
        from apps.hiring.models import Offer as _Offer
        offer = application.offer
        if offer.status != 'accepted':
            raise ValidationError({
                'non_field_errors': (
                    f"Offer must be accepted before deployment. "
                    f"Current offer status: '{offer.status}'."
                )
            })
    except _Offer.DoesNotExist:
        raise ValidationError({
            'non_field_errors': 'Accepted offer is required before deployment.'
        })

    # ── Guard: allowed statuses ───────────────────────────────────────────────
    if application.status not in _ALLOWED_STATUSES:
        raise ValidationError({
            'non_field_errors': (
                f"Cannot convert application in status '{application.status}'. "
                f"Allowed: {sorted(_ALLOWED_STATUSES)}."
            )
        })

    candidate = application.candidate
    org = application.org
    today = date.today()

    with transaction.atomic():
        # ── Employee: find or create ──────────────────────────────────────────
        created_employee = False

        employee = (
            Employee.objects.filter(org=org, candidate=candidate)
            .order_by('-created_at')
            .first()
        )

        if employee is None and candidate.phone_normalized:
            phone_match = Employee.objects.filter(
                org=org, phone_normalized=candidate.phone_normalized,
            ).exclude(candidate=candidate).first()
            if phone_match:
                if not allow_existing_employee:
                    raise ValidationError({
                        'non_field_errors': (
                            f"Employee #{phone_match.pk} already exists with the same "
                            f"phone for a different candidate. Pass "
                            f"allow_existing_employee=true to reuse."
                        )
                    })
                employee = phone_match

        if employee is None:
            # Validate / generate employee_code
            if employee_code:
                if Employee.objects.filter(org=org, employee_code=employee_code).exists():
                    raise ValidationError(
                        {'employee_code': f"Employee code '{employee_code}' already exists in this org."}
                    )
            else:
                employee_code = _generate_employee_code(org, candidate)

            employee = Employee.objects.create(
                org=org,
                candidate=candidate,
                employee_code=employee_code,
                first_name=candidate.first_name,
                middle_name=getattr(candidate, 'middle_name', '') or '',
                last_name=candidate.last_name,
                phone=candidate.phone or '',
                email=candidate.email or '',
                job_role=application.job_role,
                status='active',
                joined_on=joined_on or today,
            )
            created_employee = True

        # ── SiteDeployment: create ────────────────────────────────────────────
        start = deployment_start_date or joined_on or today

        resolved_billing_type = billing_type
        if not resolved_billing_type:
            try:
                resolved_billing_type = application.mrf.billing_type
            except Exception:
                pass
        if not resolved_billing_type:
            resolved_billing_type = 'billable'

        resolved_shift_hours = shift_hours
        if resolved_shift_hours is None:
            try:
                srr = application.mrf_line_item.site_role_requirements.filter(
                    is_active=True
                ).first()
                if srr and hasattr(srr, 'shift_hours') and srr.shift_hours:
                    resolved_shift_hours = srr.shift_hours
            except Exception:
                pass

        deployment = SiteDeployment.objects.create(
            org=org,
            employee=employee,
            site=application.site,
            job_role=application.job_role,
            mrf_line_item=application.mrf_line_item,
            hiring_application=application,
            status=deployment_status,
            start_date=start,
            shift_hours=resolved_shift_hours,
            billing_type=resolved_billing_type,
            created_by=actor,
        )

        # ── HiringApplication update ──────────────────────────────────────────
        from apps.hiring.lifecycle import STAGE_DEPLOYED, transition_application

        transition_application(
            application,
            actor=actor,
            status='deployed',
            stage_code=STAGE_DEPLOYED,
            comment='Converted to employee/deployment.',
        )

        # ── Candidate lifecycle update ────────────────────────────────────────
        candidate.lifecycle_status = 'employee_converted'
        candidate.availability_status = 'currently_deployed'
        candidate.save(update_fields=['lifecycle_status', 'availability_status'])

    return {
        'employee': employee,
        'deployment': deployment,
        'created_employee': created_employee,
        'created_deployment': True,
    }

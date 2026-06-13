"""
apps/mrf/services.py

MRF readiness checks and budget context helpers.
"""

from decimal import Decimal

from django.db.models import Sum


def build_line_item_commercial_snapshot(srr):
    """Return master commercial values from a SiteRoleRequirement."""
    return {
        'wage_min': srr.wage_min,
        'wage_max': srr.wage_max,
        'billing_rate': srr.billing_rate,
        'shift_hours': getattr(srr, 'shift_hours', None),
    }


_ACTIVE_DEMAND_STATUSES = (
    'submitted',
    'hr_review',
    'finance_review',
    'admin_review',
    'client_review',
    'approved',
)


def get_billable_headcount_usage(site, job_role, exclude_mrf=None):
    """
    Return sum of headcount for active MRF line items at (site, job_role).

    Active demand = statuses: submitted, hr_review, finance_review,
    admin_review, client_review, approved.
    Draft, rejected, and cancelled MRFs are excluded.
    Pass exclude_mrf to omit a specific MRF (avoids double-counting on re-check).
    """
    from apps.mrf.models import MRFLineItem

    qs = MRFLineItem.objects.filter(
        mrf__site=site,
        job_role=job_role,
        mrf__status__in=_ACTIVE_DEMAND_STATUSES,
    )
    if exclude_mrf is not None:
        qs = qs.exclude(mrf=exclude_mrf)

    result = qs.aggregate(total=Sum('headcount'))
    return result['total'] or 0


def check_mrf_readiness(mrf):
    """
    Validate whether the MRF is ready to enter the approval workflow.

    Returns a dict::

        {
            'ok': bool,
            'errors': [str, ...],
            'warnings': [str, ...],
            'billable_headcount': {str(line_item_pk): {job_role, requested,
                                    already_allocated, approved_headcount, available}},
            'budget': {required_amount, available_amount, sufficient} or None,
        }

    Billable rules:
      - Every line item must have a SiteRoleRequirement from mrf.site.
      - requested + already_allocated <= SRR.approved_headcount.
      - budget_plan (if set) must be billable.
      - required_amount <= available_amount (if budget_plan set).

    Non-billable rules:
      - budget_plan (if set) must be non_billable.
      - If budget_plan is department-scoped, department must match
        requesting_department or required_department on the MRF.
      - required_amount <= available_amount (if budget_plan set).
    """
    from apps.budgets.services import get_budget_plan_totals, calculate_mrf_reservation_amount
    from apps.budgets.exceptions import BudgetReservationError

    errors = []
    warnings = []
    billable_headcount = {}
    budget_summary = None
    budget_nature_valid = True

    line_items = list(
        mrf.line_items.select_related('job_role', 'site_role_requirement', 'site_role_requirement__department').all()
    )

    if not line_items:
        errors.append('MRF has no line items. Add at least one job role before submitting.')
        return {
            'ok': False,
            'errors': errors,
            'warnings': warnings,
            'billable_headcount': billable_headcount,
            'budget': budget_summary,
        }

    if mrf.billing_type == 'billable':
        for li in line_items:
            srr = li.site_role_requirement
            job_role_name = str(li.job_role)

            if srr is None:
                errors.append(
                    f'Line item for "{job_role_name}" has no SiteRoleRequirement. '
                    f'Each billable line item must be linked to an approved site role.'
                )
                continue

            if srr.site_id != mrf.site_id:
                errors.append(
                    f'Line item for "{job_role_name}": SiteRoleRequirement is for a different site.'
                )
                continue

            # SRR department must match MRF required_department when both are set
            if srr.department_id is not None and mrf.required_department_id is not None:
                if srr.department_id != mrf.required_department_id:
                    errors.append(
                        f'Line item for "{job_role_name}": SiteRoleRequirement department '
                        f'does not match the MRF required department.'
                    )
                    continue

            already_allocated = get_billable_headcount_usage(
                mrf.site, li.job_role, exclude_mrf=mrf,
            )
            available = srr.approved_headcount - already_allocated
            requested = li.headcount

            billable_headcount[str(li.pk)] = {
                'job_role': job_role_name,
                'requested': requested,
                'already_allocated': already_allocated,
                'approved_headcount': srr.approved_headcount,
                'available': available,
            }

            if requested > available:
                errors.append(
                    f'Line item for "{job_role_name}": requested {requested} but only '
                    f'{available} headcount available '
                    f'(approved {srr.approved_headcount}, already allocated {already_allocated}).'
                )

        if any(li.commercial_override_enabled for li in line_items):
            warnings.append(
                'One or more line items override configured commercial values.'
            )

        if mrf.budget_plan_id:
            bp = mrf.budget_plan
            if bp.budget_nature != 'billable':
                errors.append('Billable MRF requires a billable budget plan.')
                budget_nature_valid = False

    else:  # non_billable
        if not mrf.requesting_department_id:
            errors.append('Non-billable MRF requires a requesting department.')
        if not mrf.required_department_id:
            errors.append('Non-billable MRF requires a required department.')
        if mrf.budget_plan_id:
            bp = mrf.budget_plan
            if bp.budget_nature != 'non_billable':
                errors.append('Non-billable MRF requires a non-billable budget plan.')
                budget_nature_valid = False
            elif bp.budget_type != 'hiring':
                errors.append('Non-billable MRF requires an internal hiring budget plan.')
                budget_nature_valid = False
            elif not bp.department_id:
                errors.append('Non-billable budget plan must be department-scoped.')
            elif bp.department_id != mrf.required_department_id:
                errors.append(
                    'Budget plan department does not match the required department of this MRF.'
                )

    # ── Budget resolution & check ──────────────────────────────────────────────
    if mrf.billing_type == 'billable' and budget_nature_valid:
        from apps.budgets.services import (
            resolve_budget_plan_for_mrf,
            _budget_plan_scope,
            mrf_has_budgetable_line_items,
        )

        # Determine which budget plan to use
        if not mrf.budget_plan_id and not mrf_has_budgetable_line_items(mrf):
            resolved_plan = None
            plan_scope = 'none'
        elif mrf.budget_plan_id:
            resolved_plan = mrf.budget_plan
            plan_scope = 'explicit'
        else:
            try:
                resolved_plan = resolve_budget_plan_for_mrf(mrf)
                plan_scope = _budget_plan_scope(resolved_plan)
            except BudgetReservationError as exc:
                errors.append(str(exc))
                resolved_plan = None
                plan_scope = 'none'

        if resolved_plan is not None:
            try:
                required = calculate_mrf_reservation_amount(mrf, resolved_plan)
                totals = get_budget_plan_totals(resolved_plan)
                available_amount = totals['available_amount']
                available_after = max(Decimal('0.00'), available_amount - required)
                sufficient = required <= available_amount
                budget_summary = {
                    'ok': sufficient,
                    'sufficient': sufficient,
                    'plan_id': resolved_plan.pk,
                    'plan_name': resolved_plan.name,
                    'plan_code': resolved_plan.code,
                    'scope': plan_scope,
                    'total_amount': totals['total_amount'],
                    'available_amount': available_amount,
                    'requested_amount': required,
                    'available_after_request': available_after,
                }
                if not sufficient:
                    errors.append(
                        f'Insufficient budget: required {required}, '
                        f'available {available_amount}.'
                    )
            except BudgetReservationError as exc:
                errors.append(f'Budget calculation failed: {exc}')
        else:
            budget_summary = {
                'ok': False,
                'sufficient': False,
                'plan_id': None,
                'plan_name': None,
                'plan_code': None,
                'scope': 'none',
                'total_amount': None,
                'available_amount': None,
                'requested_amount': None,
                'available_after_request': None,
            }

    elif mrf.billing_type == 'non_billable' and budget_nature_valid:
        from apps.budgets.services import (
            resolve_non_billable_budget_plan_for_mrf,
            _budget_plan_scope,
        )

        if mrf.budget_plan_id:
            resolved_plan = mrf.budget_plan
            plan_scope = 'explicit'
        else:
            try:
                resolved_plan = resolve_non_billable_budget_plan_for_mrf(mrf)
                plan_scope = _budget_plan_scope(resolved_plan)
            except BudgetReservationError as exc:
                errors.append(str(exc))
                resolved_plan = None
                plan_scope = 'none'

        if resolved_plan is None:
            budget_summary = {
                'ok': False,
                'sufficient': False,
                'plan_id': None,
                'plan_name': None,
                'plan_code': None,
                'scope': plan_scope,
                'total_amount': None,
                'available_amount': None,
                'requested_amount': None,
                'available_after_request': None,
            }
            return {
                'ok': len(errors) == 0,
                'errors': errors,
                'warnings': warnings,
                'billable_headcount': billable_headcount,
                'budget': budget_summary,
            }

        try:
            required = calculate_mrf_reservation_amount(mrf, resolved_plan)
            totals = get_budget_plan_totals(resolved_plan)
            available_amount = totals['available_amount']
            available_after = max(Decimal('0.00'), available_amount - required)
            sufficient = required <= available_amount
            budget_summary = {
                'ok': sufficient,
                'sufficient': sufficient,
                'plan_id': resolved_plan.pk,
                'plan_name': resolved_plan.name,
                'plan_code': resolved_plan.code,
                'scope': plan_scope,
                'total_amount': totals['total_amount'],
                'available_amount': available_amount,
                'requested_amount': required,
                'available_after_request': available_after,
            }
            if not sufficient:
                errors.append(
                    f'Insufficient budget: required {required}, '
                    f'available {available_amount}.'
                )
        except BudgetReservationError as exc:
            errors.append(f'Budget calculation failed: {exc}')

    return {
        'ok': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'billable_headcount': billable_headcount,
        'budget': budget_summary,
    }


def get_resolved_budget_context(mrf):
    """
    Return a dict of resolved budget context fields for MRF serializers.

    Determines the applicable BudgetPlan (explicit or auto-resolved), then
    returns totals, reservation amounts, and estimated request amount.
    Caches the result on the MRF instance as _resolved_budget_ctx.

    All monetary values are formatted as strings (str(Decimal)).
    """
    from apps.budgets.services import (
        resolve_budget_plan_for_mrf,
        resolve_non_billable_budget_plan_for_mrf,
        get_budget_plan_totals,
        calculate_mrf_reservation_amount,
        _budget_plan_scope,
    )
    from apps.budgets.exceptions import BudgetReservationError
    from apps.budgets.models import BudgetReservation
    from django.db.models import Sum, Q

    _NONE = {
        'resolved_budget_plan_id': None,
        'resolved_budget_plan_name': None,
        'resolved_budget_plan_code': None,
        'resolved_budget_scope': 'none',
        'resolved_budget_total_amount': None,
        'resolved_budget_reserved_amount': None,
        'resolved_budget_committed_amount': None,
        'resolved_budget_available_amount': None,
        'requested_budget_amount': None,
        'budget_after_request_available_amount': None,
    }

    if hasattr(mrf, '_resolved_budget_ctx'):
        return mrf._resolved_budget_ctx

    # Determine plan + scope
    if mrf.budget_plan_id:
        plan = mrf.budget_plan
        scope = 'explicit'
    elif mrf.billing_type == 'billable':
        try:
            plan = resolve_budget_plan_for_mrf(mrf)
            scope = _budget_plan_scope(plan)
        except BudgetReservationError:
            mrf._resolved_budget_ctx = _NONE
            return _NONE
    elif mrf.billing_type == 'non_billable':
        try:
            plan = resolve_non_billable_budget_plan_for_mrf(mrf)
            scope = _budget_plan_scope(plan)
        except BudgetReservationError:
            mrf._resolved_budget_ctx = _NONE
            return _NONE
    else:
        mrf._resolved_budget_ctx = _NONE
        return _NONE

    # Totals
    try:
        totals = get_budget_plan_totals(plan)
    except Exception:
        mrf._resolved_budget_ctx = _NONE
        return _NONE

    # Actual reservation amounts for this MRF
    agg = BudgetReservation.objects.filter(mrf=mrf).aggregate(
        reserved=Sum('amount', filter=Q(status='reserved')),
        committed=Sum('amount', filter=Q(status='committed')),
    )
    actual_reserved = agg['reserved'] or Decimal('0.00')
    actual_committed = agg['committed'] or Decimal('0.00')

    # Requested: use actual if already reserved/committed, else estimate
    existing = actual_reserved + actual_committed
    if existing > Decimal('0.00'):
        requested = existing
    else:
        try:
            requested = calculate_mrf_reservation_amount(mrf, plan)
        except BudgetReservationError:
            requested = Decimal('0.00')

    available_after = max(Decimal('0.00'), totals['available_amount'] - requested)

    ctx = {
        'resolved_budget_plan_id': plan.pk,
        'resolved_budget_plan_name': plan.name,
        'resolved_budget_plan_code': plan.code,
        'resolved_budget_scope': scope,
        'resolved_budget_total_amount': str(totals['total_amount']),
        'resolved_budget_reserved_amount': str(totals['reserved_amount']),
        'resolved_budget_committed_amount': str(totals['committed_amount']),
        'resolved_budget_available_amount': str(totals['available_amount']),
        'requested_budget_amount': str(requested),
        'budget_after_request_available_amount': str(available_after),
    }
    mrf._resolved_budget_ctx = ctx
    return ctx

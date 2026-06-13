"""
apps/budgets/services.py

Shared validation helper for BudgetPlan usage across models.
Budget reservation service functions for MRF workflow integration.
"""

from decimal import Decimal


def validate_budget_plan_for_context(
    budget,
    *,
    org,
    billing_type=None,
    client=None,
    site=None,
    department_ids=None,
    require_nature=None,
    require_budget_type=None,
):
    """
    Returns a dict {'budget_plan': message} when the budget is invalid for the context.
    Returns {} when valid.

    Args:
        budget:          BudgetPlan instance to validate.
        org:             Organization the parent record belongs to.
        billing_type:    'billable' or 'non_billable' from the parent context.
        client:          Client instance for onboarding context (billable, no site).
        site:            SiteProfile instance for MRF billable boundary check.
        department_ids:  Iterable of Department PKs acceptable for non-billable match.
        require_nature:  Explicit nature override; takes precedence over billing_type.
        require_budget_type: Optional exact budget_type required by the context.
    """

    def err(msg):
        return {'budget_plan': msg}

    if budget.org_id != org.pk:
        return err('Budget does not belong to this organization.')

    if not budget.is_active:
        return err('Budget is inactive.')

    if budget.status not in ('active', 'draft'):
        return err(f"Budget status '{budget.status}' is not usable (must be active or draft).")

    nature = require_nature or billing_type
    if nature and budget.budget_nature != nature:
        label = 'billable' if nature == 'billable' else 'non-billable'
        return err(f"This context requires a {label} budget.")

    if require_budget_type and budget.budget_type != require_budget_type:
        return err(
            f"This context requires a {require_budget_type.replace('_', ' ')} budget."
        )

    if budget.budget_nature == 'billable':
        if site is not None:
            # MRF site-based boundary
            if budget.site_id is not None:
                if budget.site_id != site.pk:
                    return err('Budget is scoped to a different site.')
            elif budget.client_id is not None:
                if budget.client_id != site.client_id:
                    return err('Budget client does not match the site client.')
        elif client is not None:
            # Onboarding client-based boundary
            if budget.client_id is not None and budget.client_id != client.pk:
                return err('Budget client does not match the request client.')

    if budget.budget_nature == 'non_billable' and department_ids is not None:
        dept_ids = list(department_ids)
        if not dept_ids:
            return err('Non-billable MRFs require a required department for budget validation.')
        if not budget.department_id:
            return err('Non-billable budget must be scoped to a department.')
        if budget.department_id not in dept_ids:
            return err('Budget department does not match the required department on this MRF.')

    return {}


def get_budget_plan_totals(budget_plan):
    """Return total, committed, reserved, and available amounts for a BudgetPlan."""
    from django.db.models import Q, Sum
    from .models import BudgetReservation

    agg = BudgetReservation.objects.filter(budget_plan=budget_plan).aggregate(
        committed=Sum('amount', filter=Q(status='committed')),
        reserved=Sum('amount', filter=Q(status='reserved')),
    )
    committed = agg['committed'] or Decimal('0.00')
    reserved = agg['reserved'] or Decimal('0.00')
    total = budget_plan.amount
    available = max(Decimal('0.00'), total - committed - reserved)
    return {
        'total_amount': total,
        'committed_amount': committed,
        'reserved_amount': reserved,
        'available_amount': available,
    }


# ─── Budget resolver ─────────────────────────────────────────────────────────

def resolve_budget_plan_for_mrf(mrf):
    """
    Auto-select the appropriate active BudgetPlan for a billable MRF.

    Priority (highest first):
      1. Exact: client=mrf.site.client, site=mrf.site, department=mrf.required_department
         (only checked when mrf.required_department is set)
      2. Site-level: same client/site, department IS NULL
      3. Client-level: same client, site IS NULL, department IS NULL

    All candidates must have budget_nature='billable', status='active',
    is_active=True, and a manpower-bearing budget_type.

    Raises BudgetReservationError when:
      - mrf is not billable
      - mrf has no site or site has no client
      - multiple same-priority plans exist
      - no plan found at any priority level
    """
    from .models import BudgetPlan
    from .exceptions import BudgetReservationError

    if mrf.billing_type != 'billable':
        raise BudgetReservationError(
            'resolve_budget_plan_for_mrf is only applicable to billable MRFs.'
        )

    site = mrf.site
    if not site:
        raise BudgetReservationError('MRF has no site.')
    client = getattr(site, 'client', None)
    if client is None:
        raise BudgetReservationError('MRF site has no client.')

    base_qs = BudgetPlan.objects.filter(
        org=mrf.org,
        budget_nature='billable',
        status='active',
        is_active=True,
        client=client,
        budget_type__in=['onboarding', 'manpower', 'headcount'],
    )

    # Priority 1: department-exact match
    if mrf.required_department_id is not None:
        exact = list(base_qs.filter(site=site, department_id=mrf.required_department_id))
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise BudgetReservationError(
                'Multiple matching budget plans found for this MRF site/department.'
            )

    # Priority 2: site-level (no department)
    site_plans = list(base_qs.filter(site=site, department__isnull=True))
    if len(site_plans) == 1:
        return site_plans[0]
    if len(site_plans) > 1:
        raise BudgetReservationError(
            'Multiple matching budget plans found for this MRF site.'
        )

    # Priority 3: client-level (no site, no department)
    client_plans = list(base_qs.filter(site__isnull=True, department__isnull=True))
    if len(client_plans) == 1:
        return client_plans[0]
    if len(client_plans) > 1:
        raise BudgetReservationError(
            'Multiple matching budget plans found for this MRF client.'
        )

    raise BudgetReservationError(
        'No matching budget plan found for this MRF site/department.'
    )


def resolve_non_billable_budget_plan_for_mrf(mrf):
    """
    Auto-select the active department budget for an internal non-billable MRF.

    Non-billable demand is owned by the required department. The requesting
    department explains who raised the request, but it must not make another
    department budget appear valid.
    """
    from .models import BudgetPlan
    from .exceptions import BudgetReservationError

    if mrf.billing_type != 'non_billable':
        raise BudgetReservationError(
            'resolve_non_billable_budget_plan_for_mrf is only applicable to non-billable MRFs.'
        )
    if not mrf.required_department_id:
        raise BudgetReservationError(
            'Non-billable MRF requires a required department before resolving budget.'
        )

    plans = list(BudgetPlan.objects.filter(
        org=mrf.org,
        budget_nature='non_billable',
        budget_type='hiring',
        status='active',
        is_active=True,
        department_id=mrf.required_department_id,
    ))
    if len(plans) == 1:
        return plans[0]
    if len(plans) > 1:
        raise BudgetReservationError(
            'Multiple active internal hiring budgets found for this required department.'
        )
    raise BudgetReservationError(
        'No active internal hiring budget found for this required department.'
    )


def _budget_plan_scope(plan):
    """Return 'department', 'site', or 'client' based on plan FK fields."""
    if plan.department_id is not None:
        return 'department'
    if plan.site_id is not None:
        return 'site'
    return 'client'


# ─── Budget reservation services ─────────────────────────────────────────────

def calculate_mrf_reservation_amount(mrf, budget_plan):
    """
    Sum the reservation amount for an MRF against a specific budget_plan.

    Includes line items that reference this budget_plan OR have no budget_plan
    (treating no budget_plan as inheriting from the MRF's budget_plan).

    Per-line amount priority:
      1. budget_max
      2. budget_min
      3. headcount * billing_rate_snapshot
      4. raise BudgetReservationError

    Raises BudgetReservationError if no matching line items or total <= 0.
    """
    from django.db.models import Q
    from apps.mrf.models import MRFLineItem
    from .exceptions import BudgetReservationError

    line_items = list(
        MRFLineItem.objects.filter(mrf=mrf).filter(
            Q(budget_plan=budget_plan) | Q(budget_plan__isnull=True)
        )
    )

    if not line_items:
        raise BudgetReservationError(
            'Cannot reserve budget because this MRF has no budgeted line items.'
        )

    total = Decimal('0.00')
    for li in line_items:
        if li.budget_max is not None:
            total += li.budget_max
        elif li.budget_min is not None:
            total += li.budget_min
        elif li.billing_rate_snapshot is not None and li.headcount:
            total += Decimal(str(li.headcount)) * li.billing_rate_snapshot
        elif (
            li.site_role_requirement_id
            and li.site_role_requirement.billing_rate is not None
            and li.headcount
        ):
            total += Decimal(str(li.headcount)) * li.site_role_requirement.billing_rate
        else:
            raise BudgetReservationError(
                f'Line item {li.pk} (job role {li.job_role_id}) has no budget amount. '
                f'Set budget_max, budget_min, or billing_rate_snapshot.'
            )

    if total <= 0:
        raise BudgetReservationError(
            f'Calculated reservation amount ({total}) is not positive.'
        )

    return total


def mrf_has_budgetable_line_items(mrf):
    """
    Return True when at least one MRF line item can produce a reservation amount.
    """
    for li in mrf.line_items.select_related('site_role_requirement').all():
        if li.budget_max is not None or li.budget_min is not None:
            return True
        if li.billing_rate_snapshot is not None and li.headcount:
            return True
        if (
            li.site_role_requirement_id
            and li.site_role_requirement.billing_rate is not None
            and li.headcount
        ):
            return True
    return False


def reserve_budget_for_mrf(mrf, actor):
    """
    Create a budget reservation for an MRF when its workflow starts.

    For MRFs with no explicit budget_plan, auto-resolves the plan from the
    relevant billable or non-billable budget context and saves it before
    reserving.
    Idempotent: returns the existing reserved reservation if one already exists.
    Raises BudgetReservationError if resolution, validation, or calculation fails.
    """
    from django.utils import timezone
    from .models import BudgetReservation
    from .exceptions import BudgetReservationError

    if not mrf.budget_plan_id:
        if mrf.billing_type == 'billable':
            if not mrf_has_budgetable_line_items(mrf):
                return None
            # Auto-resolve — raises BudgetReservationError if not found/ambiguous
            budget_plan = resolve_budget_plan_for_mrf(mrf)
            mrf.budget_plan = budget_plan
            mrf.save(update_fields=['budget_plan', 'updated_at'])
        elif mrf.billing_type == 'non_billable':
            budget_plan = resolve_non_billable_budget_plan_for_mrf(mrf)
            mrf.budget_plan = budget_plan
            mrf.save(update_fields=['budget_plan', 'updated_at'])
        else:
            return None
    else:
        budget_plan = mrf.budget_plan

    # Validate budget plan is still appropriate for this MRF
    dept_ids = [mrf.required_department_id] if mrf.required_department_id else []
    errors = validate_budget_plan_for_context(
        budget_plan,
        org=mrf.org,
        billing_type=mrf.billing_type,
        site=mrf.site if mrf.billing_type == 'billable' else None,
        department_ids=dept_ids if mrf.billing_type == 'non_billable' else None,
        require_budget_type='hiring' if mrf.billing_type == 'non_billable' else None,
    )
    if errors:
        raise BudgetReservationError(
            f'Budget plan is no longer valid for this MRF: {next(iter(errors.values()))}'
        )

    # Idempotent: return existing reserved reservation
    existing_reserved = BudgetReservation.objects.filter(
        mrf=mrf, budget_plan=budget_plan, status='reserved',
    ).first()
    if existing_reserved:
        return existing_reserved

    # If already committed (e.g. previous workflow somehow approved), skip silently
    if BudgetReservation.objects.filter(
        mrf=mrf, budget_plan=budget_plan, status='committed',
    ).exists():
        return BudgetReservation.objects.filter(
            mrf=mrf, budget_plan=budget_plan, status='committed',
        ).first()

    amount = calculate_mrf_reservation_amount(mrf, budget_plan)

    totals = get_budget_plan_totals(budget_plan)
    if amount > totals['available_amount']:
        raise BudgetReservationError(
            f'Insufficient budget: required {amount}, '
            f'available {totals["available_amount"]}.'
        )

    now = timezone.now()

    return BudgetReservation.objects.create(
        org=mrf.org,
        budget_plan=budget_plan,
        mrf=mrf,
        amount=amount,
        status='reserved',
        reserved_by=actor,
        reserved_at=now,
    )


def commit_mrf_budget_reservations(mrf):
    """
    Mark all reserved reservations for an MRF as committed (workflow approved).
    No-op if no reserved reservations exist.
    """
    from django.utils import timezone
    from .models import BudgetReservation

    now = timezone.now()
    BudgetReservation.objects.filter(
        mrf=mrf, status='reserved',
    ).update(status='committed', committed_at=now)


def release_mrf_budget_reservations(mrf, note=''):
    """
    Mark all reserved reservations for an MRF as released (workflow finally rejected).
    No-op if no reserved reservations exist.
    """
    from django.utils import timezone
    from .models import BudgetReservation

    now = timezone.now()
    update_kwargs = {'status': 'released', 'released_at': now}
    if note:
        update_kwargs['note'] = note
    BudgetReservation.objects.filter(
        mrf=mrf, status='reserved',
    ).update(**update_kwargs)

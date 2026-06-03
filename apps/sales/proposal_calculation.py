"""
apps/sales/proposal_calculation.py

Centralized sales proposal budget and salary/statutory breakup calculations.

Proposal generation reads component percentages and fixed amounts from active
ProposalComponentRule rows. The constants below are retained only for seeding
starter DB rules and for low-level salary helper tests.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

# Statutory / payroll starter constants used by seed_default_proposal_component_rules.

# Starter earnings (% of Basic unless noted)
DA_PCT_OF_BASIC = Decimal('0.50')
HRA_PCT_OF_BASIC = Decimal('0.40')
WASHING_ALLOWANCE_FIXED = Decimal('500.00')
OTHER_ALLOWANCE_PCT_OF_BASIC = Decimal('0.10')

# Employee deductions
EMPLOYEE_PF_PCT_OF_BASIC = Decimal('0.12')
EMPLOYEE_ESIC_PCT_OF_GROSS = Decimal('0.0075')
PROFESSIONAL_TAX_FIXED = Decimal('200.00')

# Employer contributions / statutory load
EMPLOYER_PF_PCT_OF_BASIC = Decimal('0.13')
EMPLOYER_ESIC_PCT_OF_GROSS = Decimal('0.0325')
BONUS_PCT_OF_BASIC = Decimal('0.0833')
LEAVE_SALARY_PCT_OF_BASIC = Decimal('0.0481')
GRATUITY_PCT_OF_BASIC = Decimal('0.0481')
NH_FH_PCT_OF_BASIC = Decimal('0.02')
LWF_FIXED = Decimal('50.00')
UNIFORM_FIXED = Decimal('300.00')
BGC_PCC_FIXED = Decimal('150.00')
PAYROLL_COMPLIANCE_FIXED = Decimal('400.00')
TOOLS_EQUIPMENT_FIXED = Decimal('250.00')

DEFAULT_MANAGEMENT_FEE_PERCENT = Decimal('10.00')
DEFAULT_GST_PERCENT = Decimal('18.00')

MONEY_QUANT = Decimal('0.01')

# Proposal statuses that must not be recalculated in place
NON_REGENERATABLE_PROPOSAL_STATUSES = frozenset({
    'locked',
    'sent_to_client',
    'client_approved',
    'client_negotiation',
    'client_rejected',
    'client_revision_required',
    'submitted_internal',
    'internally_approved',
})


# Proposal statuses that lock the proposal against any direct mutation.
# Includes everything in NON_REGENERATABLE_PROPOSAL_STATUSES. A proposal is also
# considered locked when a MobilisationSetupRequest references it (implicit
# "converted" state) - that check is in is_proposal_locked().
LOCKED_PROPOSAL_STATUSES = NON_REGENERATABLE_PROPOSAL_STATUSES


def is_proposal_locked(proposal):
    """Return True when direct mutation of this proposal (or its lines) is forbidden.

    Locked when:
    1. proposal.status is in LOCKED_PROPOSAL_STATUSES, or
    2. a MobilisationSetupRequest references this proposal as source_proposal_version
       (implicit "converted" state).
    """
    if proposal is None:
        return False
    if proposal.status in LOCKED_PROPOSAL_STATUSES:
        return True
    try:
        from apps.mobilisation.models import MobilisationSetupRequest
    except Exception:  # pragma: no cover - mobilisation app should always load
        return False
    return MobilisationSetupRequest.objects.filter(
        source_proposal_version=proposal,
    ).exists()


def assert_proposal_editable(proposal):
    """Raise rest_framework.exceptions.ValidationError if the proposal is locked.

    Use in ViewSet perform_create/update/destroy hooks for ProposalVersion,
    ProposalBudgetLine, and ProposalBreakupLine.
    """
    from rest_framework.exceptions import ValidationError

    if is_proposal_locked(proposal):
        raise ValidationError(
            'Proposal is locked and cannot be edited. '
            'Create a new revision via clone instead.'
        )

BREAKUP_COMPONENT_SPECS = [
    # (name, component_type, kind)  kind used in builder
    ('Basic', 'earning', 'basic'),
    ('DA', 'earning', 'da'),
    ('HRA', 'earning', 'hra'),
    ('Washing Allowance', 'earning', 'washing'),
    ('Other Allowance', 'earning', 'other_allowance'),
    ('Gross Salary', 'total', 'gross'),
    ('Employee PF', 'employee_deduction', 'ee_pf'),
    ('Employee ESIC', 'employee_deduction', 'ee_esic'),
    ('Professional Tax', 'employee_deduction', 'ee_pt'),
    ('Net Salary', 'total', 'net'),
    ('Employer PF', 'employer_contribution', 'er_pf'),
    ('Employer ESIC', 'employer_contribution', 'er_esic'),
    ('Bonus', 'employer_contribution', 'bonus'),
    ('Leave Salary', 'employer_contribution', 'leave'),
    ('Gratuity', 'statutory', 'gratuity'),
    ('NH/FH', 'statutory', 'nh_fh'),
    ('LWF', 'statutory', 'lwf'),
    ('Uniform', 'equipment', 'uniform'),
    ('BGC/PCC', 'statutory', 'bgc'),
    ('Payroll / Compliance / Digitalization', 'statutory', 'payroll_compliance'),
    ('Tools / Equipment', 'equipment', 'tools'),
    ('Role Monthly Total', 'total', 'role_total'),
]

# Kinds that are pure totals — never sourced from rules.
TOTAL_KINDS = frozenset({'basic', 'gross', 'net', 'role_total'})

# Kinds that a complete ruleset must cover.
RULE_KINDS = frozenset(
    kind for _, _, kind in BREAKUP_COMPONENT_SPECS if kind not in TOTAL_KINDS
)


def _money(value):
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def get_wage_rate_for_requirement(role_requirement, on_date=None):
    """
    Resolve MinimumWageRate for a SalesRoleRequirement using site.location_area + wage_category.

    Uses apps.wages.services.get_applicable_minimum_wage (effective date aware).
    """
    from apps.wages.services import get_applicable_minimum_wage

    if not role_requirement.wage_category_id:
        raise ValueError(
            f'Role requirement #{role_requirement.pk} has no wage_category set.'
        )

    site = role_requirement.site
    location = site.location_area if site else None
    if location is None and site:
        loc_label = f"{site.state or ''}/{site.city or ''}".strip('/') or site.site_name
    elif location:
        loc_label = location.name
    else:
        loc_label = '(no location)'

    wage_cat = role_requirement.wage_category
    on_date = on_date or timezone.now().date()
    org = role_requirement.lead.org if role_requirement.lead_id else None

    rate = get_applicable_minimum_wage(
        wage_category=wage_cat,
        on_date=on_date,
        location=location,
        state=site.state if site else None,
        city=site.city if site else None,
        role=role_requirement.job_role,
        org=org,
    )
    if rate is None:
        raise ValueError(
            f'No wage rate configured for {loc_label} / {wage_cat.name}'
        )
    return rate


# Default code-constant percentages (×100, to match rule.percentage scale).
_DEFAULT_PERCENT_BY_KIND = {
    'da': DA_PCT_OF_BASIC * 100,
    'hra': HRA_PCT_OF_BASIC * 100,
    'other_allowance': OTHER_ALLOWANCE_PCT_OF_BASIC * 100,
    'ee_pf': EMPLOYEE_PF_PCT_OF_BASIC * 100,
    'ee_esic': EMPLOYEE_ESIC_PCT_OF_GROSS * 100,
    'er_pf': EMPLOYER_PF_PCT_OF_BASIC * 100,
    'er_esic': EMPLOYER_ESIC_PCT_OF_GROSS * 100,
    'bonus': BONUS_PCT_OF_BASIC * 100,
    'leave': LEAVE_SALARY_PCT_OF_BASIC * 100,
    'gratuity': GRATUITY_PCT_OF_BASIC * 100,
    'nh_fh': NH_FH_PCT_OF_BASIC * 100,
}


_DEFAULT_FIXED_BY_KIND = {
    'washing': WASHING_ALLOWANCE_FIXED,
    'ee_pt': PROFESSIONAL_TAX_FIXED,
    'lwf': LWF_FIXED,
    'uniform': UNIFORM_FIXED,
    'bgc': BGC_PCC_FIXED,
    'payroll_compliance': PAYROLL_COMPLIANCE_FIXED,
    'tools': TOOLS_EQUIPMENT_FIXED,
}

_GROSS_PERCENT_RULE_KINDS = frozenset({'ee_esic', 'er_esic'})


class ProposalComponentRulesNotConfigured(ValueError):
    """Raised when proposal generation lacks a complete active DB ruleset."""

    def __init__(self, missing_codes):
        self.missing_codes = tuple(sorted(missing_codes))
        missing = ', '.join(self.missing_codes) or 'none'
        super().__init__(
            'Proposal component rules are not configured for this organization. '
            f'Missing active rules: {missing}.'
        )


def _active_component_rules_queryset(org, on_date):
    from apps.sales.models import ProposalComponentRule

    return ProposalComponentRule.objects.filter(
        is_active=True,
    ).filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=on_date),
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=on_date),
    ).filter(
        Q(org__isnull=True) | Q(org=org),
    )


def _merge_component_rules(qs):
    by_code = {}
    for rule in qs:
        existing = by_code.get(rule.code)
        # Org-specific (existing.org_id is not None) wins over global.
        if existing is None:
            by_code[rule.code] = rule
        elif existing.org_id is None and rule.org_id is not None:
            by_code[rule.code] = rule
    return by_code


def _load_org_component_ruleset(org, on_date):
    """
    Merge org-specific rules with global rules. Return the merged
    `{kind: ProposalComponentRule}` dict only when it covers every RULE_KIND;
    otherwise return None. Production generation uses the required loader and
    fails fast on missing rules. Org-specific rules win per code.
    """
    by_code = _merge_component_rules(_active_component_rules_queryset(org, on_date))

    if not RULE_KINDS.issubset(by_code.keys()):
        return None
    return by_code


def _load_required_org_component_ruleset(org, on_date):
    """Return the complete active ruleset or raise a user-facing error."""
    by_code = _merge_component_rules(_active_component_rules_queryset(org, on_date))
    missing = RULE_KINDS.difference(by_code.keys())
    if missing:
        raise ProposalComponentRulesNotConfigured(missing)
    return by_code


def seed_default_proposal_component_rules(org=None, *, overwrite=False, effective_from=None):
    """Seed DB component rules matching the legacy calculation constants.

    These rows make proposal calculation explicit and editable through the
    ProposalComponentRule admin/API. They are provided as starter defaults only;
    production proposal generation still reads from DB rules.
    """
    from apps.sales.models import ProposalComponentRule

    effective_from = effective_from or date.today()
    counts = {'created': 0, 'updated': 0, 'unchanged': 0}

    for sort_order, (name, component_type, kind) in enumerate(BREAKUP_COMPONENT_SPECS, start=1):
        if kind not in RULE_KINDS:
            continue

        defaults = {
            'component_name': name,
            'component_type': component_type,
            'sort_order': sort_order,
            'is_active': True,
            'effective_from': effective_from,
            'effective_to': None,
            'remarks': 'Seeded default proposal calculation rule.',
            'percentage': None,
            'fixed_amount': None,
            'base_component_code': '',
        }
        if kind in _DEFAULT_FIXED_BY_KIND:
            defaults.update({
                'calculation_type': 'fixed',
                'fixed_amount': _money(_DEFAULT_FIXED_BY_KIND[kind]),
            })
        elif kind in _DEFAULT_PERCENT_BY_KIND:
            defaults.update({
                'calculation_type': (
                    'percent_of_gross'
                    if kind in _GROSS_PERCENT_RULE_KINDS
                    else 'percent_of_basic'
                ),
                'percentage': _money(_DEFAULT_PERCENT_BY_KIND[kind]),
            })
        else:  # pragma: no cover - guarded by RULE_KINDS/default maps parity.
            raise ValueError(f"No default rule mapping for '{kind}'.")

        rule, created = ProposalComponentRule.objects.get_or_create(
            org=org,
            code=kind,
            defaults=defaults,
        )
        if created:
            counts['created'] += 1
            continue
        if overwrite:
            for field, value in defaults.items():
                setattr(rule, field, value)
            rule.save(update_fields=[*defaults.keys(), 'updated_at'])
            counts['updated'] += 1
        else:
            counts['unchanged'] += 1

    return counts


def _rule_amount(rule, *, basic, gross, others):
    """Resolve a single rule into a money Decimal."""
    if rule.calculation_type == 'fixed':
        return _money(rule.fixed_amount or 0)
    pct = Decimal(rule.percentage or 0)
    if rule.calculation_type == 'percent_of_basic':
        return _money(basic * pct / Decimal('100'))
    if rule.calculation_type == 'percent_of_gross':
        return _money(gross * pct / Decimal('100'))
    if rule.calculation_type == 'percent_of_other':
        base_val = others.get(rule.base_component_code)
        if base_val is None:
            raise ValueError(
                f"Rule '{rule.code}' references unknown base "
                f"'{rule.base_component_code}'."
            )
        return _money(Decimal(base_val) * pct / Decimal('100'))
    raise ValueError(f"Unknown calculation_type '{rule.calculation_type}'.")


def _rule_percent(rule):
    """Percentage to surface on the breakup line (None for fixed rules)."""
    if rule.calculation_type == 'fixed':
        return None
    return _money(Decimal(rule.percentage or 0))


def _resolve_monthly_basic(role_requirement, wage_rate):
    """Resolve monthly Basic from wage master data.

    Prefer the wage master's monthly amount. If an org only maintains daily
    rates, derive monthly Basic from daily_wage x requirement working_days,
    falling back to 26 days.
    """
    monthly_wage = Decimal(wage_rate.monthly_wage or 0)
    if monthly_wage > 0:
        return _money(monthly_wage)

    daily_wage = Decimal(wage_rate.daily_wage or 0)
    if daily_wage <= 0:
        return _money('0')

    working_days = getattr(role_requirement, 'working_days', None) or Decimal('26')
    return _money(daily_wage * Decimal(working_days))


def build_salary_breakup(role_requirement, wage_rate, ruleset=None):
    """
    Build salary/statutory component amounts for one role requirement.

    When `ruleset` is provided (a {kind: ProposalComponentRule} dict covering
    every RULE_KIND), amounts and percentages are sourced from the rules.
    With `ruleset=None`, this low-level helper uses starter constants. Proposal
    generation always supplies a complete DB ruleset.

    Returns list of dicts:
      component_name, component_type, amount, percentage (optional), sort_order
    """
    basic = _resolve_monthly_basic(role_requirement, wage_rate)
    # `others` tracks already-computed amounts by kind, so `percent_of_other`
    # rules can reference earlier components.
    others = {'basic': basic}

    def get(kind):
        if ruleset is not None and kind in ruleset:
            amt = _rule_amount(ruleset[kind], basic=basic, gross=others.get('gross', basic), others=others)
        else:
            amt = _default_amount(kind, basic)
        others[kind] = amt
        return amt

    da = get('da')
    hra = get('hra')
    washing = get('washing')
    other = get('other_allowance')
    gross = _money(basic + da + hra + washing + other)
    others['gross'] = gross

    ee_pf = get('ee_pf')
    ee_esic = get('ee_esic')
    ee_pt = get('ee_pt')
    net = _money(gross - ee_pf - ee_esic - ee_pt)
    others['net'] = net

    er_pf = get('er_pf')
    er_esic = get('er_esic')
    bonus = get('bonus')
    leave = get('leave')
    gratuity = get('gratuity')
    nh_fh = get('nh_fh')
    lwf = get('lwf')
    uniform = get('uniform')
    bgc = get('bgc')
    payroll = get('payroll_compliance')
    tools = get('tools')

    role_total = _money(
        net + er_pf + er_esic + bonus + leave + gratuity + nh_fh + lwf
        + uniform + bgc + payroll + tools
    )
    others['role_total'] = role_total

    amounts_by_kind = {
        'basic': basic, 'da': da, 'hra': hra, 'washing': washing,
        'other_allowance': other, 'gross': gross,
        'ee_pf': ee_pf, 'ee_esic': ee_esic, 'ee_pt': ee_pt,
        'net': net,
        'er_pf': er_pf, 'er_esic': er_esic, 'bonus': bonus, 'leave': leave,
        'gratuity': gratuity, 'nh_fh': nh_fh, 'lwf': lwf, 'uniform': uniform,
        'bgc': bgc, 'payroll_compliance': payroll, 'tools': tools,
        'role_total': role_total,
    }

    lines = []
    for idx, (name, comp_type, kind) in enumerate(BREAKUP_COMPONENT_SPECS, start=1):
        entry = {
            'component_name': name,
            'component_type': comp_type,
            'amount': amounts_by_kind[kind],
            'sort_order': idx,
        }
        if ruleset is not None and kind in ruleset:
            pct = _rule_percent(ruleset[kind])
            if pct is not None:
                entry['percentage'] = pct
        elif kind in _DEFAULT_PERCENT_BY_KIND:
            entry['percentage'] = _money(_DEFAULT_PERCENT_BY_KIND[kind])
        lines.append(entry)
    return lines


def _default_amount(kind, basic):
    """Default amount for a component kind using the legacy code constants."""
    if kind == 'da':
        return _money(basic * DA_PCT_OF_BASIC)
    if kind == 'hra':
        return _money(basic * HRA_PCT_OF_BASIC)
    if kind == 'washing':
        return _money(WASHING_ALLOWANCE_FIXED)
    if kind == 'other_allowance':
        return _money(basic * OTHER_ALLOWANCE_PCT_OF_BASIC)
    if kind == 'ee_pf':
        return _money(basic * EMPLOYEE_PF_PCT_OF_BASIC)
    if kind == 'ee_esic':
        # `ee_esic` defaults to percent-of-gross but gross isn't known yet — for
        # the default path we recompute using legacy semantics: gross is built
        # from basic+da+hra+washing+other_allowance using the same defaults.
        da = _money(basic * DA_PCT_OF_BASIC)
        hra = _money(basic * HRA_PCT_OF_BASIC)
        washing = _money(WASHING_ALLOWANCE_FIXED)
        other = _money(basic * OTHER_ALLOWANCE_PCT_OF_BASIC)
        gross = _money(basic + da + hra + washing + other)
        return _money(gross * EMPLOYEE_ESIC_PCT_OF_GROSS)
    if kind == 'ee_pt':
        return _money(PROFESSIONAL_TAX_FIXED)
    if kind == 'er_pf':
        return _money(basic * EMPLOYER_PF_PCT_OF_BASIC)
    if kind == 'er_esic':
        da = _money(basic * DA_PCT_OF_BASIC)
        hra = _money(basic * HRA_PCT_OF_BASIC)
        washing = _money(WASHING_ALLOWANCE_FIXED)
        other = _money(basic * OTHER_ALLOWANCE_PCT_OF_BASIC)
        gross = _money(basic + da + hra + washing + other)
        return _money(gross * EMPLOYER_ESIC_PCT_OF_GROSS)
    if kind == 'bonus':
        return _money(basic * BONUS_PCT_OF_BASIC)
    if kind == 'leave':
        return _money(basic * LEAVE_SALARY_PCT_OF_BASIC)
    if kind == 'gratuity':
        return _money(basic * GRATUITY_PCT_OF_BASIC)
    if kind == 'nh_fh':
        return _money(basic * NH_FH_PCT_OF_BASIC)
    if kind == 'lwf':
        return _money(LWF_FIXED)
    if kind == 'uniform':
        return _money(UNIFORM_FIXED)
    if kind == 'bgc':
        return _money(BGC_PCC_FIXED)
    if kind == 'payroll_compliance':
        return _money(PAYROLL_COMPLIANCE_FIXED)
    if kind == 'tools':
        return _money(TOOLS_EQUIPMENT_FIXED)
    raise ValueError(f"Unknown default kind '{kind}'.")


def calculate_role_unit_cost(role_requirement, breakup_lines):
    """Per-person monthly cost = Role Monthly Total component."""
    for line in breakup_lines:
        if line['component_name'] == 'Role Monthly Total':
            return line['amount']
    raise ValueError('Breakup lines missing Role Monthly Total.')


def calculate_management_fee(subtotal, percent):
    if percent is None:
        percent = Decimal('0')
    return _money(Decimal(subtotal) * Decimal(percent) / Decimal('100'))


def calculate_gst(amount, enabled, percent=None):
    if not enabled:
        return _money('0')
    pct = Decimal(percent if percent is not None else DEFAULT_GST_PERCENT)
    return _money(Decimal(amount) * pct / Decimal('100'))


def assert_proposal_regeneratable(proposal):
    if proposal.status in NON_REGENERATABLE_PROPOSAL_STATUSES:
        raise ValueError(
            f'Cannot recalculate proposal in status "{proposal.status}". '
            f'Create a revision clone instead.'
        )


@transaction.atomic
def generate_proposal_lines_from_requirements(proposal, requirements, force=False, ruleset=None):
    """
    Create ProposalBudgetLine and ProposalBreakupLine rows from role requirements.

    Deletes existing non-overridden lines on this proposal unless force=False and
    lines are skipped individually when is_manual_override=True.

    `ruleset` (optional) is a {kind: ProposalComponentRule} mapping. When
    omitted, the org's complete active ruleset is loaded automatically.
    """
    from apps.sales.models import ProposalBudgetLine, ProposalBreakupLine

    assert_proposal_regeneratable(proposal)

    if ruleset is None:
        org = proposal.lead.org if proposal.lead_id else None
        ruleset = _load_required_org_component_ruleset(org, timezone.now().date())

    if force:
        proposal.budget_lines.all().delete()
        proposal.breakup_lines.all().delete()
    else:
        proposal.budget_lines.filter(is_manual_override=False).delete()
        proposal.breakup_lines.filter(is_manual_override=False).delete()

    budget_sort = 1
    for req in requirements:
        if not force and ProposalBudgetLine.objects.filter(
            proposal_version=proposal, role_requirement=req, is_manual_override=True,
        ).exists():
            continue

        wage_rate = get_wage_rate_for_requirement(req)
        breakup_data = build_salary_breakup(req, wage_rate, ruleset=ruleset)
        unit_cost = calculate_role_unit_cost(req, breakup_data)
        manpower = req.manpower_count
        total_cost = _money(unit_cost * manpower)

        site_name = req.site.site_name if req.site_id else 'Site'
        job_name = req.job_role.name if req.job_role_id else 'Role'
        remarks_parts = []
        if req.wage_category_id:
            remarks_parts.append(f'Wage: {req.wage_category.name}')
        if req.shift_hours:
            remarks_parts.append(f'Shift: {req.shift_hours}h')
        remarks = '; '.join(remarks_parts)

        ProposalBudgetLine.objects.create(
            proposal_version=proposal,
            site=req.site,
            role_requirement=req,
            service_category=req.service_category or 'manpower',
            job_role=req.job_role,
            description=f'{site_name} - {job_name}',
            manpower_count=manpower,
            unit_cost=unit_cost,
            total_cost=total_cost,
            remarks=remarks,
            sort_order=budget_sort,
        )
        budget_sort += 1

        for bl in breakup_data:
            ProposalBreakupLine.objects.create(
                proposal_version=proposal,
                site=req.site,
                role_requirement=req,
                job_role=req.job_role,
                component_name=bl['component_name'],
                component_type=bl['component_type'],
                percentage=bl.get('percentage'),
                amount=bl['amount'],
                sort_order=bl['sort_order'] + (budget_sort - 1) * 100,
            )

    return proposal


def calculate_proposal_for_lead(lead, proposal, force=False):
    """
    Full calculation pass: lines, subtotal, management fee, GST, grand total, manpower.
    """
    from apps.sales.models import SalesRoleRequirement

    assert_proposal_regeneratable(proposal)

    requirements = list(
        SalesRoleRequirement.objects.filter(lead=lead, is_active=True, manpower_count__gt=0)
        .select_related('job_role', 'site', 'site__location_area', 'wage_category', 'lead__org')
        .order_by('site_id', 'job_role__name')
    )
    if not requirements:
        raise ValueError('Lead has no active role requirements with manpower.')

    ruleset = _load_required_org_component_ruleset(lead.org, timezone.now().date())
    generate_proposal_lines_from_requirements(
        proposal, requirements, force=force, ruleset=ruleset,
    )

    subtotal = _money(
        sum(line.total_cost for line in proposal.budget_lines.all())
    )
    mgmt_pct = proposal.management_fee_percent
    if mgmt_pct is None:
        mgmt_pct = DEFAULT_MANAGEMENT_FEE_PERCENT
    management_fee = calculate_management_fee(subtotal, mgmt_pct)
    taxable = subtotal + management_fee
    gst_amount = calculate_gst(taxable, proposal.gst_applicable)
    grand_total = _money(subtotal + management_fee + gst_amount)
    manpower_total = sum(r.manpower_count for r in requirements)

    proposal.subtotal_amount = subtotal
    proposal.management_fee_amount = management_fee
    proposal.gst_amount = gst_amount
    proposal.grand_total = grand_total
    proposal.manpower_total = manpower_total
    proposal.save(update_fields=[
        'subtotal_amount', 'management_fee_amount', 'gst_amount',
        'grand_total', 'manpower_total', 'updated_at',
    ])
    return proposal

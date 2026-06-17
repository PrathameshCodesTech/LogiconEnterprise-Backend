"""
apps/mrf/serializers.py

Management serializers for ManpowerRequest and MRFLineItem.

Read serializers   — returned from all endpoints (list/retrieve/create/update responses).
Write serializers  — used for create/update request bodies; org and requested_by are injected
                     by the view, not accepted from the client.
"""

from datetime import date

from rest_framework import serializers

from apps.budgets.services import validate_budget_plan_for_context
from apps.core.models import Department

from .models import ManpowerRequest, MRFLineItem, MRFSupportRequirement


# ─── MRF Line Item ────────────────────────────────────────────────────────────

class MRFLineItemSerializer(serializers.ModelSerializer):
    """Read serializer — all fields read-only."""

    # ── Commercial override / effective value fields ──────────────────────────
    effective_wage_min = serializers.SerializerMethodField()
    effective_wage_max = serializers.SerializerMethodField()
    commercial_overridden_by_name = serializers.SerializerMethodField()
    approved_billing_rate = serializers.SerializerMethodField()
    requested_billing_rate = serializers.SerializerMethodField()
    billing_rate_variance = serializers.SerializerMethodField()
    is_over_approved_billing_rate = serializers.SerializerMethodField()
    line_approved_amount = serializers.SerializerMethodField()
    line_requested_amount = serializers.SerializerMethodField()

    budget_plan_name = serializers.CharField(source='budget_plan.name', read_only=True, default=None)
    budget_plan_code = serializers.CharField(source='budget_plan.code', read_only=True, default=None)
    budget_plan_amount = serializers.DecimalField(
        source='budget_plan.amount', max_digits=14, decimal_places=2, read_only=True, default=None,
    )
    budget_plan_currency = serializers.CharField(source='budget_plan.currency', read_only=True, default=None)
    budget_plan_status = serializers.CharField(source='budget_plan.status', read_only=True, default=None)
    budget_plan_nature = serializers.CharField(source='budget_plan.budget_nature', read_only=True, default=None)

    # SRR display/snapshot fields
    site_role_requirement_label = serializers.SerializerMethodField()
    srr_department_name = serializers.CharField(
        source='site_role_requirement.department.name', read_only=True, default=None,
    )
    srr_approved_headcount = serializers.IntegerField(
        source='site_role_requirement.approved_headcount', read_only=True, default=None,
    )
    srr_remaining_headcount = serializers.SerializerMethodField()
    srr_wage_min = serializers.DecimalField(
        source='site_role_requirement.wage_min', max_digits=10, decimal_places=2,
        read_only=True, default=None,
    )
    srr_wage_max = serializers.DecimalField(
        source='site_role_requirement.wage_max', max_digits=10, decimal_places=2,
        read_only=True, default=None,
    )
    srr_billing_rate = serializers.DecimalField(
        source='site_role_requirement.billing_rate', max_digits=12, decimal_places=2,
        read_only=True, default=None,
    )
    srr_shift_hours = serializers.DecimalField(
        source='site_role_requirement.shift_hours', max_digits=4, decimal_places=1,
        read_only=True, default=None,
    )

    class Meta:
        model = MRFLineItem
        fields = [
            'id', 'mrf', 'site_role_requirement', 'job_role', 'headcount',
            'replacement_for_employee', 'required_skills', 'wage_category',
            'min_wage_snapshot', 'wage_min_requested', 'wage_max_requested',
            'billing_rate_snapshot', 'budget_min', 'budget_max',
            # master snapshots
            'master_wage_min_snapshot', 'master_wage_max_snapshot',
            'master_billing_rate_snapshot', 'master_shift_hours_snapshot',
            # override audit
            'commercial_override_enabled', 'commercial_override_reason',
            'commercial_overridden_by', 'commercial_overridden_by_name', 'commercial_overridden_at',
            # effective (resolved) values
            'effective_wage_min', 'effective_wage_max',
            'approved_billing_rate', 'requested_billing_rate',
            'billing_rate_variance', 'is_over_approved_billing_rate',
            'line_approved_amount', 'line_requested_amount',
            'budget_plan', 'budget_plan_name', 'budget_plan_code',
            'budget_plan_amount', 'budget_plan_currency', 'budget_plan_status', 'budget_plan_nature',
            'site_role_requirement_label', 'srr_department_name',
            'srr_approved_headcount', 'srr_remaining_headcount',
            'srr_wage_min', 'srr_wage_max', 'srr_billing_rate', 'srr_shift_hours',
        ]
        read_only_fields = fields

    def get_site_role_requirement_label(self, obj):
        if obj.site_role_requirement_id is None:
            return None
        srr = obj.site_role_requirement
        parts = [str(srr.job_role)]
        if srr.department_id:
            parts.append(srr.department.name)
        parts.append(f'×{srr.approved_headcount}')
        return ' — '.join(parts)

    def get_srr_remaining_headcount(self, obj):
        if obj.site_role_requirement_id is None:
            return None
        from apps.mrf.services import get_billable_headcount_usage
        srr = obj.site_role_requirement
        already = get_billable_headcount_usage(srr.site, obj.job_role, exclude_mrf=obj.mrf)
        return max(0, srr.approved_headcount - already)

    def get_effective_wage_min(self, obj):
        if obj.wage_min_requested is not None:
            return str(obj.wage_min_requested)
        if obj.master_wage_min_snapshot is not None:
            return str(obj.master_wage_min_snapshot)
        return None

    def get_effective_wage_max(self, obj):
        if obj.wage_max_requested is not None:
            return str(obj.wage_max_requested)
        if obj.master_wage_max_snapshot is not None:
            return str(obj.master_wage_max_snapshot)
        return None

    def get_commercial_overridden_by_name(self, obj):
        if obj.commercial_overridden_by_id:
            return obj.commercial_overridden_by.username
        return None

    def _approved_billing_rate_value(self, obj):
        if obj.master_billing_rate_snapshot is not None:
            return obj.master_billing_rate_snapshot
        if obj.site_role_requirement_id and obj.site_role_requirement.billing_rate is not None:
            return obj.site_role_requirement.billing_rate
        return None

    @staticmethod
    def _money(value):
        from decimal import Decimal
        if value is None:
            return None
        return str(Decimal(value).quantize(Decimal('0.01')))

    def get_approved_billing_rate(self, obj):
        return self._money(self._approved_billing_rate_value(obj))

    def get_requested_billing_rate(self, obj):
        return self._money(obj.billing_rate_snapshot)

    def get_billing_rate_variance(self, obj):
        approved = self._approved_billing_rate_value(obj)
        requested = obj.billing_rate_snapshot
        if approved is None or requested is None:
            return None
        return self._money(requested - approved)

    def get_is_over_approved_billing_rate(self, obj):
        approved = self._approved_billing_rate_value(obj)
        requested = obj.billing_rate_snapshot
        if approved is None or requested is None:
            return False
        return requested > approved

    def get_line_approved_amount(self, obj):
        approved = self._approved_billing_rate_value(obj)
        if approved is None or not obj.headcount:
            return None
        return self._money(approved * obj.headcount)

    def get_line_requested_amount(self, obj):
        if obj.billing_rate_snapshot is None or not obj.headcount:
            return None
        return self._money(obj.billing_rate_snapshot * obj.headcount)


def _detect_commercial_override(data, snapshot):
    """Return True if any commercial field in data diverges from the SRR snapshot."""
    from decimal import Decimal

    def _neq(field, snap_key):
        if field not in data:
            return False
        val = data[field]
        snap = snapshot.get(snap_key)
        if val is None or snap is None:
            return False
        return Decimal(str(val)) != Decimal(str(snap))

    return (
        _neq('wage_min_requested', 'wage_min')
        or _neq('wage_max_requested', 'wage_max')
        or _neq('billing_rate_snapshot', 'billing_rate')
    )


def _changed_commercial_fields(data, snapshot):
    """Return commercial field names whose provided value differs from the SRR snapshot."""
    from decimal import Decimal

    changed = set()
    field_map = {
        'wage_min_requested': 'wage_min',
        'wage_max_requested': 'wage_max',
        'billing_rate_snapshot': 'billing_rate',
    }
    for field, snap_key in field_map.items():
        if field not in data:
            continue
        val = data[field]
        snap = snapshot.get(snap_key)
        if val is None or snap is None:
            continue
        if Decimal(str(val)) != Decimal(str(snap)):
            changed.add(field)
    return changed


class MRFLineItemWriteSerializer(serializers.ModelSerializer):
    """Write serializer for line item create/update."""

    commercial_override_reason = serializers.CharField(required=False, allow_blank=True, default='')

    class Meta:
        model = MRFLineItem
        fields = [
            'mrf', 'site_role_requirement', 'job_role', 'headcount',
            'replacement_for_employee', 'required_skills', 'wage_category',
            'wage_min_requested', 'wage_max_requested',
            'billing_rate_snapshot', 'budget_min', 'budget_max',
            'budget_plan',
            'commercial_override_reason',
        ]
        extra_kwargs = {
            'budget_plan': {'required': False, 'allow_null': True},
        }

    def validate_headcount(self, value):
        if value < 1:
            raise serializers.ValidationError("Headcount must be at least 1.")
        return value

    def validate(self, data):
        instance = self.instance

        # Resolve effective mrf and site_role_requirement (for PATCH partial updates)
        mrf = data.get('mrf') or (instance.mrf if instance else None)
        srr = data.get('site_role_requirement', instance.site_role_requirement if instance else None)

        if mrf and srr:
            duplicate_qs = MRFLineItem.objects.filter(mrf=mrf, site_role_requirement=srr)
            if instance is not None:
                duplicate_qs = duplicate_qs.exclude(pk=instance.pk)
            if duplicate_qs.exists():
                raise serializers.ValidationError({
                    'site_role_requirement': 'This approved role is already added to this MRF.'
                })

            # site_role_requirement.site must match MRF.site
            if srr.site_id != mrf.site_id:
                raise serializers.ValidationError({
                    'site_role_requirement': (
                        'SiteRoleRequirement must belong to the same site as the MRF.'
                    )
                })

            # job_role must match srr.job_role
            job_role = data.get('job_role', instance.job_role if instance else None)
            if job_role is not None and job_role.pk != srr.job_role_id:
                raise serializers.ValidationError({
                    'job_role': (
                        f'job_role must match the SiteRoleRequirement job role '
                        f'("{srr.job_role}").'
                    )
                })

            # wage_category must match srr.wage_category when SRR has one
            if srr.wage_category_id is not None:
                wage_category = data.get('wage_category', instance.wage_category if instance else None)
                if wage_category is not None and wage_category.pk != srr.wage_category_id:
                    raise serializers.ValidationError({
                        'wage_category': (
                            f'wage_category must match the SiteRoleRequirement wage category '
                            f'("{srr.wage_category}").'
                        )
                    })

            # SRR department must match MRF required_department when both are set
            if srr.department_id is not None and mrf.required_department_id is not None:
                if srr.department_id != mrf.required_department_id:
                    raise serializers.ValidationError({
                        'site_role_requirement': (
                            'SiteRoleRequirement department does not match the MRF required department.'
                        )
                    })

        # wage range
        wage_min = data.get('wage_min_requested', instance.wage_min_requested if instance else None)
        wage_max = data.get('wage_max_requested', instance.wage_max_requested if instance else None)
        if wage_min is not None and wage_max is not None and wage_min > wage_max:
            raise serializers.ValidationError({
                'wage_min_requested': 'wage_min_requested cannot exceed wage_max_requested.'
            })

        # budget range
        budget_min = data.get('budget_min', instance.budget_min if instance else None)
        budget_max = data.get('budget_max', instance.budget_max if instance else None)
        if budget_min is not None and budget_max is not None and budget_min > budget_max:
            raise serializers.ValidationError({
                'budget_min': 'budget_min cannot exceed budget_max.'
            })

        # budget_plan validation
        # Explicit null → clearing; skip validation
        if not ('budget_plan' in data and data['budget_plan'] is None):
            budget_plan = data.get('budget_plan') or (
                instance.budget_plan if instance and instance.budget_plan_id else None
            )
            if budget_plan is not None and mrf is not None:
                billing_type = mrf.billing_type
                dept_ids = [mrf.required_department_id] if mrf.required_department_id else []
                li_errors = validate_budget_plan_for_context(
                    budget_plan,
                    org=mrf.org,
                    billing_type=billing_type,
                    site=mrf.site if billing_type == 'billable' else None,
                    department_ids=dept_ids if billing_type == 'non_billable' else None,
                    require_budget_type='hiring' if billing_type == 'non_billable' else None,
                )
                if li_errors:
                    raise serializers.ValidationError(li_errors)

        # ── Commercial override enforcement ───────────────────────────────────
        COMMERCIAL_FIELDS = {'wage_min_requested', 'wage_max_requested', 'billing_rate_snapshot'}
        commercial_in_payload = any(f in data for f in COMMERCIAL_FIELDS)
        self._override_result = None

        if commercial_in_payload and mrf and mrf.billing_type == 'billable' and srr:
            from apps.mrf.services import build_line_item_commercial_snapshot
            from apps.access.capabilities import get_user_capabilities, MRF_OVERRIDE_COMMERCIALS
            snapshot = build_line_item_commercial_snapshot(srr)
            override_detected = _detect_commercial_override(data, snapshot)
            user = self.context.get('request').user if self.context.get('request') else None
            if override_detected:
                changed_fields = _changed_commercial_fields(data, snapshot)
                client_rate_exception = (
                    mrf.requested_by_type == 'client'
                    and changed_fields == {'billing_rate_snapshot'}
                )
                if client_rate_exception:
                    data['commercial_override_reason'] = (
                        str(data.get('commercial_override_reason') or '').strip()
                        or 'Client requested billing rate differs from approved budget rate.'
                    )
                    self._override_result = {'detected': True, 'user': user, 'in_payload': True}
                    return data

                user_caps = get_user_capabilities(user) if user else []
                if MRF_OVERRIDE_COMMERCIALS not in user_caps:
                    raise serializers.ValidationError(
                        'You do not have permission to override SRR commercial values.'
                    )
                reason = data.get('commercial_override_reason', '')
                if not str(reason).strip():
                    raise serializers.ValidationError({
                        'commercial_override_reason': (
                            'A reason is required when overriding commercial values.'
                        )
                    })
                self._override_result = {'detected': True, 'user': user, 'in_payload': True}
            else:
                data['commercial_override_reason'] = ''
                self._override_result = {'detected': False, 'user': None, 'in_payload': True}

        return data


# ─── MRF Support Requirement ──────────────────────────────────────────────────

_SUPPORT_FIELDS = [
    'id',
    'laptop_required', 'desktop_required', 'mail_id_required',
    'hrms_login_required', 'outlook_required', 'ms_office_required',
    'windows_required', 'own_or_rental',
    'sim_card_required', 'data_card_required', 'uniform_required',
    'visiting_cards_required', 'seating_required',
    'admin_location', 'admin_other',
    'created_at', 'updated_at',
]


class MRFSupportRequirementSerializer(serializers.ModelSerializer):
    """Read serializer — nested inside ManpowerRequestSerializer."""

    class Meta:
        model = MRFSupportRequirement
        fields = _SUPPORT_FIELDS
        read_only_fields = _SUPPORT_FIELDS


class MRFSupportRequirementWriteSerializer(serializers.ModelSerializer):
    """Write serializer — used both nested (create/update) and standalone."""

    class Meta:
        model = MRFSupportRequirement
        fields = [f for f in _SUPPORT_FIELDS if f not in ('id', 'created_at', 'updated_at')]
        extra_kwargs = {f: {'required': False} for f in [
            'laptop_required', 'desktop_required', 'mail_id_required',
            'hrms_login_required', 'outlook_required', 'ms_office_required',
            'windows_required', 'own_or_rental',
            'sim_card_required', 'data_card_required', 'uniform_required',
            'visiting_cards_required', 'seating_required',
            'admin_location', 'admin_other',
        ]}


# ─── Manpower Request ─────────────────────────────────────────────────────────

class ManpowerRequestSerializer(serializers.ModelSerializer):
    """Read serializer — used for all responses (list, retrieve, create, update)."""
    line_items = MRFLineItemSerializer(many=True, read_only=True)
    support_requirement = MRFSupportRequirementSerializer(read_only=True)
    requesting_department_name = serializers.CharField(
        source='requesting_department.name', read_only=True, default=None,
    )
    requesting_department_code = serializers.CharField(
        source='requesting_department.code', read_only=True, default=None,
    )
    required_department_name = serializers.CharField(
        source='required_department.name', read_only=True, default=None,
    )
    required_department_code = serializers.CharField(
        source='required_department.code', read_only=True, default=None,
    )

    # Budget display fields
    budget_plan_name = serializers.CharField(source='budget_plan.name', read_only=True, default=None)
    budget_plan_code = serializers.CharField(source='budget_plan.code', read_only=True, default=None)
    budget_plan_amount = serializers.DecimalField(
        source='budget_plan.amount', max_digits=14, decimal_places=2, read_only=True, default=None,
    )
    budget_plan_currency = serializers.CharField(source='budget_plan.currency', read_only=True, default=None)
    budget_plan_status = serializers.CharField(source='budget_plan.status', read_only=True, default=None)
    budget_plan_nature = serializers.CharField(source='budget_plan.budget_nature', read_only=True, default=None)

    # ── Budget reservation state (read-only, computed) ───────────────────────
    budget_reserved_amount = serializers.SerializerMethodField()
    budget_committed_amount = serializers.SerializerMethodField()
    budget_reservation_status = serializers.SerializerMethodField()

    # ── Resolved budget context (auto-resolved or explicit budget plan) ───────
    resolved_budget_plan_id = serializers.SerializerMethodField()
    resolved_budget_plan_name = serializers.SerializerMethodField()
    resolved_budget_plan_code = serializers.SerializerMethodField()
    resolved_budget_scope = serializers.SerializerMethodField()
    resolved_budget_total_amount = serializers.SerializerMethodField()
    resolved_budget_reserved_amount = serializers.SerializerMethodField()
    resolved_budget_committed_amount = serializers.SerializerMethodField()
    resolved_budget_available_amount = serializers.SerializerMethodField()
    requested_budget_amount = serializers.SerializerMethodField()
    budget_after_request_available_amount = serializers.SerializerMethodField()

    # ── Workflow state (read-only, computed) ─────────────────────────────────
    workflow_status = serializers.SerializerMethodField()
    workflow_instance_id = serializers.SerializerMethodField()
    workflow_current_step_id = serializers.SerializerMethodField()
    workflow_current_step_code = serializers.SerializerMethodField()
    workflow_current_step_name = serializers.SerializerMethodField()
    workflow_current_assigned_user = serializers.SerializerMethodField()
    workflow_current_assigned_user_name = serializers.SerializerMethodField()
    workflow_current_department_name = serializers.SerializerMethodField()

    class Meta:
        model = ManpowerRequest
        fields = [
            'id', 'org', 'site', 'requested_by', 'requested_by_type',
            'mrf_type', 'status',
            'requesting_department', 'requesting_department_name', 'requesting_department_code',
            'required_department', 'required_department_name', 'required_department_code',
            'department', 'billing_type',
            'required_by_date', 'reason', 'client_visible',
            'request_number',
            'experience_min_years', 'experience_max_years',
            'reporting_to', 'mis_requirement',
            'education_requirement', 'gender_preference', 'special_requirement',
            'salary_range_text', 'ctc_budget_text',
            'reference_note', 'other_remarks',
            'budget_plan', 'budget_plan_name', 'budget_plan_code',
            'budget_plan_amount', 'budget_plan_currency', 'budget_plan_status', 'budget_plan_nature',
            'submitted_at', 'approved_at', 'rejected_at',
            'budget_reserved_amount', 'budget_committed_amount', 'budget_reservation_status',
            'resolved_budget_plan_id', 'resolved_budget_plan_name', 'resolved_budget_plan_code',
            'resolved_budget_scope',
            'resolved_budget_total_amount', 'resolved_budget_reserved_amount',
            'resolved_budget_committed_amount', 'resolved_budget_available_amount',
            'requested_budget_amount', 'budget_after_request_available_amount',
            'line_items', 'support_requirement', 'created_at', 'updated_at',
            'workflow_status', 'workflow_instance_id',
            'workflow_current_step_id', 'workflow_current_step_code', 'workflow_current_step_name',
            'workflow_current_assigned_user', 'workflow_current_assigned_user_name',
            'workflow_current_department_name',
        ]
        read_only_fields = [
            'id', 'org', 'requested_by',
            'submitted_at', 'approved_at', 'rejected_at',
            'created_at', 'updated_at',
        ]

    def _get_workflow(self, obj):
        """Returns the active workflow, or the most recent completed one."""
        if not hasattr(obj, '_cached_wf'):
            all_wf = list(obj.workflow_instances.all())
            active = next((w for w in all_wf if w.status == 'active'), None)
            obj._cached_wf = active or (all_wf[0] if all_wf else None)
        return obj._cached_wf

    def _get_current_step(self, obj):
        """Returns the active step of the active workflow, if any."""
        if not hasattr(obj, '_cached_wf_step'):
            wf = self._get_workflow(obj)
            if wf is None or wf.status != 'active':
                obj._cached_wf_step = None
            else:
                obj._cached_wf_step = wf.steps.filter(status='active').first()
        return obj._cached_wf_step

    def get_workflow_status(self, obj):
        wf = self._get_workflow(obj)
        return wf.status if wf else 'not_started'

    def get_workflow_instance_id(self, obj):
        wf = self._get_workflow(obj)
        return wf.pk if wf else None

    def get_workflow_current_step_id(self, obj):
        step = self._get_current_step(obj)
        return step.pk if step else None

    def get_workflow_current_step_code(self, obj):
        step = self._get_current_step(obj)
        return step.step_code if step else None

    def get_workflow_current_step_name(self, obj):
        step = self._get_current_step(obj)
        return step.step_name if step else None

    def get_workflow_current_assigned_user(self, obj):
        step = self._get_current_step(obj)
        return step.assigned_user_id if step else None

    def get_workflow_current_assigned_user_name(self, obj):
        step = self._get_current_step(obj)
        if step and step.assigned_user:
            return step.assigned_user.username
        return None

    def get_workflow_current_department_name(self, obj):
        step = self._get_current_step(obj)
        return step.assigned_department_name_snapshot if step else None

    def get_budget_reserved_amount(self, obj):
        from decimal import Decimal
        from django.db.models import Sum
        from apps.budgets.models import BudgetReservation
        result = BudgetReservation.objects.filter(
            mrf=obj, status='reserved',
        ).aggregate(total=Sum('amount'))['total']
        val = result or Decimal('0.00')
        return str(val.quantize(Decimal('0.01')))

    def get_budget_committed_amount(self, obj):
        from decimal import Decimal
        from django.db.models import Sum
        from apps.budgets.models import BudgetReservation
        result = BudgetReservation.objects.filter(
            mrf=obj, status='committed',
        ).aggregate(total=Sum('amount'))['total']
        val = result or Decimal('0.00')
        return str(val.quantize(Decimal('0.01')))

    def get_budget_reservation_status(self, obj):
        from apps.budgets.models import BudgetReservation
        res = BudgetReservation.objects.filter(mrf=obj).order_by('-created_at').first()
        return res.status if res else None

    def _resolved(self, obj):
        from apps.mrf.services import get_resolved_budget_context
        return get_resolved_budget_context(obj)

    def get_resolved_budget_plan_id(self, obj):
        return self._resolved(obj)['resolved_budget_plan_id']

    def get_resolved_budget_plan_name(self, obj):
        return self._resolved(obj)['resolved_budget_plan_name']

    def get_resolved_budget_plan_code(self, obj):
        return self._resolved(obj)['resolved_budget_plan_code']

    def get_resolved_budget_scope(self, obj):
        return self._resolved(obj)['resolved_budget_scope']

    def get_resolved_budget_total_amount(self, obj):
        return self._resolved(obj)['resolved_budget_total_amount']

    def get_resolved_budget_reserved_amount(self, obj):
        return self._resolved(obj)['resolved_budget_reserved_amount']

    def get_resolved_budget_committed_amount(self, obj):
        return self._resolved(obj)['resolved_budget_committed_amount']

    def get_resolved_budget_available_amount(self, obj):
        return self._resolved(obj)['resolved_budget_available_amount']

    def get_requested_budget_amount(self, obj):
        return self._resolved(obj)['requested_budget_amount']

    def get_budget_after_request_available_amount(self, obj):
        return self._resolved(obj)['budget_after_request_available_amount']


class ManpowerRequestWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for MRF create/update.

    org and requested_by are injected by the view from the actor context.
    submitted_at / approved_at / rejected_at are managed by workflow transitions (deferred).
    support_requirement is optional nested object; create/update handled in create()/update().
    """

    requesting_department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    required_department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    support_requirement = MRFSupportRequirementWriteSerializer(
        required=False, allow_null=True,
    )

    class Meta:
        model = ManpowerRequest
        fields = [
            'site', 'requested_by_type', 'mrf_type', 'billing_type',
            'requesting_department', 'required_department', 'department',
            'required_by_date', 'reason', 'client_visible', 'status',
            'budget_plan',
            'request_number',
            'experience_min_years', 'experience_max_years',
            'reporting_to', 'mis_requirement',
            'education_requirement', 'gender_preference', 'special_requirement',
            'salary_range_text', 'ctc_budget_text',
            'reference_note', 'other_remarks',
            'support_requirement',
        ]
        extra_kwargs = {
            'site': {'required': False, 'allow_null': True},
            'mrf_type': {'required': False},
            'billing_type': {'required': False},
            'budget_plan': {'required': False, 'allow_null': True},
            'request_number': {'required': False},
            'experience_min_years': {'required': False, 'allow_null': True},
            'experience_max_years': {'required': False, 'allow_null': True},
            'reporting_to': {'required': False},
            'mis_requirement': {'required': False},
            'education_requirement': {'required': False},
            'gender_preference': {'required': False},
            'special_requirement': {'required': False},
            'salary_range_text': {'required': False},
            'ctc_budget_text': {'required': False},
            'reference_note': {'required': False},
            'other_remarks': {'required': False},
        }

    def validate_required_by_date(self, value):
        if value and value < date.today():
            raise serializers.ValidationError("required_by_date cannot be in the past.")
        return value

    def validate_status(self, value):
        # Approval/rejection transitions are handled by dedicated workflow endpoints (Phase 4F+).
        terminal = {'approved', 'rejected'}
        if value in terminal:
            raise serializers.ValidationError(
                f"Cannot set status to '{value}' directly. Use the approval workflow."
            )
        return value

    def validate(self, data):
        data = super().validate(data)
        instance = self.instance

        request = self.context.get('request')
        is_client_actor = False
        if request is not None:
            from apps.access.capabilities import is_client_facing_user
            is_client_actor = is_client_facing_user(request.user)

        requested_by_type = data.get(
            'requested_by_type',
            instance.requested_by_type if instance else None,
        )
        client_requested = is_client_actor or requested_by_type == 'client'
        if client_requested:
            data['requested_by_type'] = 'client'
            data['billing_type'] = 'billable'
            data['client_visible'] = True
            data['mrf_type'] = data.get('mrf_type') or (
                instance.mrf_type if instance else 'new_hiring'
            )
            data['requesting_department'] = None
            data['required_department'] = None
            data['department'] = ''

        site = data.get('site', instance.site if instance else None)
        requesting_department = data.get(
            'requesting_department',
            instance.requesting_department if instance else None,
        )
        required_department = data.get(
            'required_department',
            instance.required_department if instance else None,
        )

        errors = {}

        # ── Experience range validation ───────────────────────────────────────
        exp_min = data.get(
            'experience_min_years',
            instance.experience_min_years if instance else None,
        )
        exp_max = data.get(
            'experience_max_years',
            instance.experience_max_years if instance else None,
        )
        if exp_min is not None and exp_min < 0:
            errors['experience_min_years'] = 'Experience cannot be negative.'
        if exp_max is not None and exp_max < 0:
            errors['experience_max_years'] = 'Experience cannot be negative.'
        if exp_min is not None and exp_max is not None and exp_min > exp_max:
            errors['experience_min_years'] = (
                'experience_min_years cannot exceed experience_max_years.'
            )

        mrf_type = data.get('mrf_type', instance.mrf_type if instance else None)
        if client_requested and mrf_type == 'rate_revision':
            errors['mrf_type'] = (
                "Client-requested MRFs cannot use 'rate_revision'."
            )
        elif not mrf_type:
            errors['mrf_type'] = 'mrf_type is required.'

        billing_type = data.get('billing_type', instance.billing_type if instance else None)
        if not billing_type:
            errors['billing_type'] = 'billing_type is required.'

        if not client_requested and billing_type == 'non_billable':
            data['site'] = None
            site = None
            user_department = getattr(request.user, 'department', None) if request is not None else None
            if request is not None and not request.user.is_superuser:
                if user_department is None:
                    errors['requesting_department'] = (
                        'Your user account is not assigned to a department.'
                    )
                    errors['required_department'] = (
                        'Your user account is not assigned to a department.'
                    )
                else:
                    if (
                        requesting_department is not None
                        and requesting_department.pk != user_department.pk
                    ):
                        errors['requesting_department'] = (
                            'Internal MRFs can only be raised from your own department.'
                        )
                    if (
                        required_department is not None
                        and required_department.pk != user_department.pk
                    ):
                        errors['required_department'] = (
                            'Internal MRFs can only request manpower for your own department.'
                        )
                    data['requesting_department'] = user_department
                    data['required_department'] = user_department
                    requesting_department = user_department
                    required_department = user_department
            elif requesting_department is None and user_department is not None:
                data['requesting_department'] = user_department
                requesting_department = user_department
            if required_department is None and user_department is not None:
                data['required_department'] = user_department
                required_department = user_department
            if requesting_department is None:
                errors['requesting_department'] = (
                    'requesting_department is required for non-billable MRFs.'
                )
            if required_department is None:
                errors['required_department'] = (
                    'required_department is required for non-billable MRFs.'
                )
            org = (
                getattr(request.user, 'org', None)
                if request is not None else (instance.org if instance else None)
            )
            self._validate_internal_department(
                requesting_department,
                org,
                'requesting_department',
                errors,
            )
            self._validate_internal_department(
                required_department,
                org,
                'required_department',
                errors,
            )
        elif billing_type == 'billable' and site is None:
            errors['site'] = 'site is required for billable MRFs.'

        # ── request_number uniqueness (belt-and-suspenders over DB constraint) ─
        request_number = data.get(
            'request_number',
            instance.request_number if instance else '',
        )
        if request_number:
            org = site.org if site else (
                getattr(request.user, 'org', None)
                if request is not None else (instance.org if instance else None)
            )
            if org:
                qs = ManpowerRequest.objects.filter(
                    org=org, request_number=request_number,
                )
                if instance:
                    qs = qs.exclude(pk=instance.pk)
                if qs.exists():
                    errors['request_number'] = (
                        'An MRF with this request number already exists in this organization.'
                    )

        if site is not None:
            self._validate_department_for_site(
                requesting_department,
                site,
                'requesting_department',
                errors,
            )
            self._validate_department_for_site(
                required_department,
                site,
                'required_department',
                errors,
            )

        # budget_plan validation
        # Explicit null → clearing; skip validation
        if not ('budget_plan' in data and data['budget_plan'] is None):
            budget_plan = data.get('budget_plan') or (
                instance.budget_plan if instance and instance.budget_plan_id else None
            )
            org = site.org if site else (
                getattr(request.user, 'org', None)
                if request is not None else (instance.org if instance else None)
            )
            if budget_plan is not None and org is not None:
                billing_type = data.get('billing_type') or (instance.billing_type if instance else None)
                if billing_type:
                    dept_ids = [required_department.pk] if required_department else []
                    errors.update(validate_budget_plan_for_context(
                        budget_plan,
                        org=org,
                        billing_type=billing_type,
                        site=site if billing_type == 'billable' else None,
                        department_ids=dept_ids if billing_type == 'non_billable' else None,
                        require_budget_type='hiring' if billing_type == 'non_billable' else None,
                    ))

        if errors:
            raise serializers.ValidationError(errors)
        return data

    def create(self, validated_data):
        support_data = validated_data.pop('support_requirement', None)
        instance = super().create(validated_data)
        if support_data is not None:
            MRFSupportRequirement.objects.create(mrf=instance, **support_data)
        return instance

    def update(self, instance, validated_data):
        support_data = validated_data.pop('support_requirement', None)
        instance = super().update(instance, validated_data)
        if support_data is not None:
            MRFSupportRequirement.objects.update_or_create(
                mrf=instance,
                defaults=support_data,
            )
        return instance

    @staticmethod
    def _validate_department_for_site(department, site, field_name, errors):
        """
        Department must be compatible with the MRF site:
        org-level, same-client, or same-site departments are valid.
        """
        if department is None:
            return
        if department.org_id != site.org_id:
            errors[field_name] = 'Department must belong to the same organization as the site.'
            return
        if department.site_id is not None and department.site_id != site.pk:
            errors[field_name] = 'Department is scoped to a different site.'
            return
        if (
            department.site_id is None
            and department.client_id is not None
            and department.client_id != site.client_id
        ):
            errors[field_name] = 'Department belongs to a different client.'

    @staticmethod
    def _validate_internal_department(department, org, field_name, errors):
        """Internal non-billable MRF departments must be active org-level departments."""
        if department is None or org is None or field_name in errors:
            return
        if department.org_id != org.pk:
            errors[field_name] = 'Department must belong to your organization.'
            return
        if department.client_id is not None or department.site_id is not None:
            errors[field_name] = 'Internal MRFs must use an org-level department.'

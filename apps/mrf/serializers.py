"""
apps/mrf/serializers.py

Management serializers for ManpowerRequest and MRFLineItem.

Read serializers   — returned from all endpoints (list/retrieve/create/update responses).
Write serializers  — used for create/update request bodies; org and requested_by are injected
                     by the view, not accepted from the client.
"""

from datetime import date

from rest_framework import serializers

from apps.core.models import Department

from .models import ManpowerRequest, MRFLineItem


# ─── MRF Line Item ────────────────────────────────────────────────────────────

class MRFLineItemSerializer(serializers.ModelSerializer):
    """Read serializer — all fields read-only."""

    class Meta:
        model = MRFLineItem
        fields = [
            'id', 'mrf', 'site_role_requirement', 'job_role', 'headcount',
            'replacement_for_employee', 'required_skills', 'wage_category',
            'min_wage_snapshot', 'wage_min_requested', 'wage_max_requested',
            'billing_rate_snapshot', 'budget_min', 'budget_max',
        ]
        read_only_fields = fields


class MRFLineItemWriteSerializer(serializers.ModelSerializer):
    """Write serializer for line item create/update."""

    class Meta:
        model = MRFLineItem
        fields = [
            'mrf', 'site_role_requirement', 'job_role', 'headcount',
            'replacement_for_employee', 'required_skills', 'wage_category',
            'wage_min_requested', 'wage_max_requested',
            'billing_rate_snapshot', 'budget_min', 'budget_max',
        ]

    def validate_headcount(self, value):
        if value < 1:
            raise serializers.ValidationError("Headcount must be at least 1.")
        return value

    def validate(self, data):
        instance = self.instance

        # Resolve effective mrf and site_role_requirement (for PATCH partial updates)
        mrf = data.get('mrf') or (instance.mrf if instance else None)
        srr = data.get('site_role_requirement', instance.site_role_requirement if instance else None)

        # site_role_requirement.site must match MRF.site
        if mrf and srr and srr.site_id != mrf.site_id:
            raise serializers.ValidationError({
                'site_role_requirement': (
                    'SiteRoleRequirement must belong to the same site as the MRF.'
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

        return data


# ─── Manpower Request ─────────────────────────────────────────────────────────

class ManpowerRequestSerializer(serializers.ModelSerializer):
    """Read serializer — used for all responses (list, retrieve, create, update)."""
    line_items = MRFLineItemSerializer(many=True, read_only=True)
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
            'submitted_at', 'approved_at', 'rejected_at',
            'line_items', 'created_at', 'updated_at',
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


class ManpowerRequestWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for MRF create/update.

    org and requested_by are injected by the view from the actor context.
    submitted_at / approved_at / rejected_at are managed by workflow transitions (deferred).
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

    class Meta:
        model = ManpowerRequest
        fields = [
            'site', 'requested_by_type', 'mrf_type', 'billing_type',
            'requesting_department', 'required_department', 'department',
            'required_by_date', 'reason', 'client_visible', 'status',
        ]

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

        site = data.get('site') or (instance.site if instance else None)
        requesting_department = data.get(
            'requesting_department',
            instance.requesting_department if instance else None,
        )
        required_department = data.get(
            'required_department',
            instance.required_department if instance else None,
        )

        errors = {}
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

        if errors:
            raise serializers.ValidationError(errors)
        return data

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

"""
apps/onboarding/serializers.py
"""

from rest_framework import serializers

from apps.budgets.services import validate_budget_plan_for_context
from apps.sites.models import Client

from .models import (
    ClientOnboardingRequest,
    ClientOnboardingProposedBudget,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingProposedUser,
)


# ─── Proposed setup — read serializers ───────────────────────────────────────

class ProposedSiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedSite
        fields = [
            'id', 'request', 'name', 'code',
            'address', 'city', 'state', 'pincode',
            'contact_person', 'contact_phone', 'contact_email',
            'location_area', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ProposedDepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedDepartment
        fields = [
            'id', 'request', 'proposed_site', 'scope_level',
            'name', 'code', 'description', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ProposedSRRSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedSiteRoleRequirement
        fields = [
            'id', 'request', 'proposed_site', 'proposed_department',
            'job_role', 'approved_headcount',
            'billing_rate', 'wage_min', 'wage_max', 'shift_hours',
            'billing_type', 'wage_category',
            'effective_from', 'effective_to', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


# ─── Proposed setup — write serializers ──────────────────────────────────────

class ProposedSiteWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedSite
        fields = [
            'name', 'code',
            'address', 'city', 'state', 'pincode',
            'contact_person', 'contact_phone', 'contact_email',
            'location_area', 'is_active',
        ]

    def validate_code(self, value):
        # Uniqueness within active rows for this request is enforced by DB constraint.
        # Check here to give a readable 400 instead of 500.
        request_obj = self.context.get('onboarding_request')
        if request_obj is None:
            return value
        qs = ClientOnboardingProposedSite.objects.filter(
            request=request_obj, code=value, is_active=True,
        )
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A proposed site with this code already exists for this onboarding request."
            )
        return value


class ProposedDepartmentWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedDepartment
        fields = [
            'proposed_site', 'scope_level',
            'name', 'code', 'description', 'is_active',
        ]

    def validate(self, data):
        request_obj = self.context.get('onboarding_request')
        proposed_site = data.get('proposed_site', self.instance.proposed_site if self.instance else None)

        if proposed_site is not None and request_obj is not None:
            if proposed_site.request_id != request_obj.pk:
                raise serializers.ValidationError({
                    'proposed_site': 'Proposed site must belong to the same onboarding request.',
                })

        scope_level = data.get('scope_level', self.instance.scope_level if self.instance else 'site')
        code = data.get('code', self.instance.code if self.instance else None)

        if request_obj and code:
            if scope_level == 'client' or proposed_site is None:
                qs = ClientOnboardingProposedDepartment.objects.filter(
                    request=request_obj, code=code, is_active=True, proposed_site__isnull=True,
                )
            else:
                qs = ClientOnboardingProposedDepartment.objects.filter(
                    request=request_obj, code=code, is_active=True, proposed_site=proposed_site,
                )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {'code': 'A proposed department with this code already exists at the same scope.'}
                )

        return data


class ProposedSRRWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedSiteRoleRequirement
        fields = [
            'proposed_site', 'proposed_department',
            'job_role', 'approved_headcount',
            'billing_rate', 'wage_min', 'wage_max', 'shift_hours',
            'billing_type', 'wage_category',
            'effective_from', 'effective_to', 'is_active',
        ]

    def validate_approved_headcount(self, value):
        if value < 1:
            raise serializers.ValidationError("Approved headcount must be at least 1.")
        return value

    def validate(self, data):
        request_obj = self.context.get('onboarding_request')

        proposed_site = data.get('proposed_site', self.instance.proposed_site if self.instance else None)
        if proposed_site is None:
            raise serializers.ValidationError({'proposed_site': 'Proposed site is required.'})

        if request_obj and proposed_site.request_id != request_obj.pk:
            raise serializers.ValidationError({
                'proposed_site': 'Proposed site must belong to the same onboarding request.',
            })

        proposed_dept = data.get(
            'proposed_department',
            self.instance.proposed_department if self.instance else None,
        )
        if proposed_dept is not None and request_obj:
            if proposed_dept.request_id != request_obj.pk:
                raise serializers.ValidationError({
                    'proposed_department': (
                        'Proposed department must belong to the same onboarding request.'
                    ),
                })

        return data


# ─── Proposed budget — read / write serializers ──────────────────────────────

class ProposedBudgetSerializer(serializers.ModelSerializer):
    proposed_site_name = serializers.CharField(
        source='proposed_site.name', read_only=True, default=None,
    )
    proposed_site_code = serializers.CharField(
        source='proposed_site.code', read_only=True, default=None,
    )
    proposed_department_name = serializers.CharField(
        source='proposed_department.name', read_only=True, default=None,
    )
    proposed_department_code = serializers.CharField(
        source='proposed_department.code', read_only=True, default=None,
    )

    class Meta:
        model = ClientOnboardingProposedBudget
        fields = [
            'id', 'request', 'name', 'code',
            'budget_nature', 'budget_type', 'scope_level',
            'proposed_site', 'proposed_site_name', 'proposed_site_code',
            'proposed_department', 'proposed_department_name', 'proposed_department_code',
            'amount', 'currency', 'period_start', 'period_end',
            'notes', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ProposedBudgetWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedBudget
        fields = [
            'name', 'code', 'budget_nature', 'budget_type', 'scope_level',
            'proposed_site', 'proposed_department',
            'amount', 'currency', 'period_start', 'period_end',
            'notes', 'is_active',
        ]

    def validate_code(self, value):
        request_obj = self.context.get('onboarding_request')
        if request_obj is None:
            return value
        qs = ClientOnboardingProposedBudget.objects.filter(
            request=request_obj, code=value, is_active=True,
        )
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A proposed budget with this code already exists for this onboarding request."
            )
        return value

    def validate(self, data):
        request_obj = self.context.get('onboarding_request')
        instance = self.instance

        scope_level = data.get('scope_level', instance.scope_level if instance else 'client')
        proposed_site = data.get('proposed_site', instance.proposed_site if instance else None)
        proposed_dept = data.get(
            'proposed_department', instance.proposed_department if instance else None,
        )
        amount = data.get('amount', instance.amount if instance else None)
        period_start = data.get('period_start', instance.period_start if instance else None)
        period_end = data.get('period_end', instance.period_end if instance else None)

        errors = {}

        if scope_level == 'site' and proposed_site is None:
            errors['proposed_site'] = 'Site-level budget requires a proposed site.'
        if scope_level == 'department' and proposed_dept is None:
            errors['proposed_department'] = 'Department-level budget requires a proposed department.'

        if proposed_site and request_obj and proposed_site.request_id != request_obj.pk:
            errors['proposed_site'] = 'Proposed site must belong to the same onboarding request.'
        if proposed_dept and request_obj and proposed_dept.request_id != request_obj.pk:
            errors['proposed_department'] = (
                'Proposed department must belong to the same onboarding request.'
            )

        if amount is not None and amount <= 0:
            errors['amount'] = 'Amount must be greater than zero.'

        if period_start and period_end and period_end < period_start:
            errors['period_end'] = 'period_end must be on or after period_start.'

        if errors:
            raise serializers.ValidationError(errors)

        return data


# ─── Proposed user — read / write serializers ────────────────────────────────

class ClientOnboardingProposedUserSerializer(serializers.ModelSerializer):
    access_role_code = serializers.CharField(source='access_role.code', read_only=True)
    access_role_name = serializers.CharField(source='access_role.name', read_only=True)
    proposed_site_name = serializers.CharField(
        source='proposed_site.name', read_only=True, default=None,
    )

    class Meta:
        model = ClientOnboardingProposedUser
        fields = [
            'id', 'request', 'full_name', 'email', 'phone',
            'user_type', 'access_role', 'access_role_code', 'access_role_name',
            'scope_level', 'proposed_site', 'proposed_site_name',
            'is_primary_contact', 'send_invite_on_finalization',
            'is_active', 'created_user', 'invite_status', 'invite_error',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ClientOnboardingProposedUserWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingProposedUser
        fields = [
            'full_name', 'email', 'phone', 'user_type',
            'access_role', 'scope_level', 'proposed_site',
            'is_primary_contact', 'send_invite_on_finalization', 'is_active',
        ]

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, data):
        request_obj = self.context.get('onboarding_request')
        instance = self.instance

        email = data.get('email', instance.email if instance else None)
        access_role = data.get('access_role', instance.access_role if instance else None)
        scope_level = data.get('scope_level', instance.scope_level if instance else 'client')
        proposed_site = data.get('proposed_site', instance.proposed_site if instance else None)
        is_active = data.get('is_active', instance.is_active if instance else True)
        is_primary = data.get('is_primary_contact', instance.is_primary_contact if instance else False)

        errors = {}

        if access_role and request_obj:
            if access_role.org_id != request_obj.org_id:
                errors['access_role'] = 'Access role must belong to the same organization.'
            elif not access_role.is_active:
                errors['access_role'] = 'Access role is inactive.'

        if scope_level == 'site' and proposed_site is None:
            errors['proposed_site'] = 'Site-level user requires a proposed site.'

        if scope_level == 'client' and proposed_site is not None:
            errors['proposed_site'] = 'Client-level user should not have a proposed site.'

        if proposed_site and request_obj and proposed_site.request_id != request_obj.pk:
            errors['proposed_site'] = 'Proposed site must belong to the same onboarding request.'

        if email and request_obj and is_active:
            qs = ClientOnboardingProposedUser.objects.filter(
                request=request_obj, email=email, is_active=True,
            )
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                errors['email'] = (
                    'An active proposed user with this email already exists for this onboarding request.'
                )

        if email and request_obj and not errors.get('email'):
            from apps.accounts.models import User
            if User.objects.filter(email__iexact=email, org=request_obj.org).exists():
                errors['email'] = (
                    f'A user with email "{email}" already exists in this organization.'
                )

        if is_primary and is_active and request_obj:
            qs = ClientOnboardingProposedUser.objects.filter(
                request=request_obj, is_active=True, is_primary_contact=True,
            )
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                errors['is_primary_contact'] = (
                    'An active primary contact already exists for this request.'
                )

        if errors:
            raise serializers.ValidationError(errors)

        return data


# ─── ClientOnboardingRequest — read serializer ────────────────────────────────

class ClientOnboardingRequestSerializer(serializers.ModelSerializer):
    requested_by_username = serializers.CharField(
        source='requested_by.username', read_only=True,
    )
    # client can be null for new_client type — use SerializerMethodField for safety
    client_name = serializers.SerializerMethodField()

    # Budget display fields
    budget_plan_name = serializers.CharField(source='budget_plan.name', read_only=True, default=None)
    budget_plan_code = serializers.CharField(source='budget_plan.code', read_only=True, default=None)
    budget_plan_amount = serializers.DecimalField(
        source='budget_plan.amount', max_digits=14, decimal_places=2, read_only=True, default=None,
    )
    budget_plan_currency = serializers.CharField(source='budget_plan.currency', read_only=True, default=None)
    budget_plan_status = serializers.CharField(source='budget_plan.status', read_only=True, default=None)

    # Nested proposed setup (read-only)
    proposed_sites = ProposedSiteSerializer(many=True, read_only=True)
    proposed_departments = ProposedDepartmentSerializer(many=True, read_only=True)
    proposed_role_requirements = ProposedSRRSerializer(many=True, read_only=True)
    proposed_budgets = ProposedBudgetSerializer(many=True, read_only=True)
    proposed_users = ClientOnboardingProposedUserSerializer(many=True, read_only=True)

    workflow_status = serializers.SerializerMethodField()
    workflow_instance_id = serializers.SerializerMethodField()
    workflow_current_step_id = serializers.SerializerMethodField()
    workflow_current_step_code = serializers.SerializerMethodField()
    workflow_current_step_name = serializers.SerializerMethodField()
    workflow_current_assigned_user = serializers.SerializerMethodField()
    workflow_current_assigned_user_name = serializers.SerializerMethodField()
    workflow_current_department_name = serializers.SerializerMethodField()

    # Readiness (computed, not stored)
    readiness_ok = serializers.SerializerMethodField()
    readiness_errors = serializers.SerializerMethodField()
    readiness_warnings = serializers.SerializerMethodField()

    class Meta:
        model = ClientOnboardingRequest
        fields = [
            'id', 'org', 'client', 'client_name',
            'requested_by', 'requested_by_username',
            'status', 'onboarding_type', 'expected_site_count',
            'summary', 'operations_notes', 'hr_notes', 'finance_notes',
            # proposed client
            'proposed_client_name', 'proposed_client_code',
            'proposed_contact_name', 'proposed_contact_email',
            'proposed_contact_phone', 'proposed_industry',
            'proposed_billing_address', 'proposed_gst_number',
            # budget
            'budget_plan', 'budget_plan_name', 'budget_plan_code',
            'budget_plan_amount', 'budget_plan_currency', 'budget_plan_status',
            # finalization
            'created_client', 'finalization_status', 'finalized_at',
            'finalized_by', 'finalization_error',
            # timestamps
            'submitted_at', 'approved_at', 'rejected_at',
            'created_at', 'updated_at',
            # workflow
            'workflow_status', 'workflow_instance_id',
            'workflow_current_step_id', 'workflow_current_step_code', 'workflow_current_step_name',
            'workflow_current_assigned_user', 'workflow_current_assigned_user_name',
            'workflow_current_department_name',
            # proposed setup
            'proposed_sites', 'proposed_departments', 'proposed_role_requirements',
            'proposed_budgets', 'proposed_users',
            # readiness
            'readiness_ok', 'readiness_errors', 'readiness_warnings',
        ]
        read_only_fields = [
            'id', 'org', 'client', 'client_name',
            'requested_by', 'requested_by_username',
            'status', 'submitted_at', 'approved_at', 'rejected_at',
            'created_client', 'finalization_status', 'finalized_at',
            'finalized_by', 'finalization_error',
            'created_at', 'updated_at',
        ]

    def get_client_name(self, obj):
        return obj.client.name if obj.client_id else None

    def _get_workflow(self, obj):
        if not hasattr(obj, '_cached_wf'):
            all_wf = list(obj.workflow_instances.all())
            active = next((w for w in all_wf if w.status == 'active'), None)
            obj._cached_wf = active or (all_wf[0] if all_wf else None)
        return obj._cached_wf

    def _get_current_step(self, obj):
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

    def _get_readiness(self, obj):
        if not hasattr(obj, '_cached_readiness'):
            from .services import check_onboarding_readiness
            obj._cached_readiness = check_onboarding_readiness(obj)
        return obj._cached_readiness

    def get_readiness_ok(self, obj):
        return self._get_readiness(obj)[0]

    def get_readiness_errors(self, obj):
        return self._get_readiness(obj)[1]

    def get_readiness_warnings(self, obj):
        return self._get_readiness(obj)[2]


# ─── ClientOnboardingRequest — write serializer ───────────────────────────────

class ClientOnboardingRequestWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientOnboardingRequest
        fields = [
            'id', 'org', 'client', 'onboarding_type',
            'expected_site_count', 'summary',
            'operations_notes', 'hr_notes', 'finance_notes',
            'proposed_client_name', 'proposed_client_code',
            'proposed_contact_name', 'proposed_contact_email',
            'proposed_contact_phone', 'proposed_industry',
            'proposed_billing_address', 'proposed_gst_number',
            'budget_plan',
        ]
        read_only_fields = ['id', 'org']
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'budget_plan': {'required': False, 'allow_null': True},
            'proposed_client_name': {'required': False, 'allow_blank': True},
            'proposed_client_code': {'required': False, 'allow_blank': True},
            'proposed_contact_name': {'required': False, 'allow_blank': True},
            'proposed_contact_email': {'required': False, 'allow_blank': True},
            'proposed_contact_phone': {'required': False, 'allow_blank': True},
            'proposed_industry': {'required': False, 'allow_blank': True},
            'proposed_billing_address': {'required': False, 'allow_blank': True},
            'proposed_gst_number': {'required': False, 'allow_blank': True},
        }

    def validate(self, data):
        data = super().validate(data)
        instance = self.instance

        onboarding_type = data.get(
            'onboarding_type',
            instance.onboarding_type if instance else None,
        )
        client = data.get('client', instance.client if instance else None)
        # handle explicit null (clearing) for PATCH
        if 'client' in data and data['client'] is None:
            client = None

        errors = {}

        if onboarding_type == 'new_client':
            if client is not None:
                errors['client'] = (
                    "new_client onboarding must not have an existing client. "
                    "Leave client null and use proposed_client_name/code."
                )
            proposed_name = data.get(
                'proposed_client_name',
                instance.proposed_client_name if instance else '',
            )
            proposed_code = data.get(
                'proposed_client_code',
                instance.proposed_client_code if instance else '',
            )
            if not proposed_name:
                errors['proposed_client_name'] = (
                    "proposed_client_name is required for new_client onboarding."
                )
            if not proposed_code:
                errors['proposed_client_code'] = (
                    "proposed_client_code is required for new_client onboarding."
                )
            else:
                # Collision check against real active Client codes in the org
                actor_org = self.context.get('actor_org')
                if actor_org and Client.objects.filter(
                    org=actor_org, code=proposed_code, is_active=True,
                ).exists():
                    errors['proposed_client_code'] = (
                        f"A client with code '{proposed_code}' already exists in this organization."
                    )

        elif onboarding_type == 'new_site_expansion':
            if client is None:
                errors['client'] = "new_site_expansion onboarding requires an existing client."

        if errors:
            raise serializers.ValidationError(errors)

        # Budget plan validation (skip if no client for new_client type)
        if 'budget_plan' in data and data['budget_plan'] is None:
            return data

        budget_plan = data.get('budget_plan') or (
            instance.budget_plan if instance and instance.budget_plan_id else None
        )
        if budget_plan is not None and client is not None:
            bp_errors = validate_budget_plan_for_context(
                budget_plan,
                org=client.org,
                require_nature='billable',
                client=client,
            )
            if bp_errors:
                raise serializers.ValidationError(bp_errors)

        return data

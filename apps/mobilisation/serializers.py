"""
apps/mobilisation/serializers.py
"""

from rest_framework import serializers

from apps.sites.models import Client

from .models import (
    MobilisationSetupRequest,
    MobilisationProposedDepartment,
    MobilisationProposedDepartmentRole,
    MobilisationProposedUser,
)
from .role_validation import validate_client_user_access_role


# ─── Proposed department — read / write serializers ──────────────────────────

class ProposedDepartmentSerializer(serializers.ModelSerializer):
    real_site_name = serializers.CharField(source='real_site.name', read_only=True, default=None)
    real_site_code = serializers.CharField(source='real_site.code', read_only=True, default=None)

    class Meta:
        model = MobilisationProposedDepartment
        fields = [
            'id', 'request', 'real_site', 'real_site_name', 'real_site_code',
            'scope_level', 'name', 'code', 'description', 'is_active',
            'is_locked', 'source_key', 'sort_order',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ProposedDepartmentWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobilisationProposedDepartment
        fields = [
            'real_site', 'scope_level',
            'name', 'code', 'description', 'is_active',
            'sort_order',
        ]

    def validate(self, data):
        request_obj = self.context.get('mobilisation_request')
        instance = self.instance

        real_site = data.get('real_site', instance.real_site if instance else None)
        scope_level = data.get('scope_level', instance.scope_level if instance else 'site')
        code = data.get('code', instance.code if instance else None)

        errors = {}

        if scope_level == 'site':
            if real_site is None:
                errors['real_site'] = 'Site-level department requires real_site.'
            elif request_obj and (
                real_site.org_id != request_obj.org_id
                or (request_obj.client_id and real_site.client_id != request_obj.client_id)
            ):
                errors['real_site'] = 'real_site must belong to the mobilisation client.'
        elif scope_level == 'client' and real_site is not None:
            errors['real_site'] = 'Client-level department must not have a real_site.'

        if errors:
            raise serializers.ValidationError(errors)

        if request_obj and code:
            if real_site is not None:
                qs = MobilisationProposedDepartment.objects.filter(
                    request=request_obj, code=code, is_active=True, real_site=real_site,
                )
            else:
                qs = MobilisationProposedDepartment.objects.filter(
                    request=request_obj, code=code, is_active=True,
                    real_site__isnull=True,
                )
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {'code': 'A proposed department with this code already exists at the same scope.'}
                )

        return data


# ─── Proposed user — read / write serializers ────────────────────────────────

class ProposedDepartmentRoleSerializer(serializers.ModelSerializer):
    proposed_department_name = serializers.CharField(source='proposed_department.name', read_only=True)
    real_site_name = serializers.CharField(source='real_site.name', read_only=True, default=None)
    job_role_name = serializers.CharField(source='job_role.name', read_only=True, default=None)
    job_role_code = serializers.CharField(source='job_role.code', read_only=True, default=None)
    service_category = serializers.CharField(source='site_role_requirement.service_category', read_only=True, default='')
    wage_category_name = serializers.CharField(source='site_role_requirement.wage_category.name', read_only=True, default=None)
    shift_hours = serializers.DecimalField(
        source='site_role_requirement.shift_hours',
        max_digits=4,
        decimal_places=1,
        read_only=True,
        default=None,
    )

    class Meta:
        model = MobilisationProposedDepartmentRole
        fields = [
            'id', 'request', 'proposed_department', 'proposed_department_name',
            'site_role_requirement', 'real_site', 'real_site_name',
            'job_role', 'job_role_name', 'job_role_code',
            'service_category', 'wage_category_name', 'shift_hours',
            'approved_headcount_snapshot', 'sort_order', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ProposedDepartmentRoleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobilisationProposedDepartmentRole
        fields = [
            'proposed_department', 'site_role_requirement', 'sort_order', 'is_active',
        ]

    def validate(self, data):
        request_obj = self.context.get('mobilisation_request')
        instance = self.instance
        proposed_department = data.get(
            'proposed_department',
            instance.proposed_department if instance else None,
        )
        srr = data.get(
            'site_role_requirement',
            instance.site_role_requirement if instance else None,
        )
        is_active = data.get('is_active', instance.is_active if instance else True)
        errors = {}

        if request_obj and proposed_department:
            if proposed_department.request_id != request_obj.id:
                errors['proposed_department'] = 'Department must belong to this mobilisation request.'
            elif proposed_department.scope_level != 'site':
                errors['proposed_department'] = 'Role mappings can only be assigned to site-level departments.'

        if request_obj and srr:
            if request_obj.client_id and srr.site.client_id != request_obj.client_id:
                errors['site_role_requirement'] = 'Role requirement must belong to this mobilisation client.'
            elif proposed_department and proposed_department.real_site_id != srr.site_id:
                errors['proposed_department'] = (
                    'Department site must match the role requirement site.'
                )

        if request_obj and srr and is_active:
            qs = MobilisationProposedDepartmentRole.objects.filter(
                request=request_obj,
                site_role_requirement=srr,
                is_active=True,
            )
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                errors['site_role_requirement'] = (
                    'This role requirement is already assigned to an active proposed department.'
                )

        if errors:
            raise serializers.ValidationError(errors)
        return data


class MobilisationProposedUserSerializer(serializers.ModelSerializer):
    access_role_code = serializers.CharField(source='access_role.code', read_only=True)
    access_role_name = serializers.CharField(source='access_role.name', read_only=True)
    real_site_name = serializers.CharField(source='real_site.name', read_only=True, default=None)
    real_site_code = serializers.CharField(source='real_site.code', read_only=True, default=None)

    class Meta:
        model = MobilisationProposedUser
        fields = [
            'id', 'request', 'full_name', 'email', 'phone',
            'user_type', 'access_role', 'access_role_code', 'access_role_name',
            'scope_level',
            'real_site', 'real_site_name', 'real_site_code',
            'is_primary_contact', 'send_invite_on_finalization',
            'is_active', 'created_user', 'invite_status', 'invite_error',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class MobilisationProposedUserWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobilisationProposedUser
        fields = [
            'full_name', 'email', 'phone', 'user_type',
            'access_role', 'scope_level', 'real_site',
            'is_primary_contact', 'send_invite_on_finalization', 'is_active',
        ]

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, data):
        request_obj = self.context.get('mobilisation_request')
        instance = self.instance

        email = data.get('email', instance.email if instance else None)
        access_role = data.get('access_role', instance.access_role if instance else None)
        scope_level = data.get('scope_level', instance.scope_level if instance else 'client')
        real_site = data.get('real_site', instance.real_site if instance else None)
        is_active = data.get('is_active', instance.is_active if instance else True)
        is_primary = data.get('is_primary_contact', instance.is_primary_contact if instance else False)

        errors = {}

        if access_role and request_obj:
            if access_role.org_id != request_obj.org_id:
                errors['access_role'] = 'Access role must belong to the same organization.'
            elif not access_role.is_active:
                errors['access_role'] = 'Access role is inactive.'
            else:
                try:
                    validate_client_user_access_role(access_role, scope_level)
                except ValueError as exc:
                    errors['access_role'] = str(exc)

        if scope_level == 'site':
            if real_site is None:
                errors['real_site'] = 'Site-level user requires real_site.'
            elif request_obj and (
                real_site.org_id != request_obj.org_id
                or (request_obj.client_id and real_site.client_id != request_obj.client_id)
            ):
                errors['real_site'] = 'real_site must belong to the mobilisation client.'
        elif scope_level == 'client' and real_site is not None:
            errors['real_site'] = 'Client-level user must not have a real_site.'

        if errors:
            raise serializers.ValidationError(errors)

        if email and request_obj and is_active:
            qs = MobilisationProposedUser.objects.filter(
                request=request_obj, email=email, is_active=True,
            )
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                errors['email'] = (
                    'An active proposed user with this email already exists for this request.'
                )

        if email and request_obj and not errors.get('email'):
            from apps.accounts.models import User
            if User.objects.filter(email__iexact=email, org=request_obj.org).exists():
                errors['email'] = (
                    f'A user with email "{email}" already exists in this organization.'
                )

        if is_primary and is_active and request_obj:
            qs = MobilisationProposedUser.objects.filter(
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


# ─── MobilisationSetupRequest — read serializer ───────────────────────────────

class MobilisationSetupRequestSerializer(serializers.ModelSerializer):
    requested_by_username = serializers.CharField(
        source='requested_by.username', read_only=True,
    )
    assigned_operations_owner_username = serializers.CharField(
        source='assigned_operations_owner.username', read_only=True, default=None,
    )
    setup_completed_by_username = serializers.CharField(
        source='setup_completed_by.username', read_only=True, default=None,
    )
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
    proposed_departments = ProposedDepartmentSerializer(many=True, read_only=True)
    proposed_users = MobilisationProposedUserSerializer(many=True, read_only=True)

    workflow_status = serializers.SerializerMethodField()
    workflow_instance_id = serializers.SerializerMethodField()
    workflow_current_step_id = serializers.SerializerMethodField()
    workflow_current_step_code = serializers.SerializerMethodField()
    workflow_current_step_name = serializers.SerializerMethodField()
    workflow_current_assigned_user = serializers.SerializerMethodField()
    workflow_current_assigned_user_name = serializers.SerializerMethodField()
    workflow_current_department_name = serializers.SerializerMethodField()

    # Sales source fields
    source_sales_lead_name = serializers.SerializerMethodField()
    source_proposal_version_number = serializers.SerializerMethodField()

    # Readiness (computed, not stored)
    readiness_ok = serializers.SerializerMethodField()
    readiness_errors = serializers.SerializerMethodField()
    readiness_warnings = serializers.SerializerMethodField()

    class Meta:
        model = MobilisationSetupRequest
        fields = [
            'id', 'org', 'client', 'client_name',
            'requested_by', 'requested_by_username',
            'assigned_operations_owner', 'assigned_operations_owner_username',
            'status', 'mobilisation_type',
            'summary', 'operations_notes', 'hr_notes', 'finance_notes',
            # budget
            'budget_plan', 'budget_plan_name', 'budget_plan_code',
            'budget_plan_amount', 'budget_plan_currency', 'budget_plan_status',
            # finalization
            'finalization_status', 'finalized_at',
            'finalized_by', 'finalization_error',
            # sales source
            'source_sales_lead', 'source_sales_lead_name',
            'source_proposal_version', 'source_proposal_version_number',
            # mobilisation
            'mobilisation_requires_approval', 'setup_strategy',
            # timestamps
            'submitted_at', 'approved_at', 'rejected_at',
            'submitted_to_operations_at', 'setup_completed_at',
            'setup_completed_by', 'setup_completed_by_username',
            'created_at', 'updated_at',
            # workflow
            'workflow_status', 'workflow_instance_id',
            'workflow_current_step_id', 'workflow_current_step_code', 'workflow_current_step_name',
            'workflow_current_assigned_user', 'workflow_current_assigned_user_name',
            'workflow_current_department_name',
            # proposed setup
            'proposed_departments', 'proposed_users',
            # readiness
            'readiness_ok', 'readiness_errors', 'readiness_warnings',
        ]
        read_only_fields = [
            'id', 'org', 'client', 'client_name',
            'requested_by', 'requested_by_username',
            'assigned_operations_owner', 'assigned_operations_owner_username',
            'status', 'submitted_at', 'approved_at', 'rejected_at',
            'submitted_to_operations_at', 'setup_completed_at',
            'setup_completed_by', 'setup_completed_by_username',
            'finalization_status', 'finalized_at',
            'finalized_by', 'finalization_error',
            'source_sales_lead', 'source_proposal_version',
            'setup_strategy',
            'created_at', 'updated_at',
        ]

    def get_client_name(self, obj):
        return obj.client.name if obj.client_id else None

    def get_source_sales_lead_name(self, obj):
        return obj.source_sales_lead.client_name if obj.source_sales_lead_id else None

    def get_source_proposal_version_number(self, obj):
        return obj.source_proposal_version.version_number if obj.source_proposal_version_id else None

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
            from .services import check_mobilisation_readiness
            obj._cached_readiness = check_mobilisation_readiness(obj)
        return obj._cached_readiness

    def get_readiness_ok(self, obj):
        return self._get_readiness(obj)[0]

    def get_readiness_errors(self, obj):
        return self._get_readiness(obj)[1]

    def get_readiness_warnings(self, obj):
        return self._get_readiness(obj)[2]


# ─── MobilisationSetupRequest — write serializer ─────────────────────────────

class MobilisationSetupRequestWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobilisationSetupRequest
        fields = [
            'id', 'org', 'client', 'mobilisation_type',
            'summary', 'operations_notes', 'hr_notes', 'finance_notes',
            'budget_plan', 'assigned_operations_owner', 'setup_strategy',
        ]
        read_only_fields = ['id', 'org']
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'budget_plan': {'required': False, 'allow_null': True},
            'assigned_operations_owner': {'required': False, 'allow_null': True},
        }

    def validate(self, data):
        data = super().validate(data)
        instance = self.instance

        client = data.get('client', instance.client if instance else None)
        if 'client' in data and data['client'] is None:
            client = None

        if 'budget_plan' in data and data['budget_plan'] is None:
            return data

        budget_plan = data.get('budget_plan') or (
            instance.budget_plan if instance and instance.budget_plan_id else None
        )
        if budget_plan is not None and client is not None:
            from apps.budgets.services import validate_budget_plan_for_context
            bp_errors = validate_budget_plan_for_context(
                budget_plan,
                org=client.org,
                require_nature='billable',
                client=client,
            )
            if bp_errors:
                raise serializers.ValidationError(bp_errors)

        owner = data.get('assigned_operations_owner')
        if owner is not None:
            request_org = client.org if client is not None else self.context.get('actor_org')
            if request_org is not None and owner.org_id != request_org.id:
                raise serializers.ValidationError(
                    {'assigned_operations_owner': 'Operations owner must belong to the same organization.'}
                )
            if not owner.is_active:
                raise serializers.ValidationError(
                    {'assigned_operations_owner': 'Operations owner is inactive.'}
                )
            if owner.user_type != 'internal':
                raise serializers.ValidationError(
                    {'assigned_operations_owner': 'Operations owner must be an internal user.'}
                )

        return data

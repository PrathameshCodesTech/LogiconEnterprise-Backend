"""
apps/workflow/config_serializers.py

Serializers for the Workflow Config (approval-setup) APIs:
  ApprovalFlow     → WorkflowTemplate
  ApprovalStep     → WorkflowStepTemplate
  FlowRule         → WorkflowTemplateMapping
  AssignmentConfig → StepAssignmentConfig
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    WorkflowTemplate,
    WorkflowStepTemplate,
    WorkflowTemplateMapping,
    StepAssignmentConfig,
    TRIGGER_TYPE_CHOICES,
)


# ─── Approval Flows (WorkflowTemplate) ───────────────────────────────────────

class ApprovalFlowSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowTemplate
        fields = [
            'id', 'org', 'name', 'code', 'trigger_type', 'version',
            'description', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ApprovalFlowWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowTemplate
        fields = ['name', 'code', 'trigger_type', 'version', 'description', 'is_active']

    def validate(self, data):
        org = self.context.get('org')
        code = data.get('code', getattr(self.instance, 'code', None))
        trigger_type = data.get('trigger_type', getattr(self.instance, 'trigger_type', None))

        valid_types = [c[0] for c in TRIGGER_TYPE_CHOICES]
        if trigger_type and trigger_type not in valid_types:
            raise serializers.ValidationError(
                {'trigger_type': f'Must be one of: {valid_types}.'}
            )

        if org is not None and code:
            qs = WorkflowTemplate.objects.filter(org=org, code=code)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {'code': 'A workflow with this code already exists in your organization.'}
                )
        return data


# ─── Approval Steps (WorkflowStepTemplate) ───────────────────────────────────

class ApprovalStepSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name', read_only=True)
    template_code = serializers.CharField(source='template.code', read_only=True)

    class Meta:
        model = WorkflowStepTemplate
        fields = [
            'id', 'template', 'template_name', 'template_code',
            'order', 'code', 'name',
            'assignment_mode', 'actor_type',
            'on_approve_next', 'on_reject_target', 'on_request_changes_target',
            'requires_comment_on_reject', 'requires_comment_on_request_changes',
            'sla_hours',
        ]
        read_only_fields = ['id', 'template_name', 'template_code']


class ApprovalStepWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStepTemplate
        fields = [
            'template', 'order', 'code', 'name',
            'assignment_mode', 'actor_type',
            'on_approve_next', 'on_reject_target', 'on_request_changes_target',
            'requires_comment_on_reject', 'requires_comment_on_request_changes',
            'sla_hours',
        ]
        validators = []  # custom validate() gives field-level errors for order/code uniqueness

    def validate(self, data):
        # Merge partial-update data with existing instance values
        if self.instance:
            template = data.get('template', self.instance.template)
            order = data.get('order', self.instance.order)
            code = data.get('code', self.instance.code)
            on_approve_next = data.get('on_approve_next', self.instance.on_approve_next)
            on_reject_target = data.get('on_reject_target', self.instance.on_reject_target)
            on_request_changes_target = data.get(
                'on_request_changes_target', self.instance.on_request_changes_target,
            )
        else:
            template = data.get('template')
            order = data.get('order')
            code = data.get('code', '')
            on_approve_next = data.get('on_approve_next', '')
            on_reject_target = data.get('on_reject_target', '')
            on_request_changes_target = data.get('on_request_changes_target', '')

        errors = {}

        if template and template.pk:
            exclude_pk = self.instance.pk if self.instance else None

            if order is not None:
                qs = WorkflowStepTemplate.objects.filter(template=template, order=order)
                if exclude_pk:
                    qs = qs.exclude(pk=exclude_pk)
                if qs.exists():
                    errors['order'] = (
                        f'A step with order {order} already exists in this template.'
                    )

            if code:
                qs2 = WorkflowStepTemplate.objects.filter(template=template, code=code)
                if exclude_pk:
                    qs2 = qs2.exclude(pk=exclude_pk)
                if qs2.exists():
                    errors['code'] = (
                        f'A step with code "{code}" already exists in this template.'
                    )

            sibling_codes = set(
                WorkflowStepTemplate.objects
                .filter(template=template)
                .exclude(pk=exclude_pk)
                .values_list('code', flat=True)
            )
            for field_name, value in [
                ('on_approve_next', on_approve_next),
                ('on_reject_target', on_reject_target),
                ('on_request_changes_target', on_request_changes_target),
            ]:
                if value and value != 'END':
                    if value not in sibling_codes:
                        errors[field_name] = (
                            f'Step code "{value}" does not exist in this template. '
                            f'Leave blank for next sequential, use "END" to complete, '
                            f'or choose from: {sorted(sibling_codes) or "(none yet)"}.'
                        )

        if errors:
            raise serializers.ValidationError(errors)
        return data


# ─── Flow Rules (WorkflowTemplateMapping) ────────────────────────────────────

class FlowRuleSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name', read_only=True)
    template_code = serializers.CharField(source='template.code', read_only=True)
    client_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    mapping_level = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowTemplateMapping
        fields = [
            'id', 'org', 'trigger_type', 'template', 'template_name', 'template_code',
            'client', 'client_name', 'site', 'site_name',
            'is_active', 'mapping_level', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'org', 'template_name', 'template_code',
                            'client_name', 'site_name', 'mapping_level',
                            'created_at', 'updated_at']

    def get_client_name(self, obj):
        return obj.client.name if obj.client else None

    def get_site_name(self, obj):
        return obj.site.name if obj.site else None

    def get_mapping_level(self, obj):
        if obj.site_id:
            return 'site'
        if obj.client_id:
            return 'client'
        return 'company'


class FlowRuleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowTemplateMapping
        fields = ['trigger_type', 'template', 'client', 'site', 'is_active']

    def validate(self, data):
        org = self.context.get('org')

        trigger_type = data.get('trigger_type', getattr(self.instance, 'trigger_type', None))
        template = data.get('template', getattr(self.instance, 'template', None))
        client = data.get('client', getattr(self.instance, 'client', None))
        site = data.get('site', getattr(self.instance, 'site', None))
        is_active = data.get('is_active', getattr(self.instance, 'is_active', True))

        errors = {}

        # Template trigger_type must match rule trigger_type
        if template and trigger_type and template.trigger_type != trigger_type:
            errors['template'] = (
                f'Template trigger type "{template.trigger_type}" does not match '
                f'rule trigger type "{trigger_type}".'
            )

        if org:
            # Client must be in the same org
            if client and client.org_id != org.pk:
                errors['client'] = 'Client must belong to your organization.'

            # Site must be in the same org
            if site and site.org_id != org.pk:
                errors['site'] = 'Site must belong to your organization.'

        # If both client and site are set, site.client must match client
        if client and site and site.client_id != client.pk:
            errors['site'] = 'Site must belong to the selected client.'

        # Uniqueness of active rule per org + trigger_type + level
        if is_active and org and trigger_type and not errors:
            exclude_pk = self.instance.pk if self.instance else None
            if site:
                qs = WorkflowTemplateMapping.objects.filter(
                    org=org, trigger_type=trigger_type, site=site, is_active=True,
                )
            elif client:
                qs = WorkflowTemplateMapping.objects.filter(
                    org=org, trigger_type=trigger_type, client=client,
                    site__isnull=True, is_active=True,
                )
            else:
                qs = WorkflowTemplateMapping.objects.filter(
                    org=org, trigger_type=trigger_type,
                    client__isnull=True, site__isnull=True, is_active=True,
                )
            if exclude_pk:
                qs = qs.exclude(pk=exclude_pk)
            if qs.exists():
                level = 'site' if site else ('client' if client else 'company')
                errors['non_field_errors'] = (
                    f'An active {level}-level rule already exists for '
                    f'trigger_type="{trigger_type}" in this organization.'
                )

        if errors:
            raise serializers.ValidationError(errors)
        return data


# ─── Assignment Configs (StepAssignmentConfig) ───────────────────────────────

class AssignmentConfigSerializer(serializers.ModelSerializer):
    client_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    department_name = serializers.SerializerMethodField()
    named_user_name = serializers.SerializerMethodField()
    step_name = serializers.SerializerMethodField()
    assignment_level = serializers.SerializerMethodField()

    class Meta:
        model = StepAssignmentConfig
        fields = [
            'id', 'org', 'trigger_type', 'step_code', 'step_name',
            'client', 'client_name', 'site', 'site_name',
            'department', 'department_name',
            'assignment_mode', 'named_user', 'named_user_name',
            'eligible_role', 'eligible_scope',
            'effective_from', 'effective_to',
            'note', 'is_active', 'assignment_level',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'org', 'client_name', 'site_name', 'department_name',
            'named_user_name', 'step_name', 'assignment_level',
            'created_at', 'updated_at',
        ]

    def get_client_name(self, obj):
        return obj.client.name if obj.client else None

    def get_site_name(self, obj):
        return obj.site.name if obj.site else None

    def get_department_name(self, obj):
        return obj.department.name if obj.department else None

    def get_named_user_name(self, obj):
        if obj.named_user:
            full = obj.named_user.get_full_name()
            return full or obj.named_user.username
        return None

    def get_step_name(self, obj):
        step = WorkflowStepTemplate.objects.filter(
            template__org=obj.org,
            template__trigger_type=obj.trigger_type,
            template__is_active=True,
            code=obj.step_code,
        ).values('name').first()
        return step['name'] if step else obj.step_code

    def get_assignment_level(self, obj):
        if obj.site_id:
            return 'site'
        if obj.client_id:
            return 'client'
        return 'company'


class AssignmentConfigWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = StepAssignmentConfig
        fields = [
            'trigger_type', 'step_code',
            'client', 'site', 'department',
            'assignment_mode', 'named_user', 'eligible_role', 'eligible_scope',
            'effective_from', 'effective_to', 'note', 'is_active',
        ]

    def validate(self, data):
        org = self.context.get('org')

        # Merge with existing instance for partial updates
        if self.instance:
            trigger_type = data.get('trigger_type', self.instance.trigger_type)
            step_code = data.get('step_code', self.instance.step_code)
            client = data.get('client', self.instance.client)
            site = data.get('site', self.instance.site)
            department = data.get('department', self.instance.department)
            assignment_mode = data.get('assignment_mode', self.instance.assignment_mode)
            named_user = data.get('named_user', self.instance.named_user)
            effective_from = data.get('effective_from', self.instance.effective_from)
            effective_to = data.get('effective_to', self.instance.effective_to)
            is_active = data.get('is_active', self.instance.is_active)
        else:
            trigger_type = data.get('trigger_type')
            step_code = data.get('step_code')
            client = data.get('client')
            site = data.get('site')
            department = data.get('department')
            assignment_mode = data.get('assignment_mode', 'named_user')
            named_user = data.get('named_user')
            effective_from = data.get('effective_from')
            effective_to = data.get('effective_to')
            is_active = data.get('is_active', True)

        errors = {}

        # Only named_user is supported for active Phase-1 configs
        if assignment_mode != 'named_user' and is_active:
            errors['assignment_mode'] = (
                'Only "named_user" assignment mode is supported for active configs.'
            )

        # Named user validations
        if assignment_mode == 'named_user':
            if not named_user:
                errors['named_user'] = 'A named user is required for named_user assignment mode.'
            elif not named_user.is_active:
                errors['named_user'] = (
                    f'User "{named_user.username}" is inactive and cannot be assigned.'
                )
            elif org and named_user.org_id != org.pk:
                errors['named_user'] = 'User must belong to your organization.'

        # Effective date range
        if effective_from and effective_to and effective_to < effective_from:
            errors['effective_to'] = 'Effective end date must be on or after the start date.'

        # Org-level field checks
        if org:
            if client and client.org_id != org.pk:
                errors['client'] = 'Client must belong to your organization.'
            if site and site.org_id != org.pk:
                errors['site'] = 'Site must belong to your organization.'
            if department and department.org_id != org.pk:
                errors['department'] = 'Department must belong to your organization.'

        # Site–client relationship
        if site and client and site.client_id != client.pk:
            errors['site'] = 'Site must belong to the selected client.'

        # Run model-level validation (dept scope + user-dept matching)
        if not errors and org:
            inst = StepAssignmentConfig(
                org=org,
                trigger_type=trigger_type or '',
                step_code=step_code or '',
                client=client,
                site=site,
                department=department,
                assignment_mode=assignment_mode or 'named_user',
                named_user=named_user,
            )
            if self.instance:
                inst.pk = self.instance.pk
            try:
                inst.clean()
            except DjangoValidationError as exc:
                msg = exc.message_dict if hasattr(exc, 'message_dict') else {'non_field_errors': str(exc)}
                raise serializers.ValidationError(msg)

        # Active uniqueness check
        if is_active and org and trigger_type and step_code and not errors:
            exclude_pk = self.instance.pk if self.instance else None
            if site:
                qs = StepAssignmentConfig.objects.filter(
                    org=org, trigger_type=trigger_type, step_code=step_code,
                    site=site, is_active=True,
                )
            elif client:
                qs = StepAssignmentConfig.objects.filter(
                    org=org, trigger_type=trigger_type, step_code=step_code,
                    client=client, site__isnull=True, is_active=True,
                )
            else:
                qs = StepAssignmentConfig.objects.filter(
                    org=org, trigger_type=trigger_type, step_code=step_code,
                    client__isnull=True, site__isnull=True, is_active=True,
                )
            if exclude_pk:
                qs = qs.exclude(pk=exclude_pk)
            if qs.exists():
                level = 'site' if site else ('client' if client else 'company')
                errors['non_field_errors'] = (
                    f'An active {level}-level assignment already exists for '
                    f'step_code="{step_code}" (trigger_type="{trigger_type}") '
                    f'in this organization. Deactivate the existing one first.'
                )

        if errors:
            raise serializers.ValidationError(errors)
        return data

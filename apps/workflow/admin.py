from django.contrib import admin

from .models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
    ApprovalRoute, ApprovalRouteStepAssignment,
    WorkflowInstance, WorkflowStepInstance, WorkflowAction,
)


class WorkflowStepTemplateInline(admin.TabularInline):
    model = WorkflowStepTemplate
    extra = 0
    fields = [
        'order', 'code', 'name', 'assignment_mode', 'actor_type',
        'on_approve_next', 'on_reject_target', 'on_request_changes_target',
        'requires_comment_on_reject', 'requires_comment_on_request_changes', 'sla_hours',
    ]


@admin.register(WorkflowTemplate)
class WorkflowTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'org', 'trigger_type', 'version', 'is_active', 'created_at']
    search_fields = ['name', 'code']
    list_filter = ['trigger_type', 'is_active', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['org']
    inlines = [WorkflowStepTemplateInline]


@admin.register(WorkflowStepTemplate)
class WorkflowStepTemplateAdmin(admin.ModelAdmin):
    list_display = ['template', 'order', 'code', 'name', 'assignment_mode', 'actor_type', 'sla_hours']
    search_fields = ['code', 'name', 'template__name']
    list_filter = ['assignment_mode', 'actor_type']
    raw_id_fields = ['template']


@admin.register(WorkflowTemplateMapping)
class WorkflowTemplateMappingAdmin(admin.ModelAdmin):
    list_display = ['org', 'trigger_type', 'template', 'client', 'site', 'is_active', 'created_at']
    search_fields = ['template__name', 'template__code']
    list_filter = ['trigger_type', 'is_active', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['org', 'template', 'client', 'site']


@admin.register(StepAssignmentConfig)
class StepAssignmentConfigAdmin(admin.ModelAdmin):
    list_display = [
        'org', 'trigger_type', 'step_code', 'department', 'client', 'site',
        'assignment_mode', 'named_user', 'is_active', 'effective_from', 'effective_to',
    ]
    search_fields = ['step_code', 'named_user__username', 'department__name', 'department__code']
    list_filter = ['trigger_type', 'assignment_mode', 'is_active', 'org', 'department', 'client', 'site']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['org', 'client', 'site', 'named_user', 'eligible_role', 'eligible_scope', 'department']


class ApprovalRouteStepAssignmentInline(admin.TabularInline):
    model = ApprovalRouteStepAssignment
    extra = 0
    fields = ['step_code', 'assignment_mode', 'named_user', 'department', 'note', 'is_active']
    raw_id_fields = ['named_user', 'department']


@admin.register(ApprovalRoute)
class ApprovalRouteAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'code', 'org', 'trigger_type', 'client', 'site',
        'is_default', 'is_active', 'created_at',
    ]
    search_fields = ['name', 'code']
    list_filter = ['trigger_type', 'is_default', 'is_active', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['org', 'template', 'client', 'site']
    inlines = [ApprovalRouteStepAssignmentInline]


@admin.register(ApprovalRouteStepAssignment)
class ApprovalRouteStepAssignmentAdmin(admin.ModelAdmin):
    list_display = [
        'route', 'step_code', 'assignment_mode', 'named_user', 'department', 'is_active',
    ]
    search_fields = ['step_code', 'named_user__username', 'route__name', 'route__code']
    list_filter = ['assignment_mode', 'is_active', 'route__trigger_type']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['route', 'named_user', 'department']


class WorkflowStepInstanceInline(admin.TabularInline):
    model = WorkflowStepInstance
    extra = 0
    fields = [
        'step_order', 'step_code', 'step_name', 'status',
        'assigned_user', 'assigned_department_name_snapshot', 'acted_by', 'acted_at', 'action_taken',
    ]
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class WorkflowActionInline(admin.TabularInline):
    model = WorkflowAction
    extra = 0
    fields = ['step_instance', 'actor', 'action', 'comment', 'reassign_from', 'reassign_to', 'created_at']
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WorkflowInstance)
class WorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = ['id', 'mrf', 'template', 'status', 'initiated_by', 'created_at', 'completed_at']
    search_fields = ['mrf__id', 'template__name']
    list_filter = ['status', 'org', 'template']
    readonly_fields = ['created_at', 'updated_at', 'completed_at']
    raw_id_fields = ['org', 'mrf', 'template', 'initiated_by']
    inlines = [WorkflowStepInstanceInline, WorkflowActionInline]


@admin.register(WorkflowStepInstance)
class WorkflowStepInstanceAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'workflow', 'step_order', 'step_code', 'step_name',
        'assigned_user', 'status', 'acted_by', 'acted_at',
    ]
    search_fields = ['step_code', 'step_name', 'workflow__id']
    list_filter = ['status', 'assignment_mode']
    raw_id_fields = ['workflow', 'step_template', 'assigned_user', 'acted_by']
    readonly_fields = ['activated_at', 'due_at']


@admin.register(WorkflowAction)
class WorkflowActionAdmin(admin.ModelAdmin):
    list_display = ['id', 'workflow', 'step_instance', 'actor', 'action', 'created_at']
    search_fields = ['actor__username', 'comment']
    list_filter = ['action']
    readonly_fields = ['workflow', 'step_instance', 'actor', 'action', 'comment',
                       'reassign_from', 'reassign_to', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin
from .models import WorkflowTemplate, WorkflowStepTemplate, WorkflowInstance, WorkflowAction


class WorkflowStepTemplateInline(admin.TabularInline):
    model = WorkflowStepTemplate
    extra = 0
    fields = [
        'step_key', 'step_order', 'name', 'approver_role',
        'is_client_step', 'can_request_changes', 'sla_hours',
    ]
    raw_id_fields = ['approver_role', 'approver_scope_node',
                     'on_approve_next_step', 'on_reject_target_step']


@admin.register(WorkflowTemplate)
class WorkflowTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'org', 'module', 'is_active', 'created_at']
    search_fields = ['name', 'code', 'module']
    list_filter = ['module', 'is_active', 'org']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['org', 'scope_node']
    inlines = [WorkflowStepTemplateInline]


@admin.register(WorkflowStepTemplate)
class WorkflowStepTemplateAdmin(admin.ModelAdmin):
    list_display = [
        'template', 'step_order', 'step_key', 'name',
        'approver_role', 'is_client_step', 'can_request_changes', 'sla_hours',
    ]
    search_fields = ['name', 'step_key', 'template__name']
    list_filter = ['is_client_step', 'can_request_changes']
    raw_id_fields = [
        'template', 'approver_role', 'approver_scope_node',
        'on_approve_next_step', 'on_reject_target_step',
    ]


@admin.register(WorkflowInstance)
class WorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'template', 'object_type', 'object_id',
        'current_step', 'status', 'started_by', 'created_at',
    ]
    search_fields = ['object_type', 'object_id', 'template__name']
    list_filter = ['status', 'template']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['template', 'current_step', 'started_by']


@admin.register(WorkflowAction)
class WorkflowActionAdmin(admin.ModelAdmin):
    list_display = ['instance', 'step', 'step_snapshot_name', 'action', 'action_by', 'created_at']
    search_fields = ['step_snapshot_name', 'note', 'action_by__username']
    list_filter = ['action']
    readonly_fields = ['created_at']
    raw_id_fields = ['instance', 'step', 'action_by']

from django.contrib import admin
from .models import DeploymentHistory, Employee, SiteDeployment


class SiteDeploymentInline(admin.TabularInline):
    model = SiteDeployment
    extra = 0
    fields = ['site', 'job_role', 'status', 'start_date', 'end_date', 'billing_type']
    raw_id_fields = ['site', 'job_role', 'mrf_line_item', 'hiring_application']


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = [
        'employee_code', 'full_name', 'org', 'job_role',
        'phone', 'status', 'joined_on', 'exited_on',
    ]
    search_fields = [
        'employee_code', 'first_name', 'last_name',
        'phone', 'phone_normalized', 'email',
    ]
    list_filter = ['status', 'org', 'job_role']
    readonly_fields = ['phone_normalized', 'created_at', 'updated_at']
    raw_id_fields = ['org', 'candidate', 'user', 'job_role']
    inlines = [SiteDeploymentInline]


@admin.register(SiteDeployment)
class SiteDeploymentAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'employee', 'site', 'job_role', 'status',
        'billing_type', 'start_date', 'end_date', 'created_at',
    ]
    search_fields = [
        'employee__first_name', 'employee__last_name',
        'employee__employee_code', 'site__name', 'job_role__name',
    ]
    list_filter = ['status', 'billing_type', 'org', 'job_role']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = [
        'org', 'employee', 'site', 'job_role',
        'mrf_line_item', 'hiring_application', 'created_by',
    ]


@admin.register(DeploymentHistory)
class DeploymentHistoryAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'created_at', 'action_type', 'employee', 'deployment',
        'from_status', 'to_status', 'actor',
    ]
    search_fields = [
        'employee__employee_code',
        'employee__first_name', 'employee__last_name',
        'note',
    ]
    list_filter = ['action_type', 'org', 'from_status', 'to_status']
    readonly_fields = [
        'org', 'employee', 'deployment', 'action_type',
        'from_status', 'to_status',
        'from_site', 'to_site', 'from_job_role', 'to_job_role',
        'actor', 'note', 'metadata', 'created_at', 'updated_at',
    ]
    raw_id_fields = [
        'org', 'employee', 'deployment',
        'from_site', 'to_site', 'from_job_role', 'to_job_role', 'actor',
    ]

from django.contrib import admin, messages

from .models import (
    MobilisationSetupRequest,
    MobilisationProposedDepartment,
    MobilisationProposedDepartmentRole,
    MobilisationProposedUser,
)


def retry_finalization(modeladmin, request, queryset):
    """Admin action: re-attempt finalization for failed/not-finalized approved requests."""
    from .services import retry_finalize_mobilisation_request
    from .exceptions import MobilisationFinalizationError

    success = failed = skipped = 0
    for obj in queryset:
        if obj.status != 'approved':
            skipped += 1
            continue
        if obj.finalization_status == 'finalized':
            skipped += 1
            continue
        try:
            retry_finalize_mobilisation_request(obj, actor=request.user)
            success += 1
        except (MobilisationFinalizationError, ValueError) as exc:
            failed += 1
            modeladmin.message_user(
                request,
                f"Mobilisation #{obj.pk}: finalization failed — {exc}",
                level=messages.ERROR,
            )
    if success:
        modeladmin.message_user(request, f"Finalized {success} request(s) successfully.")
    if skipped:
        modeladmin.message_user(
            request, f"Skipped {skipped} request(s) (already finalized or not approved).",
            level=messages.WARNING,
        )


retry_finalization.short_description = "Retry finalization for selected approved requests"


class ProposedDepartmentInline(admin.TabularInline):
    model = MobilisationProposedDepartment
    extra = 0
    fields = ['real_site', 'scope_level', 'name', 'code', 'is_locked', 'is_active']
    show_change_link = True
    raw_id_fields = ['real_site']


class ProposedDepartmentRoleInline(admin.TabularInline):
    model = MobilisationProposedDepartmentRole
    extra = 0
    fields = [
        'proposed_department', 'site_role_requirement',
        'real_site', 'job_role', 'approved_headcount_snapshot', 'is_active',
    ]
    readonly_fields = ['real_site', 'job_role', 'approved_headcount_snapshot']
    show_change_link = True
    raw_id_fields = ['proposed_department', 'site_role_requirement']


class ProposedUserInline(admin.TabularInline):
    model = MobilisationProposedUser
    extra = 0
    fields = [
        'full_name', 'email', 'user_type', 'access_role', 'scope_level',
        'real_site', 'is_primary_contact', 'invite_status', 'is_active',
    ]
    readonly_fields = ['invite_status', 'created_user']
    show_change_link = True
    raw_id_fields = ['access_role', 'real_site']


@admin.register(MobilisationSetupRequest)
class MobilisationSetupRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'mobilisation_type', 'client',
        'status', 'assigned_operations_owner', 'finalization_status',
        'budget_plan', 'requested_by', 'created_at',
    ]
    list_filter = [
        'status', 'mobilisation_type', 'finalization_status',
        'assigned_operations_owner', 'org',
    ]
    search_fields = [
        'client__name', 'summary', 'requested_by__username',
    ]
    readonly_fields = [
        'submitted_at', 'approved_at', 'rejected_at',
        'submitted_to_operations_at', 'setup_completed_at', 'setup_completed_by',
        'finalization_status', 'finalized_at', 'finalized_by',
        'finalization_error', 'created_at', 'updated_at',
    ]
    raw_id_fields = [
        'org', 'client', 'requested_by', 'assigned_operations_owner',
        'budget_plan', 'setup_completed_by', 'finalized_by',
    ]
    inlines = [ProposedDepartmentInline, ProposedDepartmentRoleInline, ProposedUserInline]
    actions = [retry_finalization]


@admin.register(MobilisationProposedDepartment)
class MobilisationProposedDepartmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'request', 'real_site', 'scope_level', 'name', 'code', 'is_locked', 'is_active']
    list_filter = ['is_active', 'is_locked', 'scope_level']
    search_fields = ['name', 'code']
    raw_id_fields = ['request', 'real_site']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(MobilisationProposedDepartmentRole)
class MobilisationProposedDepartmentRoleAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'request', 'proposed_department', 'site_role_requirement',
        'real_site', 'job_role', 'approved_headcount_snapshot', 'is_active',
    ]
    list_filter = ['is_active']
    search_fields = [
        'proposed_department__name',
        'site_role_requirement__job_role__name',
        'site_role_requirement__site__name',
    ]
    raw_id_fields = ['request', 'proposed_department', 'site_role_requirement']
    readonly_fields = ['real_site', 'job_role', 'approved_headcount_snapshot', 'created_at', 'updated_at']


@admin.register(MobilisationProposedUser)
class MobilisationProposedUserAdmin(admin.ModelAdmin):
    list_display = ['id', 'request', 'full_name', 'email', 'user_type', 'scope_level', 'is_active', 'invite_status']
    list_filter = ['user_type', 'scope_level', 'is_active', 'invite_status']
    search_fields = ['email', 'full_name']
    raw_id_fields = ['request', 'access_role', 'real_site', 'created_user']
    readonly_fields = ['created_at', 'updated_at', 'created_user', 'invite_status', 'invite_error']

from django.contrib import admin, messages

from .models import (
    ClientOnboardingRequest,
    ClientOnboardingProposedBudget,
    ClientOnboardingProposedSite,
    ClientOnboardingProposedDepartment,
    ClientOnboardingProposedSiteRoleRequirement,
    ClientOnboardingProposedUser,
)


def retry_finalization(modeladmin, request, queryset):
    """Admin action: re-attempt finalization for failed/not-finalized approved requests."""
    from .services import retry_finalize_client_onboarding_request
    from .exceptions import OnboardingFinalizationError

    success = failed = skipped = 0
    for obj in queryset:
        if obj.status != 'approved':
            skipped += 1
            continue
        if obj.finalization_status == 'finalized':
            skipped += 1
            continue
        try:
            retry_finalize_client_onboarding_request(obj, actor=request.user)
            success += 1
        except (OnboardingFinalizationError, ValueError) as exc:
            failed += 1
            modeladmin.message_user(
                request,
                f"Onboarding #{obj.pk}: finalization failed — {exc}",
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


class ProposedSiteInline(admin.TabularInline):
    model = ClientOnboardingProposedSite
    extra = 0
    fields = ['name', 'code', 'city', 'state', 'is_active']
    readonly_fields = ['created_at']
    show_change_link = True


class ProposedDepartmentInline(admin.TabularInline):
    model = ClientOnboardingProposedDepartment
    extra = 0
    fields = ['proposed_site', 'scope_level', 'name', 'code', 'is_active']
    show_change_link = True


class ProposedSRRInline(admin.TabularInline):
    model = ClientOnboardingProposedSiteRoleRequirement
    extra = 0
    fields = ['proposed_site', 'job_role', 'approved_headcount', 'billing_type', 'is_active']
    show_change_link = True


class ProposedBudgetInline(admin.TabularInline):
    model = ClientOnboardingProposedBudget
    extra = 0
    fields = [
        'name', 'code', 'budget_nature', 'budget_type', 'scope_level',
        'proposed_site', 'proposed_department', 'amount', 'currency', 'is_active',
    ]
    show_change_link = True


class ProposedUserInline(admin.TabularInline):
    model = ClientOnboardingProposedUser
    extra = 0
    fields = [
        'full_name', 'email', 'user_type', 'access_role', 'scope_level',
        'proposed_site', 'is_primary_contact', 'invite_status', 'is_active',
    ]
    readonly_fields = ['invite_status', 'created_user']
    show_change_link = True
    raw_id_fields = ['access_role', 'proposed_site']


@admin.register(ClientOnboardingRequest)
class ClientOnboardingRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'onboarding_type', 'client', 'proposed_client_name',
        'status', 'finalization_status', 'budget_plan', 'requested_by', 'created_at',
    ]
    list_filter = ['status', 'onboarding_type', 'finalization_status', 'org']
    search_fields = [
        'client__name', 'proposed_client_name', 'proposed_client_code',
        'summary', 'requested_by__username',
    ]
    readonly_fields = [
        'submitted_at', 'approved_at', 'rejected_at',
        'created_client', 'finalization_status', 'finalized_at', 'finalized_by',
        'finalization_error', 'created_at', 'updated_at',
    ]
    raw_id_fields = ['org', 'client', 'requested_by', 'budget_plan', 'created_client', 'finalized_by']
    inlines = [ProposedSiteInline, ProposedDepartmentInline, ProposedSRRInline, ProposedBudgetInline, ProposedUserInline]
    actions = [retry_finalization]


@admin.register(ClientOnboardingProposedSite)
class ClientOnboardingProposedSiteAdmin(admin.ModelAdmin):
    list_display = ['id', 'request', 'name', 'code', 'city', 'state', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'code', 'request__id']
    raw_id_fields = ['request', 'location_area']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ClientOnboardingProposedDepartment)
class ClientOnboardingProposedDepartmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'request', 'proposed_site', 'scope_level', 'name', 'code', 'is_active']
    list_filter = ['is_active', 'scope_level']
    search_fields = ['name', 'code']
    raw_id_fields = ['request', 'proposed_site']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ClientOnboardingProposedSiteRoleRequirement)
class ClientOnboardingProposedSRRAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'request', 'proposed_site', 'job_role', 'approved_headcount',
        'billing_type', 'is_active',
    ]
    list_filter = ['is_active', 'billing_type']
    search_fields = ['job_role__name']
    raw_id_fields = ['request', 'proposed_site', 'proposed_department', 'job_role', 'wage_category']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ClientOnboardingProposedBudget)
class ClientOnboardingProposedBudgetAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'request', 'name', 'code',
        'budget_nature', 'budget_type', 'scope_level',
        'amount', 'currency', 'is_active',
    ]
    list_filter = ['is_active', 'budget_nature', 'budget_type', 'scope_level']
    search_fields = ['name', 'code', 'request__id']
    raw_id_fields = ['request', 'proposed_site', 'proposed_department']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ClientOnboardingProposedUser)
class ClientOnboardingProposedUserAdmin(admin.ModelAdmin):
    list_display = ['id', 'request', 'full_name', 'email', 'user_type', 'scope_level', 'is_active', 'invite_status']
    list_filter = ['user_type', 'scope_level', 'is_active', 'invite_status']
    search_fields = ['email', 'full_name']
    raw_id_fields = ['request', 'access_role', 'proposed_site', 'created_user']
    readonly_fields = ['created_at', 'updated_at', 'created_user', 'invite_status', 'invite_error']

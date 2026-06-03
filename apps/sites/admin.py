from django.contrib import admin
from .models import Client, SiteProfile, SiteCommercial, SiteRoleRequirement


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'code', 'org', 'source_type', 'contact_name', 'contact_email',
        'industry', 'scope_node', 'owner_sales_user', 'is_active', 'created_at',
    ]
    search_fields = ['name', 'code', 'contact_name', 'contact_email', 'gst_number', 'scope_node__path']
    list_filter = ['is_active', 'source_type', 'org', 'industry']
    readonly_fields = ['created_at', 'updated_at', 'source_type', 'source_sales_lead', 'source_proposal_version']
    raw_id_fields = ['org', 'scope_node', 'created_by', 'owner_sales_user']


@admin.register(SiteProfile)
class SiteProfileAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'code', 'org', 'client', 'source_type', 'location_area', 'city', 'state',
        'shift_type', 'contact_person', 'is_active',
    ]
    search_fields = ['name', 'code', 'city', 'state', 'address', 'contact_person']
    list_filter = ['is_active', 'source_type', 'shift_type', 'org', 'client', 'location_area']
    readonly_fields = ['created_at', 'updated_at', 'source_type', 'source_sales_lead', 'source_proposal_version']
    raw_id_fields = ['org', 'client', 'scope_node', 'created_by', 'location_area']


@admin.register(SiteCommercial)
class SiteCommercialAdmin(admin.ModelAdmin):
    list_display = ['site', 'billing_rate', 'effective_from', 'effective_to', 'is_active']
    search_fields = ['site__name', 'site__code']
    list_filter = ['is_active']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['site']


@admin.register(SiteRoleRequirement)
class SiteRoleRequirementAdmin(admin.ModelAdmin):
    list_display = [
        'site', 'job_role', 'approved_headcount', 'billing_type', 'source_type',
        'billing_rate', 'wage_min', 'wage_max', 'wage_category',
        'shift_hours', 'effective_from', 'effective_to', 'is_active',
        'wage_rate',
    ]
    search_fields = ['site__name', 'site__code', 'job_role__name']
    list_filter = ['is_active', 'source_type', 'billing_type', 'job_role', 'wage_category']
    readonly_fields = [
        'wage_rate',
        'wage_rate_monthly_snapshot', 'wage_rate_daily_snapshot',
        'wage_rate_effective_from_snapshot', 'wage_rate_source_snapshot',
        'source_type', 'source_sales_lead', 'source_proposal_version',
        'created_at', 'updated_at',
    ]
    raw_id_fields = ['site', 'job_role', 'wage_category']

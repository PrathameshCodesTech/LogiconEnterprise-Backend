from django.contrib import admin
from .models import ManpowerRequest, MRFLineItem


class MRFLineItemInline(admin.TabularInline):
    model = MRFLineItem
    extra = 0
    raw_id_fields = ['site_role_requirement', 'job_role', 'wage_category']
    fields = [
        'job_role', 'headcount', 'site_role_requirement',
        'wage_category', 'min_wage_snapshot',
        'wage_min_requested', 'wage_max_requested',
        'billing_rate_snapshot', 'budget_min', 'budget_max',
        'replacement_for_employee',
    ]


@admin.register(ManpowerRequest)
class ManpowerRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'org', 'site', 'mrf_type', 'status', 'billing_type',
        'requested_by_type', 'required_by_date', 'client_visible',
        'requested_by', 'created_at',
    ]
    search_fields = ['org__name', 'site__name', 'department']
    list_filter = ['mrf_type', 'status', 'billing_type', 'requested_by_type', 'client_visible', 'org']
    readonly_fields = ['created_at', 'updated_at', 'submitted_at', 'approved_at', 'rejected_at']
    raw_id_fields = ['org', 'site', 'requested_by']
    inlines = [MRFLineItemInline]


@admin.register(MRFLineItem)
class MRFLineItemAdmin(admin.ModelAdmin):
    list_display = [
        'mrf', 'job_role', 'headcount', 'wage_category',
        'min_wage_snapshot', 'wage_min_requested', 'wage_max_requested',
        'billing_rate_snapshot',
    ]
    search_fields = ['mrf__id', 'job_role__name']
    list_filter = ['job_role', 'wage_category']
    raw_id_fields = ['mrf', 'site_role_requirement', 'job_role', 'wage_category']

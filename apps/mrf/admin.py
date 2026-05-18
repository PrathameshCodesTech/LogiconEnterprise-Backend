from django.contrib import admin
from .models import ManpowerRequest, MRFLineItem, MRFSupportRequirement


class MRFLineItemInline(admin.TabularInline):
    model = MRFLineItem
    extra = 0
    raw_id_fields = ['site_role_requirement', 'job_role', 'wage_category', 'budget_plan']
    fields = [
        'job_role', 'headcount', 'site_role_requirement',
        'wage_category', 'min_wage_snapshot',
        'wage_min_requested', 'wage_max_requested',
        'billing_rate_snapshot', 'budget_min', 'budget_max',
        'budget_plan', 'replacement_for_employee',
    ]


class MRFSupportRequirementInline(admin.StackedInline):
    model = MRFSupportRequirement
    extra = 0
    max_num = 1
    fieldsets = [
        ('IT Use Only', {
            'fields': [
                'laptop_required', 'desktop_required', 'mail_id_required',
                'hrms_login_required', 'outlook_required', 'ms_office_required',
                'windows_required', 'own_or_rental',
            ],
        }),
        ('Admin Use Only', {
            'fields': [
                'sim_card_required', 'data_card_required', 'uniform_required',
                'visiting_cards_required', 'seating_required',
                'admin_location', 'admin_other',
            ],
        }),
    ]


@admin.register(ManpowerRequest)
class ManpowerRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'org', 'site', 'request_number', 'mrf_type', 'status', 'billing_type',
        'requesting_department', 'required_department',
        'budget_plan', 'requested_by_type', 'required_by_date', 'client_visible',
        'requested_by', 'created_at',
    ]
    search_fields = [
        'org__name', 'site__name', 'department', 'request_number',
        'requesting_department__name', 'required_department__name',
        'reporting_to', 'mis_requirement',
    ]
    list_filter = [
        'mrf_type', 'status', 'billing_type', 'requested_by_type', 'client_visible',
        'org', 'requesting_department', 'required_department',
    ]
    readonly_fields = ['created_at', 'updated_at', 'submitted_at', 'approved_at', 'rejected_at']
    raw_id_fields = ['org', 'site', 'requested_by', 'requesting_department', 'required_department', 'budget_plan']
    fieldsets = [
        (None, {
            'fields': [
                'org', 'site', 'requested_by', 'requested_by_type',
                'mrf_type', 'status', 'billing_type',
                'requesting_department', 'required_department', 'department',
                'required_by_date', 'reason', 'client_visible', 'budget_plan',
                'submitted_at', 'approved_at', 'rejected_at',
                'created_at', 'updated_at',
            ],
        }),
        ('Client Form Fields', {
            'fields': [
                'request_number',
                'experience_min_years', 'experience_max_years',
                'reporting_to', 'mis_requirement',
                'education_requirement', 'gender_preference', 'special_requirement',
                'salary_range_text', 'ctc_budget_text',
                'reference_note', 'other_remarks',
            ],
            'classes': ['collapse'],
        }),
    ]
    inlines = [MRFLineItemInline, MRFSupportRequirementInline]


@admin.register(MRFLineItem)
class MRFLineItemAdmin(admin.ModelAdmin):
    list_display = [
        'mrf', 'job_role', 'headcount', 'wage_category',
        'min_wage_snapshot', 'wage_min_requested', 'wage_max_requested',
        'billing_rate_snapshot', 'budget_plan',
    ]
    search_fields = ['mrf__id', 'job_role__name']
    list_filter = ['job_role', 'wage_category']
    raw_id_fields = ['mrf', 'site_role_requirement', 'job_role', 'wage_category', 'budget_plan']


@admin.register(MRFSupportRequirement)
class MRFSupportRequirementAdmin(admin.ModelAdmin):
    list_display = ['mrf', 'uniform_required', 'laptop_required', 'mail_id_required', 'seating_required']
    raw_id_fields = ['mrf']
    fieldsets = [
        ('IT Use Only', {
            'fields': [
                'mrf',
                'laptop_required', 'desktop_required', 'mail_id_required',
                'hrms_login_required', 'outlook_required', 'ms_office_required',
                'windows_required', 'own_or_rental',
            ],
        }),
        ('Admin Use Only', {
            'fields': [
                'sim_card_required', 'data_card_required', 'uniform_required',
                'visiting_cards_required', 'seating_required',
                'admin_location', 'admin_other',
            ],
        }),
    ]

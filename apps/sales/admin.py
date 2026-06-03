from django.contrib import admin

from .models import (
    SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement,
    ProposalVersion, ProposalBudgetLine, ProposalBreakupLine,
    SalesProposalClientToken, ClientProposalResponse,
    SalesLeadActivity, SalesDocument,
    SiteSurveyScopeAnswer, SiteSurveyShiftDeployment, SiteSurveyLocationLine,
    SiteSurveyEquipmentLine, SiteSurveyIssueLine, SurveyRoleMapping,
    ProposalComponentRule,
)


# ─── Inlines ──────────────────────────────────────────────────────────────────

class SalesLeadSiteInline(admin.TabularInline):
    model = SalesLeadSite
    extra = 0
    fields = ['site_name', 'city', 'state', 'is_active']
    show_change_link = True


class SiteSurveyInline(admin.TabularInline):
    model = SiteSurvey
    extra = 0
    fields = ['site', 'survey_date', 'feasibility_status', 'status']
    show_change_link = True


class SalesRoleRequirementInline(admin.TabularInline):
    model = SalesRoleRequirement
    extra = 0
    raw_id_fields = ['site', 'survey', 'job_role', 'wage_category']
    fields = ['site', 'job_role', 'manpower_count', 'service_category', 'is_active']
    show_change_link = True


class ProposalVersionInline(admin.TabularInline):
    model = ProposalVersion
    extra = 0
    fields = [
        'version_number', 'status', 'internal_approval_status', 'client_approval_status',
        'subtotal_amount', 'management_fee_amount', 'gst_amount', 'grand_total',
    ]
    readonly_fields = fields
    show_change_link = True


class ProposalBudgetLineInline(admin.TabularInline):
    model = ProposalBudgetLine
    extra = 0
    raw_id_fields = ['site', 'job_role']
    fields = ['site', 'job_role', 'description', 'manpower_count', 'unit_cost', 'total_cost', 'sort_order']


class ProposalBreakupLineInline(admin.TabularInline):
    model = ProposalBreakupLine
    extra = 0
    raw_id_fields = ['site', 'job_role']
    fields = ['component_name', 'component_type', 'percentage', 'amount', 'sort_order']


class ClientProposalResponseInline(admin.TabularInline):
    model = ClientProposalResponse
    extra = 0
    fields = ['proposal_version', 'client_response', 'responded_at']
    readonly_fields = ['responded_at']


# ─── ModelAdmins ──────────────────────────────────────────────────────────────

@admin.register(SalesLead)
class SalesLeadAdmin(admin.ModelAdmin):
    list_display = [
        'client_name', 'org', 'lead_type', 'existing_client', 'current_stage', 'current_status',
        'sales_person', 'rfp_required', 'rfq_required', 'created_at',
    ]
    list_filter = ['lead_type', 'current_stage', 'current_status', 'org', 'rfp_required', 'rfq_required']
    search_fields = ['client_name', 'client_contact_person', 'client_email']
    raw_id_fields = ['org', 'sales_person', 'created_by', 'final_approved_proposal', 'source_onboarding_request', 'existing_client']
    readonly_fields = ['created_at', 'updated_at', 'converted_on']
    inlines = [SalesLeadSiteInline, SiteSurveyInline, SalesRoleRequirementInline, ProposalVersionInline, ClientProposalResponseInline]
    fieldsets = [
        ('Lead Info', {
            'fields': [
                'org', 'lead_type', 'existing_client',
                'client_name', 'client_contact_person', 'client_email', 'client_phone',
                'sales_person', 'created_by',
            ],
        }),
        ('Stage & Status', {
            'fields': ['current_stage', 'current_status', 'rfp_required', 'rfq_required'],
        }),
        ('Requirements', {
            'fields': ['requirement_details', 'initial_business_requirement', 'sales_remarks'],
            'classes': ['collapse'],
        }),
        ('Outcome', {
            'fields': ['final_approved_proposal', 'converted_on', 'source_onboarding_request'],
        }),
        ('Timestamps', {
            'fields': ['created_at', 'updated_at'],
            'classes': ['collapse'],
        }),
    ]


@admin.register(SalesLeadSite)
class SalesLeadSiteAdmin(admin.ModelAdmin):
    list_display = ['site_name', 'lead', 'city', 'state', 'is_active']
    list_filter = ['is_active']
    search_fields = ['site_name', 'city', 'lead__client_name']
    raw_id_fields = ['lead', 'location_area']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SiteSurvey)
class SiteSurveyAdmin(admin.ModelAdmin):
    list_display = ['lead', 'site', 'survey_date', 'feasibility_status', 'status', 'survey_done_by']
    list_filter = ['status', 'feasibility_status']
    search_fields = ['lead__client_name', 'site__site_name']
    raw_id_fields = ['lead', 'site', 'survey_done_by']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [SalesRoleRequirementInline]


@admin.register(SalesRoleRequirement)
class SalesRoleRequirementAdmin(admin.ModelAdmin):
    list_display = ['job_role', 'manpower_count', 'site', 'lead', 'is_active']
    list_filter = ['is_active']
    search_fields = ['job_role__name', 'lead__client_name', 'site__site_name']
    raw_id_fields = ['lead', 'site', 'survey', 'job_role', 'wage_category']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ProposalVersion)
class ProposalVersionAdmin(admin.ModelAdmin):
    list_display = [
        'lead', 'version_number', 'status',
        'internal_approval_status', 'client_approval_status',
        'grand_total', 'is_final_approved_version',
    ]
    list_filter = ['status', 'internal_approval_status', 'client_approval_status', 'gst_applicable']
    search_fields = ['lead__client_name']
    raw_id_fields = ['lead', 'created_by', 'source_version']
    readonly_fields = ['created_at', 'updated_at', 'sent_to_client_at', 'locked_at']
    inlines = [ProposalBudgetLineInline, ProposalBreakupLineInline, ClientProposalResponseInline]


@admin.register(ProposalBudgetLine)
class ProposalBudgetLineAdmin(admin.ModelAdmin):
    list_display = ['description', 'proposal_version', 'manpower_count', 'unit_cost', 'total_cost', 'sort_order']
    raw_id_fields = ['proposal_version', 'site', 'job_role']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ProposalBreakupLine)
class ProposalBreakupLineAdmin(admin.ModelAdmin):
    list_display = ['component_name', 'component_type', 'proposal_version', 'percentage', 'amount', 'sort_order']
    list_filter = ['component_type']
    raw_id_fields = ['proposal_version', 'site', 'job_role']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SalesProposalClientToken)
class SalesProposalClientTokenAdmin(admin.ModelAdmin):
    list_display = [
        'proposal_version', 'recipient_email', 'expires_at', 'used_at',
        'is_revoked', 'created_at',
    ]
    list_filter = ['is_revoked']
    search_fields = ['recipient_email', 'lead__client_name']
    raw_id_fields = ['proposal_version', 'lead', 'created_by']
    readonly_fields = ['token_hash', 'created_at', 'last_accessed_at']


@admin.register(ClientProposalResponse)
class ClientProposalResponseAdmin(admin.ModelAdmin):
    list_display = ['lead', 'proposal_version', 'client_response', 'responded_at', 'sent_to_client_at']
    list_filter = ['client_response']
    search_fields = ['lead__client_name']
    raw_id_fields = ['lead', 'proposal_version', 'sent_to_client_by']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SalesLeadActivity)
class SalesLeadActivityAdmin(admin.ModelAdmin):
    list_display = ['activity_type', 'lead', 'actor', 'proposal_version', 'site', 'created_at']
    list_filter = ['activity_type', 'org']
    search_fields = ['lead__client_name', 'title', 'message']
    raw_id_fields = ['org', 'lead', 'actor', 'proposal_version', 'site']
    readonly_fields = ['created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SalesDocument)
class SalesDocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'document_type', 'lead', 'proposal_version', 'is_active', 'file_size', 'created_at']
    list_filter = ['document_type', 'is_active', 'org']
    search_fields = ['title', 'file_name', 'lead__client_name']
    raw_id_fields = ['org', 'lead', 'uploaded_by', 'proposal_version', 'site']
    readonly_fields = ['file_name', 'file_size', 'content_type', 'created_at', 'updated_at']


# ─── Phase H: Survey Excel structure + Component rules ────────────────────────

@admin.register(SiteSurveyScopeAnswer)
class SiteSurveyScopeAnswerAdmin(admin.ModelAdmin):
    list_display = ['survey', 'category', 'field_key', 'sort_order']
    list_filter = ['category']
    search_fields = ['field_key', 'field_label', 'value_text']
    raw_id_fields = ['survey']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SiteSurveyShiftDeployment)
class SiteSurveyShiftDeploymentAdmin(admin.ModelAdmin):
    list_display = ['survey', 'description', 'line_type', 'total_count', 'sort_order']
    list_filter = ['line_type']
    search_fields = ['description']
    raw_id_fields = ['survey']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SiteSurveyLocationLine)
class SiteSurveyLocationLineAdmin(admin.ModelAdmin):
    list_display = ['survey', 'location_name', 'line_type', 'present_count', 'proposed_count', 'sort_order']
    list_filter = ['line_type']
    search_fields = ['location_name']
    raw_id_fields = ['survey']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SiteSurveyEquipmentLine)
class SiteSurveyEquipmentLineAdmin(admin.ModelAdmin):
    list_display = [
        'survey', 'equipment_category', 'description', 'line_type',
        'unit_count', 'amount', 'total', 'sort_order',
    ]
    list_filter = ['equipment_category', 'line_type']
    search_fields = ['description']
    raw_id_fields = ['survey']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SiteSurveyIssueLine)
class SiteSurveyIssueLineAdmin(admin.ModelAdmin):
    list_display = ['survey', 'issue', 'sort_order']
    search_fields = ['issue', 'improvement_details']
    raw_id_fields = ['survey']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SurveyRoleMapping)
class SurveyRoleMappingAdmin(admin.ModelAdmin):
    list_display = [
        'description_text', 'org', 'job_role', 'wage_category',
        'service_category', 'shift_hours', 'working_days', 'is_active',
    ]
    list_filter = ['org', 'wage_category', 'service_category', 'is_active']
    search_fields = ['description_text', 'job_role__name', 'wage_category__name']
    raw_id_fields = ['org', 'job_role', 'wage_category']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ProposalComponentRule)
class ProposalComponentRuleAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'component_name', 'org', 'calculation_type',
        'percentage', 'fixed_amount', 'is_active', 'sort_order',
    ]
    list_filter = ['calculation_type', 'component_type', 'is_active']
    search_fields = ['code', 'component_name']
    raw_id_fields = ['org']
    readonly_fields = ['created_at', 'updated_at']

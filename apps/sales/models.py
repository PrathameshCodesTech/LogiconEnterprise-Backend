"""
apps/sales/models.py

Sales pipeline: Lead → Site Survey → Role Requirements → Proposal Versioning → Client Negotiation
"""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


# ─── Choice constants ──────────────────────────────────────────────────────────

LEAD_TYPE_CHOICES = [
    ('new_client', 'New Client'),
    ('site_expansion', 'Site Expansion'),
    ('scope_expansion', 'Scope Expansion'),
    ('renewal', 'Renewal'),
]

LEAD_PRIORITY_CHOICES = [
    ('low', 'Low'),
    ('medium', 'Medium'),
    ('high', 'High'),
    ('urgent', 'Urgent'),
]

LEAD_STAGE_CHOICES = [
    ('draft', 'Draft'),
    ('submitted_to_operations', 'Submitted to Operations'),
    ('site_survey_in_progress', 'Site Survey In Progress'),
    ('site_survey_completed', 'Site Survey Completed'),
    ('budget_generated', 'Budget Generated'),
    ('sales_review', 'Sales Review'),
    ('internal_approval', 'Internal Approval'),
    ('internally_approved', 'Internally Approved'),
    ('sent_to_client', 'Sent to Client'),
    ('client_negotiation', 'Client Negotiation'),
    ('client_revision_required', 'Client Revision Required'),
    ('client_rejected', 'Client Rejected'),
    ('client_approved', 'Client Approved'),
    ('won', 'Won'),
    ('lost', 'Lost'),
    ('closed', 'Closed'),
]

LEAD_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('blocked', 'Blocked'),
    ('rejected', 'Rejected'),
    ('approved', 'Approved'),
    ('won', 'Won'),
    ('lost', 'Lost'),
    ('closed', 'Closed'),
]

FEASIBILITY_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('feasible', 'Feasible'),
    ('not_feasible', 'Not Feasible'),
    ('needs_review', 'Needs Review'),
]

SURVEY_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('in_progress', 'In Progress'),
    ('completed', 'Completed'),
]

PROPOSAL_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('generated', 'Generated'),
    ('sales_review', 'Sales Review'),
    ('submitted_internal', 'Submitted for Internal Approval'),
    ('internal_rejected', 'Internal Rejected'),
    ('internally_approved', 'Internally Approved'),
    ('sent_to_client', 'Sent to Client'),
    ('client_negotiation', 'Client Negotiation'),
    ('client_rejected', 'Client Rejected'),
    ('client_revision_required', 'Client Revision Required'),
    ('client_approved', 'Client Approved'),
    ('locked', 'Locked'),
]

INTERNAL_APPROVAL_STATUS_CHOICES = [
    ('not_started', 'Not Started'),
    ('in_progress', 'In Progress'),
    ('rejected', 'Rejected'),
    ('approved', 'Approved'),
]

CLIENT_APPROVAL_STATUS_CHOICES = [
    ('not_sent', 'Not Sent'),
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('negotiation', 'Negotiation'),
    ('revision_required', 'Revision Required'),
]

COMPONENT_TYPE_CHOICES = [
    ('earning', 'Earning'),
    ('employee_deduction', 'Employee Deduction'),
    ('employer_contribution', 'Employer Contribution'),
    ('statutory', 'Statutory'),
    ('reimbursement', 'Reimbursement'),
    ('equipment', 'Equipment'),
    ('management_fee', 'Management Fee'),
    ('tax', 'Tax'),
    ('total', 'Total'),
]

CLIENT_RESPONSE_CHOICES = [
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('negotiation_required', 'Negotiation Required'),
    ('revision_required', 'Revision Required'),
]

ACTIVITY_TYPE_CHOICES = [
    ('lead_created', 'Lead Created'),
    ('submitted_to_operations', 'Submitted to Operations'),
    ('survey_assigned', 'Survey Assigned'),
    ('survey_started', 'Survey Started'),
    ('survey_completed', 'Survey Completed'),
    ('role_requirement_added', 'Role Requirement Added'),
    ('role_requirement_approved', 'Role Requirement Approved'),
    ('proposal_generated', 'Proposal Generated'),
    ('proposal_submitted_internal', 'Proposal Submitted for Internal Approval'),
    ('proposal_internally_approved', 'Proposal Internally Approved'),
    ('proposal_internal_rejected', 'Proposal Internal Rejected'),
    ('proposal_sent_to_client', 'Proposal Sent to Client'),
    ('client_response_received', 'Client Response Received'),
    ('proposal_revision_created', 'Proposal Revision Created'),
    ('lead_won', 'Lead Won'),
    ('mobilisation_created', 'Mobilisation Created'),
    ('converted', 'Converted'),
    ('document_uploaded', 'Document Uploaded'),
    ('note_added', 'Note Added'),
]

DOCUMENT_TYPE_CHOICES = [
    ('rfp', 'RFP'),
    ('rfq', 'RFQ'),
    ('site_survey_photo', 'Site Survey Photo'),
    ('site_survey_document', 'Site Survey Document'),
    ('proposal_pdf', 'Proposal PDF'),
    ('signed_approval', 'Signed Approval'),
    ('po', 'Purchase Order'),
    ('contract', 'Contract'),
    ('other', 'Other'),
]

# Phase H — survey Excel structure
SCOPE_CATEGORY_CHOICES = [
    ('client_scope_site',  'Client Scope / Site Details'),
    ('premises',           'Premises Details'),
    ('hk_info',            'HK Information'),
    ('technical_scope',    'Technical Scope'),
    ('existing_deployment', 'Existing Deployment Structure'),
]

SHIFT_LINE_TYPE_CHOICES = [
    ('item', 'Item'),
    ('subtotal', 'Sub Total'),
    ('total', 'Total'),
]

LOCATION_LINE_TYPE_CHOICES = [
    ('item', 'Item'),
    ('total', 'Total'),
]

EQUIPMENT_CATEGORY_CHOICES = [
    ('major', 'Major Equipment'),
    ('minor', 'Minor Equipment'),
]

EQUIPMENT_LINE_TYPE_CHOICES = [
    ('item', 'Item'),
    ('total', 'Total (Approx.)'),
    ('amortisation_cost', 'Cost of Amortisation'),
    ('maintenance_cost', 'Equipment Maintenance & Consumables Cost'),
    ('amortized_monthly', 'Amortized Monthly Cost'),
    ('monthly_total', 'Total Equipment Cost per month'),
]

CALCULATION_TYPE_CHOICES = [
    ('percent_of_basic', 'Percent of Basic'),
    ('percent_of_gross', 'Percent of Gross'),
    ('percent_of_other', 'Percent of Other Component'),
    ('fixed', 'Fixed Amount'),
]


# ─── Models ───────────────────────────────────────────────────────────────────

class SalesLead(TimeStampedModel):
    """Top-level sales opportunity for a prospect client."""

    org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='sales_leads',
    )
    lead_type = models.CharField(
        max_length=20, choices=LEAD_TYPE_CHOICES, default='new_client',
    )
    existing_client = models.ForeignKey(
        'sites.Client', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='expansion_leads',
    )
    client_name = models.CharField(max_length=255)
    client_contact_person = models.CharField(max_length=255, blank=True)
    client_email = models.EmailField(blank=True)
    client_phone = models.CharField(max_length=32, blank=True)
    sales_person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='managed_sales_leads',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_sales_leads',
    )
    current_stage = models.CharField(
        max_length=40, choices=LEAD_STAGE_CHOICES, default='draft',
    )
    current_status = models.CharField(
        max_length=20, choices=LEAD_STATUS_CHOICES, default='draft',
    )
    lead_source = models.CharField(max_length=128, blank=True)
    industry = models.CharField(max_length=128, blank=True)
    priority = models.CharField(
        max_length=16, choices=LEAD_PRIORITY_CHOICES, default='medium',
    )
    expected_start_date = models.DateField(null=True, blank=True)
    expected_contract_months = models.PositiveIntegerField(null=True, blank=True)
    estimated_monthly_value = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    submitted_to_operations_at = models.DateTimeField(null=True, blank=True)
    submitted_to_operations_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='submitted_sales_leads',
    )
    operations_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='owned_sales_leads',
    )
    rfp_required = models.BooleanField(default=False)
    rfq_required = models.BooleanField(default=False)
    requirement_details = models.TextField(blank=True)
    initial_business_requirement = models.TextField(blank=True)
    sales_remarks = models.TextField(blank=True)
    final_approved_proposal = models.ForeignKey(
        'ProposalVersion', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_for_leads',
    )
    converted_on = models.DateTimeField(null=True, blank=True)
    source_onboarding_request = models.ForeignKey(
        'mobilisation.MobilisationSetupRequest', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='source_sales_leads',
    )

    class Meta:
        verbose_name = 'Sales Lead'
        verbose_name_plural = 'Sales Leads'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['org', 'current_stage']),
            models.Index(fields=['org', 'current_status']),
            models.Index(fields=['org', 'lead_type']),
        ]

    def __str__(self):
        return f"{self.client_name} ({self.get_current_stage_display()})"


class SalesLeadSite(TimeStampedModel):
    """A prospective site associated with a sales lead."""

    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='sites',
    )
    site_name = models.CharField(max_length=255)
    site_address = models.TextField(blank=True)
    city = models.CharField(max_length=128, blank=True)
    state = models.CharField(max_length=128, blank=True)
    location_area = models.ForeignKey(
        'wages.LocationArea', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sales_lead_sites',
    )
    remarks = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Sales Lead Site'
        verbose_name_plural = 'Sales Lead Sites'
        ordering = ['lead', 'site_name']

    def __str__(self):
        return f"{self.site_name} — {self.lead.client_name}"


class SiteSurvey(TimeStampedModel):
    """Operations site survey for a sales lead site."""

    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='surveys',
    )
    site = models.ForeignKey(
        SalesLeadSite, on_delete=models.CASCADE, related_name='surveys',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_surveys',
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    survey_done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='conducted_surveys',
    )
    survey_date = models.DateField(null=True, blank=True)
    business_volume = models.TextField(blank=True)
    service_type = models.CharField(max_length=128, blank=True)
    feasibility_status = models.CharField(
        max_length=20, choices=FEASIBILITY_STATUS_CHOICES, default='pending',
    )
    survey_remarks = models.TextField(blank=True)
    survey_notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=SURVEY_STATUS_CHOICES, default='pending',
    )

    class Meta:
        verbose_name = 'Site Survey'
        verbose_name_plural = 'Site Surveys'
        constraints = [
            models.UniqueConstraint(fields=['lead', 'site'], name='uq_site_survey_lead_site'),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"Survey: {self.site.site_name} — {self.lead.client_name}"


class SalesRoleRequirement(TimeStampedModel):
    """Estimated manpower role requirements for a sales lead site."""

    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='role_requirements',
    )
    site = models.ForeignKey(
        SalesLeadSite, on_delete=models.CASCADE, related_name='role_requirements',
    )
    survey = models.ForeignKey(
        SiteSurvey, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='role_requirements',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole', on_delete=models.CASCADE, related_name='sales_role_requirements',
    )
    wage_category = models.ForeignKey(
        'wages.WageCategory', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sales_role_requirements',
    )
    service_category = models.CharField(max_length=128, blank=True)
    manpower_count = models.PositiveIntegerField(default=1)
    source_shift_deployment = models.ForeignKey(
        'SiteSurveyShiftDeployment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='generated_role_requirements',
    )
    general_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    first_shift_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    second_shift_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    night_shift_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reliever_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shift_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    working_days = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    operational_base_wage = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    operational_base_wage_source = models.CharField(max_length=40, blank=True)
    operational_base_wage_overridden = models.BooleanField(default=False)
    operational_base_wage_override_reason = models.TextField(blank=True)
    operational_monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    remarks = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_from_survey = models.BooleanField(default=False)
    approved_by_operations = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_sales_role_requirements',
    )

    class Meta:
        verbose_name = 'Sales Role Requirement'
        verbose_name_plural = 'Sales Role Requirements'
        ordering = ['lead', 'site', 'job_role']

    def __str__(self):
        return f"{self.job_role.name} x{self.manpower_count} — {self.site.site_name}"


class ProposalVersion(TimeStampedModel):
    """A versioned sales proposal for a sales lead."""

    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='proposal_versions',
    )
    version_number = models.PositiveIntegerField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_proposal_versions',
    )
    source_version = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='revisions',
    )
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    subtotal_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    management_fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    manpower_total = models.PositiveIntegerField(default=0)
    management_fee_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    gst_applicable = models.BooleanField(default=False)
    status = models.CharField(
        max_length=30, choices=PROPOSAL_STATUS_CHOICES, default='draft',
    )
    internal_approval_status = models.CharField(
        max_length=20, choices=INTERNAL_APPROVAL_STATUS_CHOICES, default='not_started',
    )
    client_approval_status = models.CharField(
        max_length=20, choices=CLIENT_APPROVAL_STATUS_CHOICES, default='not_sent',
    )
    client_remarks = models.TextField(blank=True)
    sales_remarks = models.TextField(blank=True)
    is_final_approved_version = models.BooleanField(default=False)
    sent_to_client_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    submitted_internal_at = models.DateTimeField(null=True, blank=True)
    internally_approved_at = models.DateTimeField(null=True, blank=True)
    client_approved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    validity_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'Proposal Version'
        verbose_name_plural = 'Proposal Versions'
        constraints = [
            models.UniqueConstraint(
                fields=['lead', 'version_number'],
                name='uq_proposal_lead_version',
            ),
        ]
        ordering = ['lead', '-version_number']

    def __str__(self):
        return f"v{self.version_number} — {self.lead.client_name} ({self.get_status_display()})"


class ProposalBudgetLine(TimeStampedModel):
    """Summary cost line in a proposal version."""

    proposal_version = models.ForeignKey(
        ProposalVersion, on_delete=models.CASCADE, related_name='budget_lines',
    )
    site = models.ForeignKey(
        SalesLeadSite, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposal_budget_lines',
    )
    service_category = models.CharField(max_length=128, blank=True)
    job_role = models.ForeignKey(
        'jobs.JobRole', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposal_budget_lines',
    )
    role_requirement = models.ForeignKey(
        'SalesRoleRequirement', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposal_budget_lines',
    )
    description = models.CharField(max_length=255)
    manpower_count = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    source_unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    source_unit_cost_origin = models.CharField(max_length=40, blank=True)
    remarks = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_manual_override = models.BooleanField(default=False)
    override_reason = models.TextField(blank=True)
    overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='overridden_proposal_budget_lines',
    )
    overridden_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Proposal Budget Line'
        verbose_name_plural = 'Proposal Budget Lines'
        ordering = ['proposal_version', 'sort_order', 'id']

    def __str__(self):
        return f"{self.description} — v{self.proposal_version.version_number}"


class ProposalBreakupLine(TimeStampedModel):
    """Salary/statutory component breakup in a proposal version."""

    proposal_version = models.ForeignKey(
        ProposalVersion, on_delete=models.CASCADE, related_name='breakup_lines',
    )
    site = models.ForeignKey(
        SalesLeadSite, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposal_breakup_lines',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposal_breakup_lines',
    )
    role_requirement = models.ForeignKey(
        'SalesRoleRequirement', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='proposal_breakup_lines',
    )
    component_name = models.CharField(max_length=128)
    component_type = models.CharField(max_length=30, choices=COMPONENT_TYPE_CHOICES)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    remarks = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_manual_override = models.BooleanField(default=False)
    override_reason = models.TextField(blank=True)
    overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='overridden_proposal_breakup_lines',
    )
    overridden_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Proposal Breakup Line'
        verbose_name_plural = 'Proposal Breakup Lines'
        ordering = ['proposal_version', 'sort_order', 'id']

    def __str__(self):
        return f"{self.component_name} — v{self.proposal_version.version_number}"


class SalesProposalClientToken(models.Model):
    """
    Secure hashed token for external client proposal review/response.
    Raw token is only returned once at creation (email link); DB stores hash only.
    """

    proposal_version = models.ForeignKey(
        ProposalVersion, on_delete=models.CASCADE, related_name='client_tokens',
    )
    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='proposal_client_tokens',
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    recipient_email = models.EmailField()
    recipient_name = models.CharField(max_length=255, blank=True)
    email_subject = models.CharField(max_length=255, blank=True)
    email_body = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    is_revoked = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_proposal_client_tokens',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Sales Proposal Client Token'
        verbose_name_plural = 'Sales Proposal Client Tokens'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"Client token for proposal v{self.proposal_version.version_number} "
            f"→ {self.recipient_email}"
        )


class ClientProposalResponse(TimeStampedModel):
    """Client's response record for a specific proposal version."""

    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='client_responses',
    )
    proposal_version = models.ForeignKey(
        ProposalVersion, on_delete=models.CASCADE, related_name='client_responses',
    )
    sent_to_client_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sent_proposals',
    )
    sent_to_client_at = models.DateTimeField(null=True, blank=True)
    client_response = models.CharField(
        max_length=30, choices=CLIENT_RESPONSE_CHOICES, default='pending',
    )
    client_remarks = models.TextField(blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by_name = models.CharField(max_length=255, blank=True)
    responded_by_email = models.EmailField(blank=True)
    next_action_due_date = models.DateField(null=True, blank=True)
    meeting_notes = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Client Proposal Response'
        verbose_name_plural = 'Client Proposal Responses'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"Response({self.client_response}) — "
            f"{self.lead.client_name} v{self.proposal_version.version_number}"
        )


class SalesLeadActivity(models.Model):
    """Immutable audit trail entry for a sales lead timeline event."""

    org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='sales_activities',
    )
    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='activities',
    )
    proposal_version = models.ForeignKey(
        'ProposalVersion', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='activities',
    )
    site = models.ForeignKey(
        'SalesLeadSite', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='activities',
    )
    activity_type = models.CharField(max_length=40, choices=ACTIVITY_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sales_activities',
    )
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sales Lead Activity'
        verbose_name_plural = 'Sales Lead Activities'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['org', 'lead', 'activity_type']),
            models.Index(fields=['org', 'lead', 'created_at']),
        ]

    def __str__(self):
        return f"{self.activity_type} — {self.lead.client_name}"


def _sales_document_upload_path(instance, filename):
    return f"sales/{instance.org_id}/lead_{instance.lead_id}/{instance.document_type}/{filename}"


class SalesDocument(TimeStampedModel):
    """File attachment associated with a sales lead, proposal, or site."""

    org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='sales_documents',
    )
    lead = models.ForeignKey(
        SalesLead, on_delete=models.CASCADE, related_name='documents',
    )
    proposal_version = models.ForeignKey(
        'ProposalVersion', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='documents',
    )
    site = models.ForeignKey(
        'SalesLeadSite', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='documents',
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='uploaded_sales_documents',
    )
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=_sales_document_upload_path)
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text='File size in bytes')
    content_type = models.CharField(max_length=128)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Sales Document'
        verbose_name_plural = 'Sales Documents'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['org', 'lead', 'document_type']),
            models.Index(fields=['org', 'lead', 'is_active']),
        ]

    def __str__(self):
        return f"{self.title} ({self.document_type}) — {self.lead.client_name}"


# ─── Phase H: Survey Excel structure ──────────────────────────────────────────

class SiteSurveyScopeAnswer(TimeStampedModel):
    """One free-text answer cell from the scope/premises/HK/technical sections."""

    survey = models.ForeignKey(
        SiteSurvey, on_delete=models.CASCADE, related_name='scope_answers',
    )
    category = models.CharField(max_length=32, choices=SCOPE_CATEGORY_CHOICES)
    field_key = models.CharField(max_length=64)
    field_label = models.CharField(max_length=200)
    value_text = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Site Survey Scope Answer'
        verbose_name_plural = 'Site Survey Scope Answers'
        ordering = ['category', 'sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'category', 'field_key'],
                name='uq_survey_scope_answer_key',
            ),
        ]

    def __str__(self):
        return f"{self.category}.{self.field_key} (survey #{self.survey_id})"


class SiteSurveyShiftDeployment(TimeStampedModel):
    """One row of the shift / deployment table (Site Mgmt team, Janitor, Sub Total, ...)."""

    survey = models.ForeignKey(
        SiteSurvey, on_delete=models.CASCADE, related_name='shift_deployments',
    )
    job_role = models.ForeignKey(
        'jobs.JobRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='survey_shift_deployments',
        help_text='Optional structured role for rows added from the job role master.',
    )
    description = models.CharField(max_length=200)
    general_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    first_shift_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    second_shift_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    night_shift_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reliever_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shift_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    working_days = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    base_wage = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    base_wage_source = models.CharField(max_length=40, blank=True)
    base_wage_overridden = models.BooleanField(default=False)
    base_wage_override_reason = models.TextField(blank=True)
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    remarks = models.TextField(blank=True)
    is_applicable = models.BooleanField(default=True)
    not_applicable_reason = models.TextField(blank=True)
    line_type = models.CharField(
        max_length=20, choices=SHIFT_LINE_TYPE_CHOICES, default='item',
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Site Survey Shift Deployment Row'
        verbose_name_plural = 'Site Survey Shift Deployment Rows'
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'description'],
                name='uq_survey_shift_deployment_desc',
            ),
        ]

    def __str__(self):
        return f"{self.description} (survey #{self.survey_id})"


class SiteSurveyLocationLine(TimeStampedModel):
    """One row of the per-location present/proposed staffing table."""

    survey = models.ForeignKey(
        SiteSurvey, on_delete=models.CASCADE, related_name='location_lines',
    )
    location_name = models.CharField(max_length=255)
    present_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    proposed_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    remarks = models.TextField(blank=True)
    is_applicable = models.BooleanField(default=True)
    not_applicable_reason = models.TextField(blank=True)
    line_type = models.CharField(
        max_length=20, choices=LOCATION_LINE_TYPE_CHOICES, default='item',
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Site Survey Location Line'
        verbose_name_plural = 'Site Survey Location Lines'
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'location_name'],
                name='uq_survey_location_line_name',
            ),
        ]

    def __str__(self):
        return f"{self.location_name} (survey #{self.survey_id})"


class SiteSurveyEquipmentLine(TimeStampedModel):
    """
    Single row in the major or minor equipment table.

    `item` rows auto-compute total = unit_count * amount on save.
    Aggregate rows (total, amortisation_cost, maintenance_cost, amortized_monthly,
    monthly_total) preserve whatever total the operator entered.
    """

    survey = models.ForeignKey(
        SiteSurvey, on_delete=models.CASCADE, related_name='equipment_lines',
    )
    equipment_category = models.CharField(
        max_length=10, choices=EQUIPMENT_CATEGORY_CHOICES,
    )
    description = models.CharField(max_length=255)
    unit_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_applicable = models.BooleanField(default=True)
    not_applicable_reason = models.TextField(blank=True)
    line_type = models.CharField(
        max_length=24, choices=EQUIPMENT_LINE_TYPE_CHOICES, default='item',
    )
    amortisation_months = models.PositiveIntegerField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Site Survey Equipment Line'
        verbose_name_plural = 'Site Survey Equipment Lines'
        ordering = ['equipment_category', 'sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'equipment_category', 'description'],
                name='uq_survey_equipment_line',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.line_type == 'item':
            self.total = (self.unit_count or 0) * (self.amount or 0)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.equipment_category}:{self.description} (survey #{self.survey_id})"


class SiteSurveyIssueLine(TimeStampedModel):
    """One issue / improvement row from the operator survey."""

    survey = models.ForeignKey(
        SiteSurvey, on_delete=models.CASCADE, related_name='issue_lines',
    )
    issue = models.CharField(max_length=500)
    improvement_details = models.TextField(blank=True)
    is_applicable = models.BooleanField(default=True)
    not_applicable_reason = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Site Survey Issue Line'
        verbose_name_plural = 'Site Survey Issue Lines'
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'issue'],
                name='uq_survey_issue_line',
            ),
        ]

    def __str__(self):
        return f"{self.issue[:60]}... (survey #{self.survey_id})"


class SurveyRoleMapping(TimeStampedModel):
    """Maps a survey deployment row label to proposal-ready role defaults."""

    org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='survey_role_mappings',
    )
    description_text = models.CharField(max_length=200)
    job_role = models.ForeignKey(
        'jobs.JobRole', on_delete=models.CASCADE,
        related_name='survey_role_mappings',
    )
    wage_category = models.ForeignKey(
        'wages.WageCategory', on_delete=models.PROTECT,
        related_name='survey_role_mappings',
    )
    service_category = models.CharField(max_length=128, blank=True)
    shift_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    working_days = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    remarks = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Survey Role Mapping'
        verbose_name_plural = 'Survey Role Mappings'
        ordering = ['org', 'description_text']
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'description_text'],
                name='uq_survey_role_mapping_org_description',
            ),
        ]
        indexes = [
            models.Index(fields=['org', 'description_text']),
            models.Index(fields=['org', 'is_active']),
        ]

    def __str__(self):
        return f"{self.description_text} -> {self.job_role}"


class ProposalComponentRule(TimeStampedModel):
    """
    Configurable rule for one salary/statutory breakup component.

    `org` is nullable for global default rules. Org-specific rules win per code;
    global rules fill the gaps. Proposal generation requires a complete active
    ruleset for the org; otherwise generation fails with a configuration error.
    """

    org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='proposal_component_rules',
    )
    code = models.CharField(max_length=64)
    component_name = models.CharField(max_length=128)
    component_type = models.CharField(max_length=32, choices=COMPONENT_TYPE_CHOICES)
    calculation_type = models.CharField(max_length=24, choices=CALCULATION_TYPE_CHOICES)
    percentage = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True,
    )
    fixed_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    base_component_code = models.CharField(max_length=64, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Proposal Component Rule'
        verbose_name_plural = 'Proposal Component Rules'
        ordering = ['sort_order', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['org', 'code'],
                condition=models.Q(org__isnull=False),
                name='uq_proposal_component_rule_org_code',
            ),
            models.UniqueConstraint(
                fields=['code'],
                condition=models.Q(org__isnull=True),
                name='uq_proposal_component_rule_global_code',
            ),
        ]
        indexes = [
            models.Index(fields=['org', 'is_active']),
            models.Index(fields=['org', 'code']),
        ]

    def __str__(self):
        scope = self.org.code if self.org_id else 'GLOBAL'
        return f"[{scope}] {self.code} ({self.calculation_type})"

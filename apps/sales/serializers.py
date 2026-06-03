"""
apps/sales/serializers.py

Read/write serializers for all sales app models.
"""

from rest_framework import serializers

from .models import (
    SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement,
    ProposalVersion, ProposalBudgetLine, ProposalBreakupLine, ClientProposalResponse,
    SalesLeadActivity, SalesDocument,
    SiteSurveyScopeAnswer, SiteSurveyShiftDeployment, SiteSurveyLocationLine,
    SiteSurveyEquipmentLine, SiteSurveyIssueLine, SurveyRoleMapping,
    ProposalComponentRule,
)

_ALLOWED_CONTENT_TYPES = {
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'image/jpeg',
    'image/png',
}
_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _user_summary(user):
    if user is None:
        return None
    return {'id': user.pk, 'username': user.username}


# ─── SalesLeadSite ────────────────────────────────────────────────────────────

class SalesLeadSiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesLeadSite
        fields = [
            'id', 'lead', 'site_name', 'site_address', 'city', 'state',
            'location_area', 'remarks', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SalesLeadSiteWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesLeadSite
        fields = [
            'id', 'lead', 'site_name', 'site_address', 'city', 'state',
            'location_area', 'remarks', 'is_active',
        ]
        read_only_fields = ['id']


# ─── SiteSurvey ───────────────────────────────────────────────────────────────

class SiteSurveySerializer(serializers.ModelSerializer):
    lead_client_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    survey_done_by_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()

    class Meta:
        model = SiteSurvey
        fields = [
            'id', 'lead', 'lead_client_name', 'site', 'site_name',
            'assigned_to', 'assigned_to_name', 'assigned_at',
            'started_at', 'completed_at', 'due_date',
            'survey_done_by', 'survey_done_by_name',
            'survey_date', 'business_volume', 'service_type',
            'feasibility_status', 'survey_remarks', 'survey_notes', 'status',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'lead_client_name', 'site_name',
            'survey_done_by_name', 'assigned_to_name',
            'assigned_at', 'started_at', 'completed_at',
            'created_at', 'updated_at',
        ]

    def get_lead_client_name(self, obj):
        return obj.lead.client_name if obj.lead_id else None

    def get_site_name(self, obj):
        return obj.site.site_name if obj.site_id else None

    def get_survey_done_by_name(self, obj):
        return obj.survey_done_by.username if obj.survey_done_by_id else None

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.username if obj.assigned_to_id else None


class SiteSurveyWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSurvey
        fields = [
            'id', 'lead', 'site', 'survey_done_by',
            'survey_date', 'due_date', 'business_volume', 'service_type',
            'feasibility_status', 'survey_remarks', 'survey_notes', 'status',
        ]
        read_only_fields = ['id']


# ─── SalesRoleRequirement ─────────────────────────────────────────────────────

class SalesRoleRequirementSerializer(serializers.ModelSerializer):
    job_role_name = serializers.SerializerMethodField()
    wage_category_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = SalesRoleRequirement
        fields = [
            'id', 'lead', 'site', 'site_name', 'survey',
            'job_role', 'job_role_name', 'wage_category', 'wage_category_name',
            'service_category', 'manpower_count', 'shift_hours', 'working_days',
            'remarks', 'is_active',
            'created_from_survey', 'approved_by_operations', 'approved_at',
            'approved_by', 'approved_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'job_role_name', 'wage_category_name', 'site_name', 'approved_by_name',
            'approved_by_operations', 'approved_at', 'approved_by',
            'created_at', 'updated_at',
        ]

    def get_job_role_name(self, obj):
        return obj.job_role.name if obj.job_role_id else None

    def get_wage_category_name(self, obj):
        return obj.wage_category.name if obj.wage_category_id else None

    def get_site_name(self, obj):
        return obj.site.site_name if obj.site_id else None

    def get_approved_by_name(self, obj):
        return obj.approved_by.username if obj.approved_by_id else None


class SalesRoleRequirementWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesRoleRequirement
        fields = [
            'id', 'lead', 'site', 'survey',
            'job_role', 'wage_category',
            'service_category', 'manpower_count', 'shift_hours', 'working_days',
            'remarks', 'is_active', 'created_from_survey',
        ]
        read_only_fields = ['id']


# ─── ProposalBudgetLine ───────────────────────────────────────────────────────

class ProposalBudgetLineSerializer(serializers.ModelSerializer):
    job_role_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    overridden_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProposalBudgetLine
        fields = [
            'id', 'proposal_version', 'site', 'site_name', 'role_requirement',
            'service_category', 'job_role', 'job_role_name',
            'description', 'manpower_count', 'unit_cost', 'total_cost',
            'remarks', 'sort_order',
            'is_manual_override', 'override_reason',
            'overridden_by', 'overridden_by_name', 'overridden_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'job_role_name', 'site_name', 'overridden_by_name',
            'created_at', 'updated_at',
        ]

    def get_job_role_name(self, obj):
        return obj.job_role.name if obj.job_role_id else None

    def get_site_name(self, obj):
        return obj.site.site_name if obj.site_id else None

    def get_overridden_by_name(self, obj):
        return obj.overridden_by.username if obj.overridden_by_id else None


class ProposalBudgetLineWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalBudgetLine
        fields = [
            'id', 'proposal_version', 'site', 'role_requirement',
            'service_category', 'job_role',
            'description', 'manpower_count', 'unit_cost', 'total_cost',
            'remarks', 'sort_order',
            'is_manual_override', 'override_reason', 'overridden_by', 'overridden_at',
        ]
        read_only_fields = ['id']


# ─── ProposalBreakupLine ──────────────────────────────────────────────────────

class ProposalBreakupLineSerializer(serializers.ModelSerializer):
    job_role_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    overridden_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProposalBreakupLine
        fields = [
            'id', 'proposal_version', 'site', 'site_name', 'role_requirement',
            'job_role', 'job_role_name',
            'component_name', 'component_type', 'percentage', 'amount',
            'remarks', 'sort_order',
            'is_manual_override', 'override_reason',
            'overridden_by', 'overridden_by_name', 'overridden_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'job_role_name', 'site_name', 'overridden_by_name',
            'created_at', 'updated_at',
        ]

    def get_job_role_name(self, obj):
        return obj.job_role.name if obj.job_role_id else None

    def get_site_name(self, obj):
        return obj.site.site_name if obj.site_id else None

    def get_overridden_by_name(self, obj):
        return obj.overridden_by.username if obj.overridden_by_id else None


class ProposalBreakupLineWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalBreakupLine
        fields = [
            'id', 'proposal_version', 'site', 'role_requirement',
            'job_role', 'component_name', 'component_type',
            'percentage', 'amount', 'remarks', 'sort_order',
            'is_manual_override', 'override_reason', 'overridden_by', 'overridden_at',
        ]
        read_only_fields = ['id']


# ─── ClientProposalResponse ───────────────────────────────────────────────────

class ClientProposalResponseSerializer(serializers.ModelSerializer):
    sent_to_client_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ClientProposalResponse
        fields = [
            'id', 'lead', 'proposal_version',
            'sent_to_client_by', 'sent_to_client_by_name', 'sent_to_client_at',
            'client_response', 'client_remarks', 'responded_at',
            'responded_by_name', 'responded_by_email',
            'next_action_due_date', 'meeting_notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'sent_to_client_by_name', 'created_at', 'updated_at']

    def get_sent_to_client_by_name(self, obj):
        return obj.sent_to_client_by.username if obj.sent_to_client_by_id else None


class ClientProposalResponseWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientProposalResponse
        fields = [
            'id', 'lead', 'proposal_version',
            'sent_to_client_by', 'sent_to_client_at',
            'client_response', 'client_remarks', 'responded_at',
            'responded_by_name', 'responded_by_email',
            'next_action_due_date', 'meeting_notes',
        ]
        read_only_fields = ['id']


# ─── ProposalVersion ──────────────────────────────────────────────────────────

class ProposalVersionSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProposalVersion
        fields = [
            'id', 'lead', 'version_number',
            'created_by', 'created_by_name', 'source_version',
            'grand_total', 'subtotal_amount', 'management_fee_amount', 'gst_amount',
            'manpower_total', 'management_fee_percent', 'gst_applicable',
            'status', 'internal_approval_status', 'client_approval_status',
            'client_remarks', 'sales_remarks',
            'is_final_approved_version', 'sent_to_client_at', 'locked_at',
            'submitted_internal_at', 'internally_approved_at', 'client_approved_at',
            'expires_at', 'validity_days',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'created_by_name', 'is_final_approved_version',
            'sent_to_client_at', 'locked_at',
            'submitted_internal_at', 'internally_approved_at', 'client_approved_at',
            'created_at', 'updated_at',
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.username if obj.created_by_id else None


class ProposalVersionDetailSerializer(ProposalVersionSerializer):
    """ProposalVersion with all nested lines and client responses."""
    budget_lines = ProposalBudgetLineSerializer(many=True, read_only=True)
    breakup_lines = ProposalBreakupLineSerializer(many=True, read_only=True)
    client_responses = ClientProposalResponseSerializer(many=True, read_only=True)

    class Meta(ProposalVersionSerializer.Meta):
        fields = ProposalVersionSerializer.Meta.fields + [
            'budget_lines', 'breakup_lines', 'client_responses',
        ]


class ProposalVersionWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalVersion
        fields = [
            'id', 'lead', 'version_number',
            'grand_total', 'subtotal_amount', 'management_fee_amount', 'gst_amount',
            'manpower_total', 'management_fee_percent', 'gst_applicable',
            'status', 'sales_remarks', 'client_remarks',
        ]
        read_only_fields = ['id']


# ─── SalesLead ────────────────────────────────────────────────────────────────

class SalesLeadSerializer(serializers.ModelSerializer):
    sales_person_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    final_approved_proposal_summary = serializers.SerializerMethodField()
    submitted_to_operations_by_name = serializers.SerializerMethodField()
    operations_owner_name = serializers.SerializerMethodField()
    existing_client_name = serializers.SerializerMethodField()
    existing_client_code = serializers.SerializerMethodField()

    class Meta:
        model = SalesLead
        fields = [
            'id', 'org',
            'lead_type', 'existing_client', 'existing_client_name', 'existing_client_code',
            'client_name', 'client_contact_person', 'client_email', 'client_phone',
            'sales_person', 'sales_person_name',
            'created_by', 'created_by_name',
            'current_stage', 'current_status',
            'lead_source', 'industry', 'priority',
            'expected_start_date', 'expected_contract_months', 'estimated_monthly_value',
            'submitted_to_operations_at',
            'submitted_to_operations_by', 'submitted_to_operations_by_name',
            'operations_owner', 'operations_owner_name',
            'rfp_required', 'rfq_required',
            'requirement_details', 'initial_business_requirement', 'sales_remarks',
            'final_approved_proposal', 'final_approved_proposal_summary',
            'converted_on', 'source_onboarding_request',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'org', 'created_by', 'sales_person_name', 'created_by_name',
            'existing_client_name', 'existing_client_code',
            'final_approved_proposal', 'final_approved_proposal_summary',
            'submitted_to_operations_at', 'submitted_to_operations_by',
            'submitted_to_operations_by_name', 'operations_owner_name',
            'converted_on', 'created_at', 'updated_at',
        ]

    def get_sales_person_name(self, obj):
        return obj.sales_person.username if obj.sales_person_id else None

    def get_created_by_name(self, obj):
        return obj.created_by.username if obj.created_by_id else None

    def get_final_approved_proposal_summary(self, obj):
        p = obj.final_approved_proposal
        if not p:
            return None
        return {'id': p.pk, 'version_number': p.version_number, 'status': p.status}

    def get_submitted_to_operations_by_name(self, obj):
        return obj.submitted_to_operations_by.username if obj.submitted_to_operations_by_id else None

    def get_operations_owner_name(self, obj):
        return obj.operations_owner.username if obj.operations_owner_id else None

    def get_existing_client_name(self, obj):
        return obj.existing_client.name if obj.existing_client_id else None

    def get_existing_client_code(self, obj):
        return obj.existing_client.code if obj.existing_client_id else None


class SalesLeadDetailSerializer(SalesLeadSerializer):
    """SalesLead with all nested resources."""
    sites = SalesLeadSiteSerializer(many=True, read_only=True)
    surveys = SiteSurveySerializer(many=True, read_only=True)
    role_requirements = SalesRoleRequirementSerializer(many=True, read_only=True)
    proposal_versions = ProposalVersionSerializer(many=True, read_only=True)
    recent_activities = serializers.SerializerMethodField()
    documents_count = serializers.SerializerMethodField()

    class Meta(SalesLeadSerializer.Meta):
        fields = SalesLeadSerializer.Meta.fields + [
            'sites', 'surveys', 'role_requirements', 'proposal_versions',
            'recent_activities', 'documents_count',
        ]

    def get_recent_activities(self, obj):
        qs = obj.activities.order_by('-created_at')[:10]
        return SalesLeadActivitySerializer(qs, many=True, context=self.context).data

    def get_documents_count(self, obj):
        return obj.documents.filter(is_active=True).count()


# ─── SalesLeadActivity ────────────────────────────────────────────────────────

class SalesLeadActivitySerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True, default=None)

    class Meta:
        model = SalesLeadActivity
        fields = [
            'id', 'org', 'lead', 'proposal_version', 'site',
            'activity_type', 'title', 'message',
            'actor', 'actor_username',
            'metadata', 'created_at',
        ]
        read_only_fields = fields


# ─── SalesDocument ────────────────────────────────────────────────────────────

class SalesDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_username = serializers.CharField(source='uploaded_by.username', read_only=True, default=None)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = SalesDocument
        fields = [
            'id', 'org', 'lead', 'proposal_version', 'site',
            'uploaded_by', 'uploaded_by_username',
            'document_type', 'title',
            'file_url', 'file_name', 'file_size', 'content_type',
            'notes', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_file_url(self, obj):
        request = self.context.get('request')
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url if obj.file else None


class SalesDocumentWriteSerializer(serializers.ModelSerializer):
    file = serializers.FileField()

    class Meta:
        model = SalesDocument
        fields = ['lead', 'proposal_version', 'site', 'document_type', 'title', 'file', 'notes']

    def validate(self, data):
        file = data.get('file')
        lead = data.get('lead')
        proposal_version = data.get('proposal_version')
        site = data.get('site')

        if file:
            if file.size > _MAX_FILE_SIZE_BYTES:
                raise serializers.ValidationError({'file': 'File size must not exceed 20 MB.'})
            ct = getattr(file, 'content_type', '')
            if ct not in _ALLOWED_CONTENT_TYPES:
                raise serializers.ValidationError(
                    {'file': f'File type "{ct}" is not permitted. Allowed: pdf, doc/docx, xls/xlsx, jpg, png.'}
                )

        if proposal_version and lead and proposal_version.lead_id != lead.pk:
            raise serializers.ValidationError(
                {'proposal_version': 'Proposal version does not belong to this lead.'}
            )
        if site and lead and site.lead_id != lead.pk:
            raise serializers.ValidationError(
                {'site': 'Site does not belong to this lead.'}
            )

        return data

    def create(self, validated_data):
        file = validated_data['file']
        validated_data['file_name'] = file.name
        validated_data['file_size'] = file.size
        validated_data['content_type'] = getattr(file, 'content_type', 'application/octet-stream')
        validated_data['org'] = validated_data['lead'].org
        validated_data['uploaded_by'] = self.context['request'].user
        instance = super().create(validated_data)
        from apps.sales.activity import log_sales_activity
        log_sales_activity(
            lead=instance.lead,
            activity_type='document_uploaded',
            title=f'Document uploaded: {instance.title}',
            message=f'{instance.document_type}: {instance.file_name}',
            actor=instance.uploaded_by,
            proposal_version=instance.proposal_version,
            site=instance.site,
            metadata={'document_id': instance.pk, 'document_type': instance.document_type},
        )
        return instance


class SalesLeadWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesLead
        fields = [
            'id',
            'lead_type', 'existing_client',
            'client_name', 'client_contact_person', 'client_email', 'client_phone',
            'sales_person', 'operations_owner',
            'lead_source', 'industry', 'priority',
            'expected_start_date', 'expected_contract_months', 'estimated_monthly_value',
            'rfp_required', 'rfq_required',
            'requirement_details', 'initial_business_requirement', 'sales_remarks',
            'source_onboarding_request',
        ]
        read_only_fields = ['id']
        extra_kwargs = {
            'client_name': {'required': False, 'allow_blank': True},
        }

    def validate(self, data):
        lead_type = data.get('lead_type') or (self.instance.lead_type if self.instance else 'new_client')
        existing_client = data.get('existing_client') or (
            self.instance.existing_client if self.instance else None
        )

        if lead_type == 'new_client' and existing_client is not None:
            raise serializers.ValidationError(
                {'existing_client': 'new_client leads must not have an existing_client.'}
            )
        if lead_type in ('site_expansion', 'scope_expansion', 'renewal') and existing_client is None:
            raise serializers.ValidationError(
                {'existing_client': f'{lead_type} leads require existing_client to be set.'}
            )

        # existing_client must belong to the same org as the lead
        if existing_client is not None:
            request = self.context.get('request')
            if request and hasattr(request, 'user') and request.user.org_id:
                if existing_client.org_id != request.user.org_id:
                    raise serializers.ValidationError(
                        {'existing_client': 'existing_client must belong to the same org.'}
                    )

        # Auto-copy client_name from existing_client when blank
        if lead_type != 'new_client' and existing_client is not None:
            client_name = data.get('client_name', '')
            if not client_name and (self.instance is None or not self.instance.client_name):
                data['client_name'] = existing_client.name

        # Ensure client_name is not blank after auto-fill
        client_name_final = data.get('client_name') or (
            self.instance.client_name if self.instance else ''
        )
        if not client_name_final:
            raise serializers.ValidationError({'client_name': 'client_name is required.'})

        return data


# ─── Phase H: Survey Excel structure ──────────────────────────────────────────

class SiteSurveyScopeAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSurveyScopeAnswer
        fields = [
            'id', 'survey', 'category', 'field_key', 'field_label',
            'value_text', 'sort_order', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SiteSurveyShiftDeploymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSurveyShiftDeployment
        fields = [
            'id', 'survey', 'description',
            'general_count', 'first_shift_count', 'second_shift_count', 'total_count',
            'remarks', 'is_applicable', 'not_applicable_reason',
            'line_type', 'sort_order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SiteSurveyLocationLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSurveyLocationLine
        fields = [
            'id', 'survey', 'location_name',
            'present_count', 'proposed_count',
            'remarks', 'is_applicable', 'not_applicable_reason',
            'line_type', 'sort_order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SiteSurveyEquipmentLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSurveyEquipmentLine
        fields = [
            'id', 'survey', 'equipment_category', 'description',
            'unit_count', 'amount', 'total',
            'is_applicable', 'not_applicable_reason',
            'line_type', 'amortisation_months', 'sort_order',
            'created_at', 'updated_at',
        ]
        # `total` is auto-computed for 'item' rows; writes are accepted but the
        # save() method will recompute item rows.
        read_only_fields = ['id', 'created_at', 'updated_at']


class SiteSurveyIssueLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSurveyIssueLine
        fields = [
            'id', 'survey', 'issue', 'improvement_details',
            'is_applicable', 'not_applicable_reason', 'sort_order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SurveyRoleMappingSerializer(serializers.ModelSerializer):
    job_role_name = serializers.SerializerMethodField()
    wage_category_name = serializers.SerializerMethodField()

    class Meta:
        model = SurveyRoleMapping
        fields = [
            'id', 'org', 'description_text',
            'job_role', 'job_role_name',
            'wage_category', 'wage_category_name',
            'service_category', 'shift_hours', 'working_days',
            'is_active', 'remarks',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'job_role_name', 'wage_category_name', 'created_at', 'updated_at']

    def get_job_role_name(self, obj):
        return obj.job_role.name if obj.job_role_id else None

    def get_wage_category_name(self, obj):
        return obj.wage_category.name if obj.wage_category_id else None


class SiteSurveyStructuredSerializer(serializers.Serializer):
    """Read-only grouped payload for GET site-surveys/{id}/structured/."""

    survey = SiteSurveySerializer(read_only=True)
    scope_answers = SiteSurveyScopeAnswerSerializer(many=True, read_only=True)
    shift_deployments = SiteSurveyShiftDeploymentSerializer(many=True, read_only=True)
    location_lines = SiteSurveyLocationLineSerializer(many=True, read_only=True)
    equipment_lines = SiteSurveyEquipmentLineSerializer(many=True, read_only=True)
    issue_lines = SiteSurveyIssueLineSerializer(many=True, read_only=True)


# ─── Phase H: Proposal component rules ────────────────────────────────────────

class ProposalComponentRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalComponentRule
        fields = [
            'id', 'org', 'code', 'component_name', 'component_type',
            'calculation_type', 'percentage', 'fixed_amount', 'base_component_code',
            'sort_order', 'is_active', 'effective_from', 'effective_to', 'remarks',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, data):
        calc = data.get('calculation_type') or (
            self.instance.calculation_type if self.instance else None
        )
        percentage = data.get('percentage') if 'percentage' in data else (
            self.instance.percentage if self.instance else None
        )
        fixed_amount = data.get('fixed_amount') if 'fixed_amount' in data else (
            self.instance.fixed_amount if self.instance else None
        )
        base_component_code = data.get('base_component_code') if 'base_component_code' in data else (
            self.instance.base_component_code if self.instance else ''
        )

        if calc in ('percent_of_basic', 'percent_of_gross'):
            if percentage is None:
                raise serializers.ValidationError(
                    {'percentage': f'`percentage` is required when calculation_type is {calc}.'}
                )
            if fixed_amount is not None:
                raise serializers.ValidationError(
                    {'fixed_amount': f'`fixed_amount` must be empty when calculation_type is {calc}.'}
                )
            if base_component_code:
                raise serializers.ValidationError(
                    {'base_component_code': f'`base_component_code` must be empty when calculation_type is {calc}.'}
                )
        elif calc == 'percent_of_other':
            if percentage is None:
                raise serializers.ValidationError(
                    {'percentage': '`percentage` is required when calculation_type is percent_of_other.'}
                )
            if not base_component_code:
                raise serializers.ValidationError(
                    {'base_component_code': '`base_component_code` is required for percent_of_other.'}
                )
            if fixed_amount is not None:
                raise serializers.ValidationError(
                    {'fixed_amount': '`fixed_amount` must be empty for percent_of_other.'}
                )
        elif calc == 'fixed':
            if fixed_amount is None:
                raise serializers.ValidationError(
                    {'fixed_amount': '`fixed_amount` is required when calculation_type is fixed.'}
                )
            if percentage is not None:
                raise serializers.ValidationError(
                    {'percentage': '`percentage` must be empty when calculation_type is fixed.'}
                )
            if base_component_code:
                raise serializers.ValidationError(
                    {'base_component_code': '`base_component_code` must be empty when calculation_type is fixed.'}
                )
        return data

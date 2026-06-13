"""
apps/talent/serializers.py

Phase Talent-Hiring-B: read/write serializers for Candidate and Resume.
"""

import json

from rest_framework import serializers

from apps.hiring.models import PipelineStage
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem

from .models import (
    Candidate, Resume, CandidateSkill,
    ParsedResume, CandidateExperience, CandidateEducation,
    TalentResumeReview, ResumeImportBatch, ResumeImportItem,
)


# ─── CandidateSkill ───────────────────────────────────────────────────────────

class CandidateSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateSkill
        fields = [
            'id', 'skill_name', 'normalized_skill_name', 'confidence',
            'years_experience', 'proficiency', 'source',
        ]


# ─── Candidate ────────────────────────────────────────────────────────────────

class CandidateSerializer(serializers.ModelSerializer):
    """Read serializer — returned for all list/retrieve/create/update responses."""
    full_name = serializers.CharField(read_only=True)
    target_job_role_name = serializers.CharField(source='target_job_role.name', read_only=True)
    target_job_role_code = serializers.CharField(source='target_job_role.code', read_only=True)
    skills_count = serializers.SerializerMethodField()
    resume_count = serializers.SerializerMethodField()
    latest_resume_status = serializers.SerializerMethodField()
    latest_document_type = serializers.SerializerMethodField()
    latest_source_type = serializers.SerializerMethodField()
    active_application_count = serializers.SerializerMethodField()
    profile_quality = serializers.SerializerMethodField()
    profile_quality_score = serializers.SerializerMethodField()
    journey_status = serializers.SerializerMethodField()
    journey_status_label = serializers.SerializerMethodField()
    latest_application_id = serializers.SerializerMethodField()
    latest_application_status = serializers.SerializerMethodField()
    latest_offer_status = serializers.SerializerMethodField()
    employee_id = serializers.SerializerMethodField()
    employee_status = serializers.SerializerMethodField()
    deployment_id = serializers.SerializerMethodField()
    deployment_status = serializers.SerializerMethodField()

    class Meta:
        model = Candidate
        fields = [
            'id', 'org', 'phone', 'phone_normalized',
            'first_name', 'middle_name', 'last_name', 'full_name',
            'email', 'current_location',
            'total_experience_years', 'current_ctc', 'expected_ctc',
            'source', 'is_blacklisted', 'blacklist_reason',
            'lifecycle_status', 'availability_status',
            'preferred_location', 'notice_period_days',
            'current_company', 'current_role',
            'target_job_role', 'target_job_role_name', 'target_job_role_code',
            'source_reference',
            'is_duplicate', 'duplicate_of', 'do_not_contact',
            'skills_count', 'resume_count',
            'latest_resume_status', 'latest_document_type', 'latest_source_type',
            'active_application_count',
            'profile_quality', 'profile_quality_score',
            'journey_status', 'journey_status_label',
            'latest_application_id', 'latest_application_status',
            'latest_offer_status',
            'employee_id', 'employee_status',
            'deployment_id', 'deployment_status',
            'created_at', 'updated_at',
        ]
        read_only_fields = [f for f in fields if f != 'id']

    def _journey(self, obj):
        cached = getattr(obj, '_candidate_journey_status', None)
        if cached is None:
            from .services import candidate_journey_status
            cached = candidate_journey_status(obj)
            setattr(obj, '_candidate_journey_status', cached)
        return cached

    def get_skills_count(self, obj):
        return obj.skills.count()

    def get_resume_count(self, obj):
        return obj.resumes.count()

    def get_latest_resume_status(self, obj):
        r = obj.resumes.order_by('-uploaded_at').values('status').first()
        return r['status'] if r else None

    def get_latest_document_type(self, obj):
        r = obj.resumes.order_by('-uploaded_at').values('document_type').first()
        if r:
            return r['document_type']
        item = (
            obj.resume_import_items
            .select_related('batch')
            .order_by('-created_at')
            .first()
        )
        return item.document_type if item else None

    def get_latest_source_type(self, obj):
        r = obj.resumes.order_by('-uploaded_at').values('source_type').first()
        if r:
            return r['source_type']
        item = (
            obj.resume_import_items
            .select_related('batch')
            .order_by('-created_at')
            .first()
        )
        return item.batch.source_type if item else None

    def get_active_application_count(self, obj):
        return obj.hiring_applications.exclude(
            status__in=['rejected', 'cancelled']
        ).count()

    def get_profile_quality(self, obj):
        from .services import candidate_profile_quality
        return candidate_profile_quality(obj)

    def get_profile_quality_score(self, obj):
        return self.get_profile_quality(obj)['score']

    def get_journey_status(self, obj):
        return self._journey(obj)['journey_status']

    def get_journey_status_label(self, obj):
        return self._journey(obj)['journey_status_label']

    def get_latest_application_id(self, obj):
        return self._journey(obj)['latest_application_id']

    def get_latest_application_status(self, obj):
        return self._journey(obj)['latest_application_status']

    def get_latest_offer_status(self, obj):
        return self._journey(obj)['latest_offer_status']

    def get_employee_id(self, obj):
        return self._journey(obj)['employee_id']

    def get_employee_status(self, obj):
        return self._journey(obj)['employee_status']

    def get_deployment_id(self, obj):
        return self._journey(obj)['deployment_id']

    def get_deployment_status(self, obj):
        return self._journey(obj)['deployment_status']


class CandidateWriteSerializer(serializers.ModelSerializer):
    """Write serializer — safe writable fields only."""

    class Meta:
        model = Candidate
        fields = [
            'phone', 'first_name', 'middle_name', 'last_name',
            'email', 'current_location',
            'total_experience_years', 'current_ctc', 'expected_ctc',
            'source', 'lifecycle_status', 'availability_status',
            'preferred_location', 'notice_period_days',
            'current_company', 'current_role',
            'target_job_role', 'source_reference',
        ]
        extra_kwargs = {
            'phone': {'required': True},
            'first_name': {'required': True},
            'last_name': {'required': True},
        }

    def validate_phone(self, value):
        from .services import normalize_phone
        # Validate format; normalized value stored via perform_create/update
        normalize_phone(value)
        return value

    def validate_target_job_role(self, value):
        if value is None:
            return value
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        org_id = getattr(user, 'org_id', None)
        if org_id and value.org_id != org_id:
            raise serializers.ValidationError(
                'Target job role does not belong to your organization.'
            )
        return value


# ─── Resume ───────────────────────────────────────────────────────────────────

class ResumeSerializer(serializers.ModelSerializer):
    """Read serializer for resumes."""
    target_job_role_name = serializers.CharField(source='target_job_role.name', read_only=True)
    target_job_role_code = serializers.CharField(source='target_job_role.code', read_only=True)
    candidate_full_name = serializers.CharField(source='candidate.full_name', read_only=True)
    candidate_phone = serializers.CharField(source='candidate.phone', read_only=True)

    class Meta:
        model = Resume
        fields = [
            'id', 'candidate', 'file', 'original_filename', 'content_type',
            'candidate_full_name', 'candidate_phone',
            'size_bytes', 'parsed_status', 'uploaded_at', 'view_only_note',
            'status', 'file_hash', 'document_type', 'source_type',
            'target_job_role', 'target_job_role_name', 'target_job_role_code',
            'target_role_source', 'import_batch_id',
            'ocr_used', 'extraction_engine', 'extraction_confidence',
            'parser_engine', 'parser_confidence',
            'error_message', 'manual_review_reason',
            'source_intake_document', 'uploaded_by',
        ]
        read_only_fields = [
            'uploaded_at', 'file_hash', 'status', 'original_filename',
            'content_type', 'size_bytes',
        ]


class ResumeWriteSerializer(serializers.ModelSerializer):
    """Write serializer for resume upload (Mode A: upload for existing candidate)."""

    class Meta:
        model = Resume
        fields = [
            'candidate', 'file', 'source_type', 'view_only_note',
            'target_job_role', 'target_role_source',
        ]
        extra_kwargs = {
            'source_type': {'required': False},
            'view_only_note': {'required': False},
            'target_job_role': {'required': False, 'allow_null': True},
            'target_role_source': {'required': False, 'allow_blank': True},
        }

    def validate_source_type(self, value):
        allowed = {
            'manual_upload', 'recruiter_upload', 'portal', 'referral',
            'bulk_upload', 'campaign', 'qr_intake',
        }
        if value not in allowed:
            raise serializers.ValidationError(
                f"source_type must be one of: {', '.join(sorted(allowed))}."
            )
        return value

    def validate_target_job_role(self, value):
        if value is None:
            return value
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        org_id = getattr(user, 'org_id', None)
        if org_id and value.org_id != org_id:
            raise serializers.ValidationError(
                'Target job role does not belong to your organization.'
            )
        return value


class ResumeBulkUploadSerializer(serializers.Serializer):
    """Input for POST /api/talent/resumes/bulk-upload/."""

    files = serializers.ListField(child=serializers.FileField(), allow_empty=False)
    target_job_role = serializers.PrimaryKeyRelatedField(queryset=JobRole.objects.all())
    source_type = serializers.ChoiceField(
        choices=['bulk_upload', 'recruiter_upload', 'manual_upload', 'campaign'],
        default='bulk_upload',
        required=False,
    )
    view_only_note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default='',
    )

    def validate_target_job_role(self, value):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        org_id = getattr(user, 'org_id', None)
        if org_id and value.org_id != org_id:
            raise serializers.ValidationError(
                'Target job role does not belong to your organization.'
            )
        return value


class ResumeExcelImportSerializer(serializers.Serializer):
    """Input for POST /api/talent/resumes/excel-import/."""

    file = serializers.FileField()
    target_job_role = serializers.PrimaryKeyRelatedField(
        queryset=JobRole.objects.all(), required=False, allow_null=True,
    )
    source_type = serializers.ChoiceField(
        choices=['excel_import', 'bulk_upload', 'campaign'],
        default='excel_import',
        required=False,
    )

    def validate_target_job_role(self, value):
        if value is None:
            return value
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        org_id = getattr(user, 'org_id', None)
        if org_id and value.org_id != org_id:
            raise serializers.ValidationError(
                'Target job role does not belong to your organization.'
            )
        return value


class ResumeImportItemSerializer(serializers.ModelSerializer):
    candidate_name = serializers.CharField(source='candidate.full_name', read_only=True)
    candidate_phone = serializers.CharField(source='candidate.phone', read_only=True)

    class Meta:
        model = ResumeImportItem
        fields = [
            'id', 'original_filename', 'content_type', 'size_bytes',
            'document_type', 'row_number',
            'status', 'error_message', 'candidate', 'candidate_name',
            'candidate_phone', 'resume', 'processed_at', 'created_at',
        ]


class ResumeImportBatchSerializer(serializers.ModelSerializer):
    target_job_role_name = serializers.CharField(source='target_job_role.name', read_only=True)
    target_job_role_code = serializers.CharField(source='target_job_role.code', read_only=True)
    items = ResumeImportItemSerializer(many=True, read_only=True)

    class Meta:
        model = ResumeImportBatch
        fields = [
            'id', 'org', 'target_job_role', 'target_job_role_name',
            'target_job_role_code', 'source_type', 'status',
            'import_file', 'original_filename', 'content_type',
            'size_bytes', 'document_type',
            'total_count', 'processed_count', 'success_count',
            'duplicate_count', 'failed_count', 'manual_review_count',
            'view_only_note', 'created_by', 'created_at', 'updated_at',
            'items',
        ]


class ResumePatchSerializer(serializers.ModelSerializer):
    """Write serializer for manual-review PATCH — only status/reason updatable."""

    class Meta:
        model = Resume
        fields = ['manual_review_reason', 'view_only_note']


# ─── ParsedResume / Experience / Education ────────────────────────────────────

class ParsedResumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParsedResume
        fields = [
            'id', 'resume', 'parsed_json', 'normalized_json', 'summary',
            'career_level', 'primary_domain', 'validation_errors',
            'missing_fields', 'confidence', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class CandidateExperienceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateExperience
        fields = [
            'id', 'candidate', 'job_title', 'normalized_title',
            'company_name', 'industry', 'start_date', 'end_date',
            'is_current', 'duration_months', 'description',
            'responsibilities', 'confidence', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class CandidateEducationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateEducation
        fields = [
            'id', 'candidate', 'degree', 'normalized_degree',
            'specialization', 'institute', 'start_year', 'end_year',
            'confidence', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


# ─── ManualResumeIntake ───────────────────────────────────────────────────────

class ManualResumeIntakeSerializer(serializers.Serializer):
    """Input for POST /api/talent/manual-resume-intake/"""

    # ── Candidate ─────────────────────────────────────────────────────────────
    first_name = serializers.CharField(max_length=128)
    middle_name = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    current_role = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    current_location = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    total_experience_years = serializers.DecimalField(
        max_digits=5, decimal_places=1, required=False, allow_null=True, default=None,
    )
    preferred_location = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    notice_period_days = serializers.IntegerField(min_value=0, required=False, allow_null=True, default=None)
    current_company = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    expected_ctc = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, default=None,
    )
    current_ctc = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, default=None,
    )

    # ── Resume ────────────────────────────────────────────────────────────────
    resume_file = serializers.FileField()
    view_only_note = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')

    # ── Skills ────────────────────────────────────────────────────────────────
    skills = serializers.CharField(required=False, allow_blank=True, default='')

    # ── Optional hiring link ──────────────────────────────────────────────────
    mrf = serializers.PrimaryKeyRelatedField(
        queryset=ManpowerRequest.objects.all(), required=False, allow_null=True, default=None,
    )
    mrf_line_item = serializers.PrimaryKeyRelatedField(
        queryset=MRFLineItem.objects.all(), required=False, allow_null=True, default=None,
    )
    current_stage = serializers.PrimaryKeyRelatedField(
        queryset=PipelineStage.objects.all(), required=False, allow_null=True, default=None,
    )

    def validate_phone(self, value):
        from .services import normalize_phone
        normalize_phone(value)
        return value

    def validate_skills(self, value):
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if str(s).strip()]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return [s.strip() for s in value.split(',') if s.strip()]

    def validate(self, data):
        mrf_li = data.get('mrf_line_item')
        mrf = data.get('mrf')

        if mrf_li:
            if not mrf:
                data['mrf'] = mrf_li.mrf
                mrf = mrf_li.mrf

            if mrf_li.mrf_id != mrf.pk:
                raise serializers.ValidationError(
                    {'mrf_line_item': 'Line item does not belong to the specified MRF.'}
                )

            if mrf.status != 'approved':
                raise serializers.ValidationError(
                    {'mrf': 'MRF must be in approved status.'}
                )

        return data


# ─── Review Queue ─────────────────────────────────────────────────────────────

class _CandidateQueueSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Candidate
        fields = ['id', 'full_name', 'phone', 'email', 'lifecycle_status']


class _ParsedResumeSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ParsedResume
        fields = ['id', 'confidence', 'career_level', 'primary_domain', 'validation_errors', 'missing_fields']


class ResumeReviewQueueSerializer(serializers.ModelSerializer):
    candidate_summary = _CandidateQueueSummarySerializer(source='candidate', read_only=True)
    parsed_resume_summary = serializers.SerializerMethodField()

    class Meta:
        model = Resume
        fields = [
            'id', 'original_filename', 'status', 'manual_review_reason', 'error_message',
            'parser_engine', 'parser_confidence', 'extraction_engine', 'extraction_confidence',
            'uploaded_at', 'source_type', 'document_type', 'uploaded_by',
            'candidate_summary', 'parsed_resume_summary',
        ]

    def get_parsed_resume_summary(self, obj):
        try:
            return _ParsedResumeSummarySerializer(obj.parsed_resume).data
        except ParsedResume.DoesNotExist:
            return None


# ─── Review Detail ────────────────────────────────────────────────────────────

class ResumeReviewDetailSerializer(serializers.ModelSerializer):
    candidate = CandidateSerializer(read_only=True)
    parsed_resume = ParsedResumeSerializer(read_only=True)
    parsed_skills = serializers.SerializerMethodField()
    parsed_experience = serializers.SerializerMethodField()
    parsed_education = serializers.SerializerMethodField()

    class Meta:
        model = Resume
        fields = [
            'id', 'original_filename', 'content_type', 'size_bytes',
            'status', 'manual_review_reason', 'error_message', 'document_type',
            'parser_engine', 'parser_confidence', 'extraction_engine', 'extraction_confidence',
            'raw_text', 'cleaned_text', 'uploaded_at', 'source_type',
            'candidate', 'parsed_resume', 'parsed_skills', 'parsed_experience', 'parsed_education',
        ]

    def get_parsed_skills(self, obj):
        skills = obj.skills.filter(source__in=['parsed', 'reviewed'])
        return CandidateSkillSerializer(skills, many=True).data

    def get_parsed_experience(self, obj):
        return CandidateExperienceSerializer(obj.experiences.all(), many=True).data

    def get_parsed_education(self, obj):
        return CandidateEducationSerializer(obj.educations.all(), many=True).data


# ─── Apply Review (input) ─────────────────────────────────────────────────────

class _ReviewCandidateUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    middle_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    current_role = serializers.CharField(max_length=255, required=False, allow_blank=True)
    current_company = serializers.CharField(max_length=255, required=False, allow_blank=True)
    current_location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    total_experience_years = serializers.DecimalField(
        max_digits=5, decimal_places=1, required=False, allow_null=True,
    )
    expected_ctc = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    current_ctc = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    notice_period_days = serializers.IntegerField(min_value=0, required=False, allow_null=True)

    def validate_phone(self, value):
        if value:
            from .services import normalize_phone
            normalize_phone(value)
        return value


class _ReviewSkillSerializer(serializers.Serializer):
    skill_name = serializers.CharField(max_length=128)
    years_experience = serializers.DecimalField(
        max_digits=4, decimal_places=1, required=False, allow_null=True,
    )
    proficiency = serializers.ChoiceField(
        choices=['beginner', 'intermediate', 'advanced', 'expert'],
        required=False, allow_blank=True, default='',
    )


class _ReviewExperienceSerializer(serializers.Serializer):
    job_title = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    company_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    industry = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)
    is_current = serializers.BooleanField(required=False, default=False)
    duration_months = serializers.IntegerField(required=False, allow_null=True, default=None)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    responsibilities = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
    )


class _ReviewEducationSerializer(serializers.Serializer):
    degree = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    specialization = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    institute = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    start_year = serializers.IntegerField(required=False, allow_null=True, default=None)
    end_year = serializers.IntegerField(required=False, allow_null=True, default=None)


class ResumeApplyReviewSerializer(serializers.Serializer):
    candidate = _ReviewCandidateUpdateSerializer(required=False)
    skills = _ReviewSkillSerializer(many=True, required=False)
    experience = _ReviewExperienceSerializer(many=True, required=False)
    education = _ReviewEducationSerializer(many=True, required=False)
    review_note = serializers.CharField(required=False, allow_blank=True, default='')


# ─── Audit / History ──────────────────────────────────────────────────────────

class TalentResumeReviewSerializer(serializers.ModelSerializer):
    reviewed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TalentResumeReview
        fields = [
            'id', 'resume', 'candidate', 'reviewed_by', 'reviewed_by_name',
            'review_type', 'previous_status', 'new_status', 'review_note',
            'correction_payload', 'created_at',
        ]
        read_only_fields = ['created_at']

    def get_reviewed_by_name(self, obj):
        if obj.reviewed_by:
            name = obj.reviewed_by.get_full_name()
            return name or str(obj.reviewed_by)
        return None


# ─── Duplicate Resolution (input) ────────────────────────────────────────────

class ResumeDuplicateResolutionSerializer(serializers.Serializer):
    RESOLUTION_CHOICES = ['link_existing', 'keep_separate', 'mark_duplicate']

    resolution = serializers.ChoiceField(choices=RESOLUTION_CHOICES)
    candidate = serializers.PrimaryKeyRelatedField(
        queryset=Candidate.objects.all(), required=False, allow_null=True, default=None,
    )
    note = serializers.CharField(required=False, allow_blank=True, default='')


class CandidateMergeSerializer(serializers.Serializer):
    target_candidate = serializers.PrimaryKeyRelatedField(queryset=Candidate.objects.all())
    note = serializers.CharField(required=False, allow_blank=True, default='')

"""
apps/talent/serializers.py

Phase Talent-Hiring-B: read/write serializers for Candidate and Resume.
"""

import json

from rest_framework import serializers

from apps.hiring.models import PipelineStage
from apps.mrf.models import ManpowerRequest, MRFLineItem

from .models import (
    Candidate, Resume, CandidateSkill,
    ParsedResume, CandidateExperience, CandidateEducation,
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
    skills_count = serializers.SerializerMethodField()
    resume_count = serializers.SerializerMethodField()
    latest_resume_status = serializers.SerializerMethodField()
    active_application_count = serializers.SerializerMethodField()

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
            'current_company', 'current_role', 'source_reference',
            'is_duplicate', 'do_not_contact',
            'skills_count', 'resume_count',
            'latest_resume_status', 'active_application_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = [f for f in fields if f != 'id']

    def get_skills_count(self, obj):
        return obj.skills.count()

    def get_resume_count(self, obj):
        return obj.resumes.count()

    def get_latest_resume_status(self, obj):
        r = obj.resumes.order_by('-uploaded_at').values('status').first()
        return r['status'] if r else None

    def get_active_application_count(self, obj):
        return obj.hiring_applications.exclude(
            status__in=['rejected', 'cancelled']
        ).count()


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
            'current_company', 'current_role', 'source_reference',
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


# ─── Resume ───────────────────────────────────────────────────────────────────

class ResumeSerializer(serializers.ModelSerializer):
    """Read serializer for resumes."""

    class Meta:
        model = Resume
        fields = [
            'id', 'candidate', 'file', 'original_filename', 'content_type',
            'size_bytes', 'parsed_status', 'uploaded_at', 'view_only_note',
            'status', 'file_hash', 'source_type',
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
        fields = ['candidate', 'file', 'source_type', 'view_only_note']
        extra_kwargs = {
            'source_type': {'required': False},
            'view_only_note': {'required': False},
        }

    def validate_source_type(self, value):
        allowed = {'manual_upload', 'recruiter_upload', 'portal', 'referral'}
        if value not in allowed:
            raise serializers.ValidationError(
                f"source_type must be one of: {', '.join(sorted(allowed))}."
            )
        return value


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

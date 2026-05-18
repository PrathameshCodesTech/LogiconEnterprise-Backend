"""
apps/hiring/serializers.py

Phase Talent-Hiring-B: read/write serializers for HiringApplication,
PipelineStage, ApplicationStageHistory, HiringDemand, CandidateMatchResult.
"""

from rest_framework import serializers

from .models import (
    HiringApplication, Interview, InterviewFeedback, Offer,
    PipelineStage, ApplicationStageHistory, CandidateMatchResult,
)


# ─── PipelineStage ────────────────────────────────────────────────────────────

class PipelineStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineStage
        fields = [
            'id', 'org', 'name', 'code', 'order',
            'stage_type', 'is_terminal', 'is_active',
        ]


# ─── ApplicationStageHistory ──────────────────────────────────────────────────

class ApplicationStageHistoryBriefSerializer(serializers.ModelSerializer):
    from_stage_name = serializers.CharField(
        source='from_stage.name', read_only=True, default=None,
    )
    to_stage_name = serializers.CharField(
        source='to_stage.name', read_only=True, default=None,
    )
    moved_by_username = serializers.CharField(
        source='moved_by.username', read_only=True, default=None,
    )

    class Meta:
        model = ApplicationStageHistory
        fields = [
            'id', 'from_stage', 'from_stage_name',
            'to_stage', 'to_stage_name',
            'from_status', 'to_status',
            'moved_by', 'moved_by_username',
            'comment', 'created_at',
        ]


class ApplicationStageHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationStageHistory
        fields = [
            'id', 'hiring_application', 'from_stage', 'to_stage',
            'from_status', 'to_status', 'moved_by', 'comment', 'created_at',
        ]
        read_only_fields = ['created_at']


# ─── HiringApplication ────────────────────────────────────────────────────────

class HiringApplicationReadSerializer(serializers.ModelSerializer):
    """Full read serializer with display fields and recent history."""
    candidate_name = serializers.SerializerMethodField()
    candidate_phone = serializers.CharField(
        source='candidate.phone', read_only=True,
    )
    site_name = serializers.CharField(source='site.name', read_only=True)
    client_name = serializers.CharField(
        source='site.client.name', read_only=True, default=None,
    )
    job_role_name = serializers.CharField(source='job_role.name', read_only=True)
    current_stage_name = serializers.CharField(
        source='current_stage.name', read_only=True, default=None,
    )
    current_stage_code = serializers.CharField(
        source='current_stage.code', read_only=True, default=None,
    )
    recent_stage_history = serializers.SerializerMethodField()

    class Meta:
        model = HiringApplication
        fields = [
            'id', 'org', 'candidate', 'candidate_name', 'candidate_phone',
            'mrf', 'site', 'site_name', 'client_name',
            'job_role', 'job_role_name', 'mrf_line_item',
            'current_stage', 'current_stage_name', 'current_stage_code',
            'status', 'match_score',
            'shortlisted_by', 'shortlisted_at',
            'client_visible', 'client_decision',
            'client_decision_by', 'client_decision_at', 'client_decision_note',
            'source_intake_submission',
            'recent_stage_history',
            'created_at', 'updated_at',
        ]

    def get_candidate_name(self, obj):
        return obj.candidate.full_name

    def get_recent_stage_history(self, obj):
        history = (
            obj.stage_history
            .select_related('from_stage', 'to_stage', 'moved_by')
            .order_by('-created_at')[:5]
        )
        return ApplicationStageHistoryBriefSerializer(history, many=True).data


class HiringApplicationCreateSerializer(serializers.ModelSerializer):
    """Write serializer for creating a new hiring application."""

    class Meta:
        model = HiringApplication
        fields = [
            'candidate', 'mrf', 'mrf_line_item',
            'current_stage', 'source_intake_submission', 'match_score',
        ]
        extra_kwargs = {
            'mrf': {'required': False, 'allow_null': True},
            'mrf_line_item': {'required': False, 'allow_null': True},
            'current_stage': {'required': False, 'allow_null': True},
            'source_intake_submission': {'required': False, 'allow_null': True},
            'match_score': {'required': False, 'allow_null': True},
        }
        # Suppress the auto-generated UniqueTogetherValidator so validate()
        # can return the user-facing "already linked" message instead.
        validators = []

    def validate(self, data):
        mrf = data.get('mrf')
        mrf_li = data.get('mrf_line_item')
        candidate = data.get('candidate')

        if not mrf and not mrf_li:
            raise serializers.ValidationError(
                {'non_field_errors': 'Provide at least one of: mrf, mrf_line_item.'}
            )

        if mrf_li and not mrf:
            data['mrf'] = mrf_li.mrf
            mrf = mrf_li.mrf

        errors = {}
        if mrf and mrf.status != 'approved':
            errors['mrf'] = 'MRF must be in approved status to create an application.'

        if mrf and mrf_li and mrf_li.mrf_id != mrf.pk:
            errors['mrf_line_item'] = 'MRF line item must belong to the specified MRF.'

        if candidate and mrf and candidate.org_id != mrf.org_id:
            errors['candidate'] = 'Candidate organization must match MRF organization.'

        if mrf_li and candidate and HiringApplication.objects.filter(
            candidate=candidate, mrf_line_item=mrf_li,
        ).exists():
            errors['non_field_errors'] = 'This candidate is already linked to this hiring demand.'

        if errors:
            raise serializers.ValidationError(errors)
        return data


class HiringApplicationPatchSerializer(serializers.ModelSerializer):
    """Write serializer for patching basic fields on an existing application."""

    class Meta:
        model = HiringApplication
        fields = [
            'client_visible', 'client_decision', 'client_decision_note', 'match_score',
        ]
        extra_kwargs = {
            'client_visible': {'required': False},
            'client_decision': {'required': False, 'allow_null': True, 'allow_blank': True},
            'client_decision_note': {'required': False},
            'match_score': {'required': False, 'allow_null': True},
        }


# ─── HiringDemand ─────────────────────────────────────────────────────────────

class HiringDemandSerializer(serializers.Serializer):
    """Read serializer for hiring demand (approved MRF line items + counts)."""
    id = serializers.IntegerField()
    mrf_id = serializers.IntegerField()
    site_id = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()
    client_name = serializers.SerializerMethodField()
    job_role_id = serializers.IntegerField()
    job_role_name = serializers.SerializerMethodField()
    billing_type = serializers.SerializerMethodField()
    requested_headcount = serializers.IntegerField(source='headcount')

    application_count = serializers.IntegerField()
    shortlisted_count = serializers.IntegerField()
    selected_count = serializers.IntegerField()
    offer_accepted_count = serializers.IntegerField()
    open_count = serializers.SerializerMethodField()

    def get_site_id(self, obj):
        return obj.mrf.site_id

    def get_site_name(self, obj):
        return obj.mrf.site.name if obj.mrf.site else None

    def get_client_id(self, obj):
        return obj.mrf.site.client_id if obj.mrf.site else None

    def get_client_name(self, obj):
        if obj.mrf.site and obj.mrf.site.client:
            return obj.mrf.site.client.name
        return None

    def get_job_role_name(self, obj):
        return obj.job_role.name if obj.job_role else None

    def get_billing_type(self, obj):
        return obj.mrf.billing_type

    def get_open_count(self, obj):
        filled = getattr(obj, 'offer_accepted_count', 0) or 0
        return max(0, obj.headcount - filled)


# ─── CandidateMatchResult ─────────────────────────────────────────────────────

class CandidateMatchResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateMatchResult
        fields = [
            'id', 'org', 'candidate', 'mrf_line_item',
            'final_score',
            'role_score', 'skill_score', 'experience_score',
            'location_score', 'industry_score', 'education_score',
            'salary_score', 'semantic_score',
            'matched_skills', 'missing_skills',
            'match_reason', 'warnings',
            'match_details',
            'match_score',  # legacy — kept for backward compatibility
            'match_source', 'is_auto_match',
            'created_by', 'created_at',
        ]
        read_only_fields = ['created_at']


# ─── Interview / InterviewFeedback / Offer ────────────────────────────────────

class InterviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interview
        fields = [
            'id', 'hiring_application', 'round_type', 'round_number',
            'scheduled_at', 'scheduled_by', 'interviewer',
            'status', 'mode', 'location', 'meeting_link',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class InterviewFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = InterviewFeedback
        fields = [
            'id', 'interview', 'given_by', 'rating',
            'feedback', 'recommendation', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class OfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = Offer
        fields = [
            'id', 'hiring_application', 'offered_ctc', 'salary_breakup',
            'joining_date', 'status', 'released_by', 'released_at',
            'accepted_at', 'declined_at', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

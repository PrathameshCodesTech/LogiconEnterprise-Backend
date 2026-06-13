"""
apps/hiring/serializers.py

Phase Talent-Hiring-B: read/write serializers for HiringApplication,
PipelineStage, ApplicationStageHistory, HiringDemand, CandidateMatchResult.
"""

from decimal import Decimal

from rest_framework import serializers

from apps.talent.models import Candidate

from .models import (
    HiringApplication, Interview, InterviewFeedback, InterviewPlan, InterviewPlanRound, Offer,
    PipelineStage, ApplicationStageHistory, CandidateMatchResult,
)
from .lanes import (
    hiring_lane_for_application,
    hiring_lane_for_mrf,
    hiring_lane_label,
    requires_client_review_for_application,
    requires_client_review_for_mrf,
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
    offer_status = serializers.SerializerMethodField()
    offered_ctc = serializers.SerializerMethodField()
    offer_joining_date = serializers.SerializerMethodField()
    billing_type = serializers.SerializerMethodField()
    hiring_lane = serializers.SerializerMethodField()
    hiring_lane_label = serializers.SerializerMethodField()
    requires_client_review = serializers.SerializerMethodField()

    class Meta:
        model = HiringApplication
        fields = [
            'id', 'org', 'candidate', 'candidate_name', 'candidate_phone',
            'mrf', 'site', 'site_name', 'client_name',
            'job_role', 'job_role_name', 'mrf_line_item',
            'billing_type', 'hiring_lane', 'hiring_lane_label',
            'requires_client_review',
            'current_stage', 'current_stage_name', 'current_stage_code',
            'interview_plan',
            'status', 'match_score', 'match_result', 'match_snapshot',
            'shortlisted_by', 'shortlisted_at',
            'client_visible', 'client_decision',
            'client_decision_by', 'client_decision_at', 'client_decision_note',
            'source_intake_submission',
            'offer_status', 'offered_ctc', 'offer_joining_date',
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

    def get_offer_status(self, obj):
        try:
            return obj.offer.status
        except Exception:
            return None

    def get_offered_ctc(self, obj):
        try:
            ctc = obj.offer.offered_ctc
            return str(ctc) if ctc is not None else None
        except Exception:
            return None

    def get_offer_joining_date(self, obj):
        try:
            jd = obj.offer.joining_date
            return str(jd) if jd is not None else None
        except Exception:
            return None

    def get_billing_type(self, obj):
        return obj.mrf.billing_type

    def get_hiring_lane(self, obj):
        return hiring_lane_for_application(obj)

    def get_hiring_lane_label(self, obj):
        return hiring_lane_label(hiring_lane_for_application(obj))

    def get_requires_client_review(self, obj):
        return requires_client_review_for_application(obj)


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
    requesting_department_id = serializers.SerializerMethodField()
    requesting_department_name = serializers.SerializerMethodField()
    requesting_department_code = serializers.SerializerMethodField()
    required_department_id = serializers.SerializerMethodField()
    required_department_name = serializers.SerializerMethodField()
    required_department_code = serializers.SerializerMethodField()
    resolved_budget_plan_id = serializers.SerializerMethodField()
    resolved_budget_plan_name = serializers.SerializerMethodField()
    resolved_budget_plan_code = serializers.SerializerMethodField()
    job_role_id = serializers.IntegerField()
    job_role_name = serializers.SerializerMethodField()
    billing_type = serializers.SerializerMethodField()
    hiring_lane = serializers.SerializerMethodField()
    hiring_lane_label = serializers.SerializerMethodField()
    requires_client_review = serializers.SerializerMethodField()
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

    def get_requesting_department_id(self, obj):
        return obj.mrf.requesting_department_id

    def get_requesting_department_name(self, obj):
        dept = obj.mrf.requesting_department
        return dept.name if dept else None

    def get_requesting_department_code(self, obj):
        dept = obj.mrf.requesting_department
        return dept.code if dept else None

    def get_required_department_id(self, obj):
        return obj.mrf.required_department_id

    def get_required_department_name(self, obj):
        dept = obj.mrf.required_department
        return dept.name if dept else None

    def get_required_department_code(self, obj):
        dept = obj.mrf.required_department
        return dept.code if dept else None

    def _budget_context(self, obj):
        if not hasattr(obj, '_hiring_demand_budget_context'):
            from apps.mrf.services import get_resolved_budget_context
            obj._hiring_demand_budget_context = get_resolved_budget_context(obj.mrf)
        return obj._hiring_demand_budget_context

    def get_resolved_budget_plan_id(self, obj):
        return self._budget_context(obj)['resolved_budget_plan_id']

    def get_resolved_budget_plan_name(self, obj):
        return self._budget_context(obj)['resolved_budget_plan_name']

    def get_resolved_budget_plan_code(self, obj):
        return self._budget_context(obj)['resolved_budget_plan_code']

    def get_job_role_name(self, obj):
        return obj.job_role.name if obj.job_role else None

    def get_billing_type(self, obj):
        return obj.mrf.billing_type

    def get_hiring_lane(self, obj):
        return hiring_lane_for_mrf(obj.mrf)

    def get_hiring_lane_label(self, obj):
        return hiring_lane_label(hiring_lane_for_mrf(obj.mrf))

    def get_requires_client_review(self, obj):
        return requires_client_review_for_mrf(obj.mrf)

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
            'match_score',  # legacy - kept for backward compatibility
            'match_source', 'is_auto_match',
            'created_by', 'created_at',
        ]
        read_only_fields = ['created_at']


# ─── Interview / InterviewFeedback / Offer ────────────────────────────────────

class InterviewPlanRoundSerializer(serializers.ModelSerializer):
    class Meta:
        model = InterviewPlanRound
        fields = [
            'id', 'plan', 'round_type', 'round_number', 'mode',
            'is_required', 'is_active', 'instructions',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class InterviewPlanSerializer(serializers.ModelSerializer):
    rounds = InterviewPlanRoundSerializer(many=True, required=False)
    job_role_name = serializers.CharField(source='job_role.name', read_only=True, default=None)

    class Meta:
        model = InterviewPlan
        fields = [
            'id', 'org', 'job_role', 'job_role_name',
            'name', 'code', 'description',
            'is_default', 'is_active', 'rounds',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']
        extra_kwargs = {
            'org': {'required': False},
        }

    def create(self, validated_data):
        rounds = validated_data.pop('rounds', [])
        plan = InterviewPlan.objects.create(**validated_data)
        for round_data in rounds:
            round_data.pop('plan', None)
            InterviewPlanRound.objects.create(plan=plan, **round_data)
        return plan

    def update(self, instance, validated_data):
        rounds = validated_data.pop('rounds', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if rounds is not None:
            instance.rounds.update(is_active=False)
            for round_data in rounds:
                round_data.pop('plan', None)
                round_id = round_data.pop('id', None)
                if round_id:
                    InterviewPlanRound.objects.update_or_create(
                        id=round_id,
                        plan=instance,
                        defaults=round_data,
                    )
                else:
                    InterviewPlanRound.objects.create(plan=instance, **round_data)
        return instance


class ApplyInterviewPlanSerializer(serializers.Serializer):
    plan = serializers.PrimaryKeyRelatedField(
        queryset=InterviewPlan.objects.filter(is_active=True),
    )


class InterviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interview
        fields = [
            'id', 'hiring_application', 'planned_round', 'round_type', 'round_number',
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


class InterviewAssignmentSerializer(serializers.ModelSerializer):
    application = serializers.IntegerField(source='hiring_application_id', read_only=True)
    candidate_name = serializers.CharField(
        source='hiring_application.candidate.full_name', read_only=True,
    )
    candidate_phone = serializers.CharField(
        source='hiring_application.candidate.phone', read_only=True,
    )
    site_name = serializers.CharField(source='hiring_application.site.name', read_only=True)
    client_name = serializers.CharField(
        source='hiring_application.site.client.name', read_only=True, default=None,
    )
    job_role_name = serializers.CharField(
        source='hiring_application.job_role.name', read_only=True,
    )
    application_status = serializers.CharField(
        source='hiring_application.status', read_only=True,
    )
    planned_round_name = serializers.SerializerMethodField()
    interviewer_name = serializers.CharField(
        source='interviewer.username', read_only=True, default=None,
    )
    scheduled_by_name = serializers.CharField(
        source='scheduled_by.username', read_only=True, default=None,
    )
    assignment_state = serializers.CharField(read_only=True)
    latest_feedback = serializers.SerializerMethodField()

    class Meta:
        model = Interview
        fields = [
            'id', 'application', 'candidate_name', 'candidate_phone',
            'client_name', 'site_name', 'job_role_name', 'application_status',
            'planned_round', 'planned_round_name', 'round_type', 'round_number',
            'scheduled_at', 'scheduled_by', 'scheduled_by_name',
            'interviewer', 'interviewer_name', 'status', 'assignment_state',
            'mode', 'location', 'meeting_link', 'latest_feedback',
            'created_at', 'updated_at',
        ]

    def get_planned_round_name(self, obj):
        if obj.planned_round_id:
            return (
                f"Round {obj.planned_round.round_number} "
                f"({obj.planned_round.get_round_type_display()})"
            )
        return None

    def get_latest_feedback(self, obj):
        from .interview_services import latest_feedback_for_interview
        feedback = latest_feedback_for_interview(obj)
        if feedback is None:
            return None
        return InterviewFeedbackSerializer(feedback).data


class OfferSerializer(serializers.ModelSerializer):
    released_by_username = serializers.CharField(
        source='released_by.username', read_only=True, default=None,
    )

    class Meta:
        model = Offer
        fields = [
            'id', 'hiring_application', 'offered_ctc', 'salary_breakup',
            'joining_date', 'status',
            'released_by', 'released_by_username', 'released_at',
            'accepted_at', 'declined_at', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class OfferCreateSerializer(serializers.Serializer):
    """Input for creating a new offer."""
    hiring_application = serializers.PrimaryKeyRelatedField(
        queryset=HiringApplication.objects.all(),
    )
    offered_ctc = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01')
    )
    salary_breakup = serializers.JSONField(required=False, default=dict)
    joining_date = serializers.DateField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class OfferUpdateSerializer(serializers.Serializer):
    """Input for patching a draft offer."""
    offered_ctc = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'), required=False,
    )
    salary_breakup = serializers.JSONField(required=False)
    joining_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class OfferActionSerializer(serializers.Serializer):
    """Input for offer lifecycle actions (release/accept/decline/withdraw/expire)."""
    note = serializers.CharField(required=False, allow_blank=True, default='')


# ─── Candidate Pool (ranked results) ─────────────────────────────────────────

class CandidatePoolResultSerializer(serializers.Serializer):
    """Single ranked candidate entry returned by the candidate-pool endpoint."""
    candidate = serializers.SerializerMethodField()
    match_result = serializers.IntegerField(required=False, allow_null=True)
    score = serializers.FloatField()
    match_status = serializers.CharField()
    score_breakdown = serializers.DictField()
    matched_skills = serializers.ListField(child=serializers.CharField())
    missing_skills = serializers.ListField(child=serializers.CharField())
    extra_candidate_skills = serializers.ListField(child=serializers.CharField())
    reasons = serializers.ListField(child=serializers.CharField())
    warnings = serializers.ListField(child=serializers.CharField())

    def get_candidate(self, obj):
        from apps.talent.serializers import CandidateSerializer
        return CandidateSerializer(obj['candidate'], context=self.context).data


# ─── Shortlist (input) ────────────────────────────────────────────────────────

class ShortlistCandidateSerializer(serializers.Serializer):
    candidate = serializers.PrimaryKeyRelatedField(queryset=Candidate.objects.all())
    match_result = serializers.PrimaryKeyRelatedField(
        queryset=CandidateMatchResult.objects.all(),
        required=False, allow_null=True, default=None,
    )
    comment = serializers.CharField(required=False, allow_blank=True, default='')


# ─── Client Review ────────────────────────────────────────────────────────────

class SendToClientReviewSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default='')


class BulkSendToClientReviewSerializer(serializers.Serializer):
    application_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_null=True, default=None,
    )
    note = serializers.CharField(required=False, allow_blank=True, default='')


class ClientDecisionSerializer(serializers.Serializer):
    DECISION_CHOICES = ['approved', 'rejected']
    decision = serializers.ChoiceField(choices=DECISION_CHOICES)
    note = serializers.CharField(required=False, allow_blank=True, default='')
    override = serializers.BooleanField(required=False, default=False)


class ClientReviewApplicationSerializer(serializers.ModelSerializer):
    """
    Client-facing read serializer. Exposes safe candidate summary only -
    never raw_text or cleaned_text from the resume file.
    """
    candidate_summary = serializers.SerializerMethodField()
    job_role_name = serializers.CharField(source='job_role.name', read_only=True)
    site_name = serializers.CharField(source='site.name', read_only=True)
    client_name = serializers.CharField(
        source='site.client.name', read_only=True, default=None,
    )
    current_stage_name = serializers.CharField(
        source='current_stage.name', read_only=True, default=None,
    )
    client_decision_by_username = serializers.CharField(
        source='client_decision_by.username', read_only=True, default=None,
    )
    resume_summary = serializers.SerializerMethodField()
    billing_type = serializers.SerializerMethodField()
    hiring_lane = serializers.SerializerMethodField()
    hiring_lane_label = serializers.SerializerMethodField()
    requires_client_review = serializers.SerializerMethodField()

    class Meta:
        model = HiringApplication
        fields = [
            'id', 'org',
            'candidate', 'candidate_summary',
            'mrf', 'mrf_line_item',
            'site', 'site_name', 'client_name',
            'job_role', 'job_role_name',
            'billing_type', 'hiring_lane', 'hiring_lane_label',
            'requires_client_review',
            'match_score',
            'status',
            'current_stage', 'current_stage_name',
            'client_visible',
            'client_decision',
            'client_decision_by', 'client_decision_by_username',
            'client_decision_at',
            'client_decision_note',
            'resume_summary',
            'created_at', 'updated_at',
        ]

    def get_candidate_summary(self, obj):
        c = obj.candidate
        return {
            'id': c.pk,
            'full_name': c.full_name,
            'phone': c.phone,
            'email': c.email or '',
            'current_role': c.current_role or '',
            'current_location': c.current_location or '',
            'total_experience_years': (
                str(c.total_experience_years) if c.total_experience_years is not None else None
            ),
            'availability_status': c.availability_status or '',
        }

    def get_resume_summary(self, obj):
        """Parsed resume metadata - deliberately excludes raw_text and cleaned_text."""
        try:
            from apps.talent.models import ParsedResume
            parsed = (
                ParsedResume.objects
                .filter(resume__candidate=obj.candidate, resume__status='indexed')
                .order_by('-created_at')
                .first()
            )
            if not parsed:
                return None
            return {
                'confidence': str(parsed.confidence) if parsed.confidence is not None else None,
                'summary': parsed.summary or '',
                'career_level': parsed.career_level or '',
                'primary_domain': parsed.primary_domain or '',
            }
        except Exception:
            return None

    def get_billing_type(self, obj):
        return obj.mrf.billing_type if obj.mrf else None

    def get_hiring_lane(self, obj):
        return hiring_lane_for_application(obj)

    def get_hiring_lane_label(self, obj):
        return hiring_lane_label(hiring_lane_for_application(obj))

    def get_requires_client_review(self, obj):
        return requires_client_review_for_application(obj)

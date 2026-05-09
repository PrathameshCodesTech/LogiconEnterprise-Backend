from rest_framework import serializers
from .models import HiringApplication, Interview, InterviewFeedback, Offer


class HiringApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = HiringApplication
        fields = [
            'id', 'org', 'candidate', 'mrf', 'mrf_line_item',
            'site', 'job_role', 'source_intake_submission', 'match_score',
            'status', 'shortlisted_by', 'shortlisted_at',
            'client_visible', 'client_decision',
            'client_decision_by', 'client_decision_at', 'client_decision_note',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


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

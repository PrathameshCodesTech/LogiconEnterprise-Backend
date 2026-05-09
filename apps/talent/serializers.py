from rest_framework import serializers
from .models import Candidate, Resume, CandidateSkill


class CandidateSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateSkill
        fields = ['id', 'skill_name', 'normalized_skill_name', 'confidence']


class CandidateSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Candidate
        fields = [
            'id', 'org', 'phone', 'phone_normalized',
            'first_name', 'middle_name', 'last_name', 'full_name',
            'email', 'current_location',
            'total_experience_years', 'current_ctc', 'expected_ctc',
            'source', 'is_blacklisted', 'blacklist_reason',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at', 'full_name']


class ResumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resume
        fields = [
            'id', 'candidate', 'file', 'original_filename', 'content_type',
            'size_bytes', 'parsed_status', 'uploaded_at', 'view_only_note',
        ]
        read_only_fields = ['uploaded_at']

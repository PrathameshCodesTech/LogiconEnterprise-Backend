"""Serializers for unauthenticated client proposal response API."""

from rest_framework import serializers

from apps.sales.models import CLIENT_RESPONSE_CHOICES


class PublicProposalResponseSubmitSerializer(serializers.Serializer):
    response = serializers.ChoiceField(
        choices=[
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('negotiation_required', 'Negotiation Required'),
            ('revision_required', 'Revision Required'),
        ],
    )
    remarks = serializers.CharField(required=False, allow_blank=True, default='')
    respondent_name = serializers.CharField(required=False, allow_blank=True, default='')
    respondent_email = serializers.EmailField(required=False, allow_blank=True, default='')

"""
apps/intake/public_views.py

Public (AllowAny, throttled) endpoints for the QR intake flow.

GET  /api/public/campaigns/<token>/   - campaign detail
POST /api/public/submissions/         - candidate submission
"""

from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from django.utils import timezone

from .models import QRCampaign
from .serializers import (
    PublicCampaignSerializer,
    SubmissionCreateSerializer,
    SubmissionResponseSerializer,
)
from .services import (
    create_intake_submission,
    create_intake_documents,
    validate_submission_documents,
)


class SubmissionThrottle(AnonRateThrottle):
    rate = '30/hour'


def _get_active_campaign(token: str) -> QRCampaign:
    try:
        campaign = QRCampaign.objects.select_related(
            'site', 'org',
        ).prefetch_related(
            'campaign_job_roles__job_role',
            'form_fields',
        ).get(token=token)
    except QRCampaign.DoesNotExist:
        raise NotFound("Campaign not found or inactive.")

    if not campaign.is_active:
        raise NotFound("Campaign not found or inactive.")

    now = timezone.now()
    if campaign.starts_at and campaign.starts_at > now:
        raise NotFound("Campaign not found or inactive.")
    if campaign.ends_at and campaign.ends_at < now:
        raise NotFound("Campaign not found or inactive.")

    return campaign


class PublicCampaignDetailView(generics.RetrieveAPIView):
    """GET /api/public/campaigns/<token>/"""
    permission_classes = [AllowAny]
    serializer_class = PublicCampaignSerializer
    throttle_classes = [AnonRateThrottle]

    def get_object(self):
        return _get_active_campaign(self.kwargs['token'])


class PublicSubmissionCreateView(APIView):
    """POST /api/public/submissions/"""
    permission_classes = [AllowAny]
    throttle_classes = [SubmissionThrottle]

    def post(self, request, *args, **kwargs):
        serializer = SubmissionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validate_submission_documents(
            serializer.validated_data['campaign'],
            serializer.validated_data.get('job_role'),
            request.FILES,
        )

        submission = create_intake_submission(
            serializer.validated_data, request=request,
        )

        if request.FILES:
            create_intake_documents(submission, request.FILES)

        out = SubmissionResponseSerializer(submission)
        return Response(out.data, status=status.HTTP_201_CREATED)

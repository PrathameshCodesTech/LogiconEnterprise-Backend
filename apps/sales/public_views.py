"""
Unauthenticated client proposal review/response (secure token link).

GET  /api/sales/public/proposal-response/<token>/
POST /api/sales/public/proposal-response/<token>/
"""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.sales.exceptions import ClientProposalTokenError
from apps.sales.public_serializers import PublicProposalResponseSubmitSerializer
from apps.sales.services import (
    record_public_client_response,
    validate_client_proposal_token,
    _proposal_already_responded,
)


class ProposalResponseThrottle(AnonRateThrottle):
    rate = '60/hour'


def _serialize_budget_line(line):
    return {
        'id': line.pk,
        'site': line.site_id,
        'site_id': line.site_id,
        'site_name': line.site.site_name if line.site_id else None,
        'role_requirement': line.role_requirement_id,
        'job_role': line.job_role_id,
        'job_role_id': line.job_role_id,
        'job_role_name': line.job_role.name if line.job_role_id else None,
        'description': line.description,
        'service_category': line.service_category or '',
        'manpower_count': line.manpower_count,
        'unit_cost': str(line.unit_cost),
        'total_cost': str(line.total_cost),
        'sort_order': line.sort_order,
    }


def _serialize_breakup_line(line):
    return {
        'id': line.pk,
        'site': line.site_id,
        'site_id': line.site_id,
        'site_name': line.site.site_name if line.site_id else None,
        'role_requirement': line.role_requirement_id,
        'job_role': line.job_role_id,
        'job_role_id': line.job_role_id,
        'job_role_name': line.job_role.name if line.job_role_id else None,
        'component_name': line.component_name,
        'component_type': line.component_type,
        'percentage': str(line.percentage) if line.percentage is not None else None,
        'amount': str(line.amount),
        'sort_order': line.sort_order,
    }


def _public_proposal_payload(proposal, *, token_record=None, already_responded=False):
    lead = proposal.lead
    data = {
        'client_name': lead.client_name,
        'client_contact_person': lead.client_contact_person or '',
        'client_email': lead.client_email or '',
        'client_phone': lead.client_phone or '',
        'sales_owner_name': lead.sales_person.username if lead.sales_person_id else '',
        'proposal_version_number': proposal.version_number,
        'proposal_status': proposal.status,
        'client_approval_status': proposal.client_approval_status,
        'internal_approval_status': proposal.internal_approval_status,
        'grand_total': str(proposal.grand_total),
        'manpower_total': proposal.manpower_total,
        'management_fee_percent': (
            str(proposal.management_fee_percent)
            if proposal.management_fee_percent is not None else None
        ),
        'gst_applicable': proposal.gst_applicable,
        'budget_lines': [
            _serialize_budget_line(bl)
            for bl in proposal.budget_lines.order_by('sort_order', 'pk')
        ],
        'breakup_lines': [
            _serialize_breakup_line(bl)
            for bl in proposal.breakup_lines.order_by('sort_order', 'pk')
        ],
        'already_responded': already_responded,
        'can_respond': not already_responded,
    }
    if token_record is not None:
        data['expires_at'] = token_record.expires_at
        data['recipient_email'] = token_record.recipient_email
        data['recipient_name'] = token_record.recipient_name or ''
    if already_responded:
        latest = proposal.client_responses.order_by('-created_at').first()
        if latest:
            data['client_response'] = latest.client_response
            data['responded_at'] = latest.responded_at
            data['client_remarks'] = latest.client_remarks or ''
    return data


def _token_error_response(exc):
    status_map = {
        'invalid_token': status.HTTP_404_NOT_FOUND,
        'expired': status.HTTP_410_GONE,
        'revoked': status.HTTP_410_GONE,
        'used': status.HTTP_410_GONE,
        'already_responded': status.HTTP_409_CONFLICT,
        'not_approved': status.HTTP_403_FORBIDDEN,
    }
    return Response(
        {'detail': str(exc), 'code': exc.code},
        status=status_map.get(exc.code, status.HTTP_400_BAD_REQUEST),
    )


class PublicProposalResponseView(APIView):
    """GET/POST public proposal review for external clients."""
    permission_classes = [AllowAny]
    throttle_classes = [ProposalResponseThrottle]

    def get(self, request, token):
        try:
            token_record, proposal = validate_client_proposal_token(
                token, touch_access=True,
            )
        except ClientProposalTokenError as exc:
            return _token_error_response(exc)

        already = _proposal_already_responded(proposal)
        return Response(_public_proposal_payload(
            proposal, token_record=token_record, already_responded=already,
        ))

    def post(self, request, token):
        serializer = PublicProposalResponseSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = record_public_client_response(
                token,
                serializer.validated_data['response'],
                serializer.validated_data.get('remarks', ''),
                respondent_name=serializer.validated_data.get('respondent_name', ''),
                respondent_email=serializer.validated_data.get('respondent_email', ''),
            )
        except ClientProposalTokenError as exc:
            return _token_error_response(exc)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)

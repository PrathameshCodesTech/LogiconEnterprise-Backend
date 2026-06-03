"""Email dispatch for sales proposal client response links."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


def send_proposal_client_link_email(*, proposal, recipient_email, recipient_name, raw_token, expires_at):
    """
    Send proposal review link to external client.

    Raises on SMTP failure (fail_silently=False). Caller must not mark proposal sent if this fails.
    """
    if not recipient_email:
        raise ValueError('recipient_email is required to send proposal link.')

    frontend_url = (getattr(settings, 'FRONTEND_BASE_URL', '') or 'http://127.0.0.1:5173').strip().rstrip('/')
    if not (frontend_url.startswith('http://') or frontend_url.startswith('https://')):
        raise ValueError('FRONTEND_BASE_URL must start with http:// or https://.')

    response_url = f"{frontend_url}/proposal-response?token={raw_token}"
    lead = proposal.lead
    display_name = recipient_name or lead.client_contact_person or lead.client_name
    expiry_label = timezone.localtime(expires_at).strftime('%d %b %Y, %I:%M %p %Z')

    subject = f"Proposal v{proposal.version_number} for {lead.client_name} — review & response"
    body = (
        f"Hello {display_name},\n\n"
        f"Please review the commercial proposal prepared for {lead.client_name}.\n\n"
        f"Proposal version: v{proposal.version_number}\n"
        f"Grand total: {proposal.grand_total}\n\n"
        f"Open the secure link below to view details and submit your response:\n\n"
        f"{response_url}\n\n"
        f"This link expires on {expiry_label}.\n\n"
        f"If you did not expect this email, please ignore it."
    )

    logger.info(
        "Sending proposal client link email proposal_pk=%s recipient=%s",
        proposal.pk,
        recipient_email,
    )
    sent_count = send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
        fail_silently=False,
    )
    if sent_count < 1:
        raise RuntimeError('SMTP backend accepted the email call but sent 0 messages.')
    return sent_count

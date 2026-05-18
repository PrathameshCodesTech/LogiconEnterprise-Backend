"""
apps/accounts/services.py

Account-level services: invite email generation and dispatch.
"""

import logging

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def send_user_invite_email(user, *, context_label=None):
    """
    Generate a set-password link for `user` and send an invite email.

    - Uses PasswordResetTokenGenerator (token not stored; validated on use).
    - Raises on SMTP failure; callers decide whether to swallow or propagate.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = PasswordResetTokenGenerator().make_token(user)
    frontend_url = (getattr(settings, 'FRONTEND_BASE_URL', '') or 'http://127.0.0.1:5173').strip().rstrip('/')
    invite_url = f"{frontend_url}/set-password?uid={uid}&token={token}"

    label = context_label or 'Your account'
    subject = f"{label} - Set your password"
    body = (
        f"Hello {user.first_name or user.username},\n\n"
        f"Your account has been created. Please set your password using the link below:\n\n"
        f"{invite_url}\n\n"
        f"This link is valid for a limited time.\n\n"
        f"If you did not expect this email, please ignore it."
    )

    logger.debug("Sending invite email to user pk=%s", user.pk)
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )

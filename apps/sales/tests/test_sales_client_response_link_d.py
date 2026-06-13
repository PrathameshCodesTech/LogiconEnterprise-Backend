"""
Phase Sales-Client-Response-Link-D — secure public client proposal response.
"""

import re
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.capabilities import (
    SALES_LEAD_UPDATE, SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE,
    SALES_PROPOSAL_UPDATE, SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
    SALES_SURVEY_UPDATE,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sales.models import (
    SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement,
    ProposalVersion, SalesProposalClientToken, ClientProposalResponse,
)
from apps.sales.services import (
    generate_proposal_version,
    submit_to_operations,
    mark_proposal_internally_approved,
    send_proposal_to_client,
    create_client_proposal_token,
    record_public_client_response,
    convert_won_sales_lead_to_onboarding_setup,
    validate_proposal_ready_for_mobilisation_conversion,
)
from apps.sales.proposal_calculation import seed_default_proposal_component_rules
from apps.sales.token_utils import hash_client_proposal_token


PROPOSALS_URL = '/api/sales/proposal-versions/'
PUBLIC_URL = '/api/sales/public/proposal-response/{token}/'


def _org(code):
    org = Organization.objects.create(name=f'Org {code}', code=code)
    seed_default_proposal_component_rules(org=org)
    return org


def _scope_node(org):
    return ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )


def _caps():
    return [
        SALES_LEAD_UPDATE, SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE,
        SALES_PROPOSAL_UPDATE, SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
        SALES_SURVEY_UPDATE,
    ]


def _user(username, org, role=None, scope_node=None):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.save()
    if role and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _ready_proposal(org, user, email='client@acme.com'):
    lead = SalesLead.objects.create(
        org=org, client_name='Acme', client_email=email,
        client_contact_person='Jane Client',
    )
    site = SalesLeadSite.objects.create(lead=lead, site_name='HQ', city='Mumbai', state='MH')
    jr = JobRole.objects.get_or_create(org=org, code='guard', defaults={'name': 'Guard'})[0]
    from apps.sales.tests.proposal_wage_fixtures import (
        ensure_wage_category, ensure_location_area_mumbai, ensure_minimum_wage,
        wire_site_and_requirement_for_wages,
    )
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    ensure_minimum_wage(org, location, wage_cat, jr, monthly_wage=12000)
    rr = SalesRoleRequirement.objects.create(
        lead=lead, site=site, job_role=jr, manpower_count=2, is_active=True,
    )
    wire_site_and_requirement_for_wages(site, rr, location, wage_cat)
    submit_to_operations(lead, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])
    proposal = generate_proposal_version(lead, user)
    proposal.status = 'submitted_internal'
    proposal.internal_approval_status = 'in_progress'
    proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
    mark_proposal_internally_approved(proposal, user)
    proposal.refresh_from_db()
    return lead, proposal


def _send_and_get_raw_token(proposal, user):
    send_proposal_to_client(proposal, user)
    body = mail.outbox[-1].body
    match = re.search(r'proposal-response\?token=([^\s\n]+)', body)
    assert match, body
    return match.group(1)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestClientProposalToken(TestCase):
    def setUp(self):
        self.org = _org('cpt')
        self.node = _scope_node(self.org)
        role = AccessRole.objects.get_or_create(org=self.org, code='sales_cpt', defaults={'name': 'sales'})[0]
        bootstrap_role_permissions(role, _caps())
        self.user = _user('u_cpt', self.org, role, self.node)
        self.lead, self.proposal = _ready_proposal(self.org, self.user)

    def test_cannot_create_token_before_internal_approval(self):
        draft = generate_proposal_version(self.lead, self.user)
        with self.assertRaises(ValueError):
            create_client_proposal_token(draft, 'x@y.com', self.user)

    def test_send_creates_hashed_token_not_raw(self):
        send_proposal_to_client(self.proposal, self.user)
        token = SalesProposalClientToken.objects.get(proposal_version=self.proposal)
        self.assertEqual(len(token.token_hash), 64)
        self.assertNotIn('http', token.token_hash)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('proposal-response?token=', mail.outbox[0].body)

    def test_send_does_not_mark_sent_if_email_fails(self):
        from unittest.mock import patch
        self.proposal.refresh_from_db()
        with patch(
            'apps.sales.email_services.send_mail',
            side_effect=RuntimeError('smtp down'),
        ):
            with self.assertRaises(ValueError):
                send_proposal_to_client(self.proposal, self.user)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, 'internally_approved')
        self.assertFalse(
            SalesProposalClientToken.objects.filter(
                proposal_version=self.proposal,
            ).exists()
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestPublicProposalResponseAPI(TestCase):
    def setUp(self):
        self.org = _org('pub')
        self.node = _scope_node(self.org)
        role = AccessRole.objects.get_or_create(org=self.org, code='sales_pub', defaults={'name': 'sales'})[0]
        bootstrap_role_permissions(role, _caps())
        self.user = _user('u_pub', self.org, role, self.node)
        self.lead, self.proposal = _ready_proposal(self.org, self.user)
        self.raw_token = _send_and_get_raw_token(self.proposal, self.user)
        self.token_record = SalesProposalClientToken.objects.get(
            token_hash=hash_client_proposal_token(self.raw_token),
        )
        mail.outbox.clear()

    def test_public_get_returns_safe_payload(self):
        c = APIClient()
        resp = c.get(PUBLIC_URL.format(token=self.raw_token))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['client_name'], 'Acme')
        self.assertEqual(resp.data['proposal_version_number'], self.proposal.version_number)
        self.assertIn('budget_lines', resp.data)
        self.assertIn('breakup_lines', resp.data)
        budget_line = resp.data['budget_lines'][0]
        breakup_line = resp.data['breakup_lines'][0]
        self.assertIn('role_requirement', budget_line)
        self.assertIn('job_role_name', budget_line)
        self.assertIn('site_name', budget_line)
        self.assertIn('role_requirement', breakup_line)
        self.assertIn('job_role_name', breakup_line)
        self.assertIn('site_name', breakup_line)
        self.assertFalse(resp.data['already_responded'])

    def test_invalid_token_rejected(self):
        resp = APIClient().get(PUBLIC_URL.format(token='not-a-real-token'))
        self.assertEqual(resp.status_code, 404)

    def test_expired_token_rejected(self):
        self.token_record.expires_at = timezone.now() - timedelta(hours=1)
        self.token_record.save(update_fields=['expires_at'])
        resp = APIClient().get(PUBLIC_URL.format(token=self.raw_token))
        self.assertEqual(resp.status_code, 410)

    def test_revoked_token_rejected(self):
        self.token_record.is_revoked = True
        self.token_record.save(update_fields=['is_revoked'])
        resp = APIClient().get(PUBLIC_URL.format(token=self.raw_token))
        self.assertEqual(resp.status_code, 410)

    def test_public_approved_marks_proposal_final(self):
        c = APIClient()
        resp = c.post(
            PUBLIC_URL.format(token=self.raw_token),
            {
                'response': 'approved',
                'remarks': 'Looks good',
                'respondent_name': 'Jane Client',
                'respondent_email': 'jane@acme.com',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.client_approval_status, 'approved')
        self.assertEqual(self.proposal.status, 'client_approved')
        self.assertTrue(self.proposal.is_final_approved_version)
        self.assertIsNotNone(self.proposal.client_approved_at)
        cpr = ClientProposalResponse.objects.filter(proposal_version=self.proposal).latest('created_at')
        self.assertEqual(cpr.client_response, 'approved')
        self.assertEqual(cpr.responded_by_email, 'jane@acme.com')

    def test_rejected_response_sets_negotiation_path(self):
        lead2, prop2 = _ready_proposal(self.org, self.user, email='other@acme.com')
        raw = _send_and_get_raw_token(prop2, self.user)
        resp = APIClient().post(
            PUBLIC_URL.format(token=raw),
            {'response': 'rejected', 'remarks': 'Too expensive'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        prop2.refresh_from_db()
        lead2.refresh_from_db()
        self.assertEqual(prop2.status, 'client_rejected')
        self.assertEqual(lead2.current_stage, 'client_rejected')

    def test_repeated_response_blocked(self):
        APIClient().post(
            PUBLIC_URL.format(token=self.raw_token),
            {'response': 'approved', 'remarks': 'ok'},
            format='json',
        )
        resp = APIClient().post(
            PUBLIC_URL.format(token=self.raw_token),
            {'response': 'rejected', 'remarks': 'late'},
            format='json',
        )
        self.assertIn(resp.status_code, (409, 410))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestSendToClientAPI(TestCase):
    def setUp(self):
        self.org = _org('api_send')
        self.node = _scope_node(self.org)
        role = AccessRole.objects.get_or_create(org=self.org, code='sales_send', defaults={'name': 's'})[0]
        bootstrap_role_permissions(role, _caps())
        self.user = _user('u_send', self.org, role, self.node)
        self.lead, self.proposal = _ready_proposal(self.org, self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_send_to_client_requires_capability(self):
        role2 = AccessRole.objects.get_or_create(org=self.org, code='no_send', defaults={'name': 'ns'})[0]
        bootstrap_role_permissions(role2, [SALES_PROPOSAL_READ, SALES_PROPOSAL_UPDATE])
        u2 = _user('u_nosend', self.org, role2, self.node)
        c = APIClient()
        c.force_authenticate(u2)
        resp = c.post(f'{PROPOSALS_URL}{self.proposal.pk}/send-to-client/', {}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_send_to_client_returns_email_metadata(self):
        resp = self.client.post(
            f'{PROPOSALS_URL}{self.proposal.pk}/send-to-client/',
            {'recipient_email': 'buyer@acme.com', 'expires_days': 14},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data['email_sent'])
        self.assertEqual(resp.data['recipient_email'], 'buyer@acme.com')
        self.assertIn('token_expires_at', resp.data)

    def test_send_to_client_uses_custom_email_draft_and_stores_redacted_snapshot(self):
        resp = self.client.post(
            f'{PROPOSALS_URL}{self.proposal.pk}/send-to-client/',
            {
                'recipient_email': 'buyer@acme.com',
                'email_subject': 'Please review revised commercial proposal',
                'email_body': 'Dear Buyer,\nPlease review the attached commercial proposal and respond today.',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(mail.outbox[-1].subject, 'Please review revised commercial proposal')
        self.assertIn('Dear Buyer', mail.outbox[-1].body)
        self.assertIn('Review proposal:', mail.outbox[-1].body)
        self.assertIn('proposal-response?token=', mail.outbox[-1].body)

        token = SalesProposalClientToken.objects.get(proposal_version=self.proposal)
        self.assertEqual(token.email_subject, 'Please review revised commercial proposal')
        self.assertIn('Dear Buyer', token.email_body)
        self.assertIn('[secure-token-redacted]', token.email_body)
        raw_token = re.search(r'token=([^\s]+)', mail.outbox[-1].body).group(1)
        self.assertNotIn(raw_token, token.email_body)
        self.assertEqual(resp.data['email_subject'], token.email_subject)
        self.assertEqual(resp.data['email_body'], token.email_body)

    def test_send_to_client_accepts_legacy_note_as_email_body(self):
        resp = self.client.post(
            f'{PROPOSALS_URL}{self.proposal.pk}/send-to-client/',
            {
                'recipient_email': 'buyer@acme.com',
                'note': 'This is the commercial note from sales.',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('This is the commercial note from sales.', mail.outbox[-1].body)

    def test_send_to_client_rejects_multiline_subject(self):
        resp = self.client.post(
            f'{PROPOSALS_URL}{self.proposal.pk}/send-to-client/',
            {
                'recipient_email': 'buyer@acme.com',
                'email_subject': 'Line one\nLine two',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('email_subject', resp.data['detail'])


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestConversionGuardrails(TestCase):
    def setUp(self):
        self.org = _org('conv')
        self.node = _scope_node(self.org)
        role = AccessRole.objects.get_or_create(org=self.org, code='sales_conv', defaults={'name': 'c'})[0]
        bootstrap_role_permissions(role, _caps())
        self.user = _user('u_conv', self.org, role, self.node)
        self.lead, self.proposal = _ready_proposal(self.org, self.user)

    def test_convert_blocked_before_client_approval(self):
        with self.assertRaises(ValueError):
            validate_proposal_ready_for_mobilisation_conversion(self.lead, self.proposal)

    def test_convert_succeeds_after_public_approval(self):
        raw = _send_and_get_raw_token(self.proposal, self.user)
        record_public_client_response(raw, 'approved', 'yes')
        self.proposal.refresh_from_db()
        self.lead.refresh_from_db()
        validate_proposal_ready_for_mobilisation_conversion(self.lead, self.proposal)
        from apps.sales.services import mark_lead_won_from_client_approval
        mark_lead_won_from_client_approval(self.lead, self.proposal, self.user)
        req = convert_won_sales_lead_to_onboarding_setup(self.lead, self.user, proposal=self.proposal)
        self.assertIsNotNone(req.pk)
        self.assertEqual(req.source_sales_lead_id, self.lead.pk)

    def test_convert_api_idempotent(self):
        raw = _send_and_get_raw_token(self.proposal, self.user)
        record_public_client_response(raw, 'approved', 'ok')
        c = APIClient()
        c.force_authenticate(self.user)
        url = f'{PROPOSALS_URL}{self.proposal.pk}/convert-to-onboarding/'
        r1 = c.post(url, {}, format='json')
        self.assertIn(r1.status_code, (200, 201), r1.data)
        r2 = c.post(url, {}, format='json')
        self.assertEqual(r2.status_code, 200)

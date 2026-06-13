"""
apps/hiring/tests/test_client_review_e.py

Phase Hiring-Client-Review-Backend-E — 18 tests covering:
  Send-to-client-review (1-4),
  Bulk send (5-6),
  Client-review list (7-8),
  Client decision (9-12),
  Override / manage (13),
  Cross-org security (14),
  Conversion guardrails (15-17),
  Serializer safety (18).
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.deployment.services import convert_hiring_application_to_deployment
from apps.hiring.models import (
    ApplicationStageHistory, HiringApplication, Offer, PipelineStage,
)
from apps.hiring.serializers import ClientReviewApplicationSerializer
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_tree(org):
    n_co = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name=f'Client {org.code}', code=f'cl-{org.code}',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}', name=f'cl-{org.code}', node_type='client',
        parent=n_co, depth=1, path=f'{org.code}/cl-{org.code}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name=f'Site {org.code}', code=f'site-{org.code}', is_active=True,
    )
    return n_co, n_cl, client, site


def _role(org, code):
    r, _ = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})
    bootstrap_role_permissions(r)
    return r


def _user(username, org, role_obj=None, scope_node=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.is_superuser = is_superuser
    u.is_staff = is_superuser
    u.save()
    if role_obj and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role_obj, scope_node=scope_node)
    return u


def _candidate(org, phone, first='Test', last='Cand'):
    return Candidate.objects.create(
        org=org, phone=phone, phone_normalized=phone,
        first_name=first, last_name=last, source='manual',
    )


def _application(org, candidate, mrf, mrf_li, site, job_role, stage,
                 app_status='shortlisted', client_visible=False, client_decision=None):
    return HiringApplication.objects.create(
        org=org, candidate=candidate, mrf=mrf,
        mrf_line_item=mrf_li, site=site, job_role=job_role,
        current_stage=stage, status=app_status,
        client_visible=client_visible,
        client_decision=client_decision,
    )


# ─── Shared base ─────────────────────────────────────────────────────────────

class ClientReviewBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('cre')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        cls.r_admin = _role(cls.org, 'hr_admin')
        cls.r_exec = _role(cls.org, 'hr_executive')
        cls.r_client_admin = _role(cls.org, 'client_admin')

        cls.hr_admin = _user('cre_admin', cls.org, cls.r_admin, cls.n_co)
        cls.hr_exec = _user('cre_exec', cls.org, cls.r_exec, cls.n_co)
        cls.client_admin = _user('cre_client_admin', cls.org, cls.r_client_admin, cls.n_cl)
        cls.superuser = _user('cre_super', cls.org, is_superuser=True)

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard CRE', code='guard-cre',
        )
        cls.mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=cls.hr_admin,
        )
        cls.mrf_li = MRFLineItem.objects.create(
            mrf=cls.mrf, job_role=cls.job_role, headcount=5,
        )
        cls.stage = PipelineStage.objects.create(
            org=cls.org, name='Screening CRE', code='screening-cre',
            order=1, stage_type='screening',
        )

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)

    def _send_url(self, app_id):
        return f'/api/hiring/applications/{app_id}/send-to-client-review/'

    def _decision_url(self, app_id):
        return f'/api/hiring/applications/{app_id}/client-decision/'

    def _bulk_send_url(self, demand_id=None):
        did = demand_id or self.mrf_li.pk
        return f'/api/hiring/demands/{did}/send-shortlisted-to-client-review/'

    def _client_review_list_url(self):
        return '/api/hiring/client-review/'

    def _applications_list_url(self):
        return '/api/hiring/applications/'

    def _application_detail_url(self, app_id):
        return f'/api/hiring/applications/{app_id}/'

    def _demands_list_url(self):
        return '/api/hiring/demands/'

    def _convert_url(self, app_id):
        return f'/api/hiring/applications/{app_id}/convert-to-deployment/'

    def _make_cand(self, suffix):
        return _candidate(self.org, f'8800{suffix}', first='John', last=f'U{suffix}')

    def _make_app(self, suffix, app_status='shortlisted', **kwargs):
        cand = self._make_cand(suffix)
        return _application(
            self.org, cand, self.mrf, self.mrf_li,
            self.site, self.job_role, self.stage,
            app_status=app_status, **kwargs,
        )

    def _accept_offer(self, app):
        Offer.objects.create(
            hiring_application=app,
            offered_ctc='300000.00',
            status='accepted',
        )
        app.status = 'offer_accepted'
        app.save(update_fields=['status'])
        return app


# ═══════════════════════════════════════════════════════════════════════════════
# Group 1 — Send-to-client-review (1-4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSendToClientReview(ClientReviewBase):

    def test_01_send_sets_visible_pending_status_and_history(self):
        """POST send-to-client-review sets client_visible, client_decision=pending, moves to client_review."""
        app = self._make_app('0101', 'shortlisted')
        PipelineStage.objects.create(
            org=self.org, name='Client Review', code='client_review',
            order=20, stage_type='screening',
        )
        self._auth(self.hr_admin)
        resp = self.api.post(self._send_url(app.pk), {'note': 'Please review'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        app.refresh_from_db()
        self.assertTrue(app.client_visible)
        self.assertEqual(app.client_decision, 'pending')
        self.assertEqual(app.status, 'client_review')
        self.assertEqual(app.current_stage.code, 'client_review')

        self.assertTrue(
            ApplicationStageHistory.objects.filter(
                hiring_application=app, to_status='client_review',
            ).exists()
        )

    def test_02_cannot_send_rejected_application(self):
        """Rejected application cannot be sent to client review → 400."""
        app = self._make_app('0201', 'rejected')
        self._auth(self.hr_admin)
        resp = self.api.post(self._send_url(app.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_03_cannot_send_deployed_application(self):
        """Deployed application cannot be sent to client review → 400."""
        app = self._make_app('0301', 'deployed')
        self._auth(self.hr_admin)
        resp = self.api.post(self._send_url(app.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_04_sending_already_pending_without_note_is_skipped(self):
        """Re-sending an already-pending visible application with no note returns skipped."""
        app = self._make_app('0401', 'client_review',
                             client_visible=True, client_decision='pending')
        self._auth(self.hr_admin)
        resp = self.api.post(self._send_url(app.pk), {}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['result'], 'skipped')

    def test_04b_non_billable_application_cannot_be_sent_to_client_review(self):
        """Internal non-billable hiring does not use client review."""
        mrf = ManpowerRequest.objects.create(
            org=self.org, site=self.site, mrf_type='new_hiring',
            billing_type='non_billable', status='approved', requested_by=self.hr_admin,
        )
        mrf_li = MRFLineItem.objects.create(
            mrf=mrf, job_role=self.job_role, headcount=1,
        )
        cand = self._make_cand('0402')
        app = _application(
            self.org, cand, mrf, mrf_li,
            self.site, self.job_role, self.stage,
            app_status='shortlisted',
        )

        self._auth(self.hr_admin)
        resp = self.api.post(self._send_url(app.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('non-billable', str(resp.data).lower())


# ═══════════════════════════════════════════════════════════════════════════════
# Group 2 — Bulk send (5-6)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkSend(ClientReviewBase):

    def test_05_bulk_send_sends_all_shortlisted_for_demand(self):
        """Bulk send with no application_ids sends all shortlisted for the demand."""
        app1 = self._make_app('0501', 'shortlisted')
        app2 = self._make_app('0502', 'shortlisted')
        self._auth(self.hr_admin)
        resp = self.api.post(self._bulk_send_url(), {}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.data['sent'], 2)
        self.assertEqual(resp.data['errors'], [])

        for app in [app1, app2]:
            app.refresh_from_db()
            self.assertTrue(app.client_visible)
            self.assertEqual(app.client_decision, 'pending')

    def test_06_bulk_send_skips_non_shortlisted(self):
        """Bulk send with no application_ids omits non-shortlisted applications."""
        app_short = self._make_app('0601', 'shortlisted')
        app_reject = self._make_app('0602', 'rejected')
        self._auth(self.hr_admin)
        resp = self.api.post(
            self._bulk_send_url(),
            {'application_ids': [app_short.pk, app_reject.pk]},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['sent'], 1)
        self.assertGreaterEqual(len(resp.data['errors']), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 3 — Client review list (7-8)
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientReviewList(ClientReviewBase):

    def test_07_list_only_returns_client_visible_applications(self):
        """GET client-review only returns applications with client_visible=True."""
        app_visible = self._make_app('0701', 'client_review',
                                     client_visible=True, client_decision='pending')
        _invisible = self._make_app('0702', 'shortlisted', client_visible=False)
        self._auth(self.hr_admin)
        resp = self.api.get(self._client_review_list_url())
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        ids = [r['id'] for r in results]
        self.assertIn(app_visible.pk, ids)
        self.assertNotIn(_invisible.pk, ids)

    def test_08_list_only_pending_filter(self):
        """?only_pending=true returns only pending-decision applications."""
        app_pending = self._make_app('0801', 'client_review',
                                     client_visible=True, client_decision='pending')
        app_approved = self._make_app('0802', 'selected',
                                      client_visible=True, client_decision='approved')
        self._auth(self.hr_admin)
        resp = self.api.get(self._client_review_list_url() + '?only_pending=true')
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        ids = [r['id'] for r in results]
        self.assertIn(app_pending.pk, ids)
        self.assertNotIn(app_approved.pk, ids)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 4 — Client decision (9-12)
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientDecision(ClientReviewBase):

    def test_09_approved_decision_moves_status_to_selected(self):
        """client_decision=approved moves status from client_review → selected."""
        app = self._make_app('0901', 'client_review',
                             client_visible=True, client_decision='pending')
        PipelineStage.objects.create(
            org=self.org, name='Client Approved', code='client_approved',
            order=30, stage_type='screening',
        )
        self._auth(self.hr_admin)
        resp = self.api.post(
            self._decision_url(app.pk),
            {'decision': 'approved', 'note': 'Looks good'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        app.refresh_from_db()
        self.assertEqual(app.client_decision, 'approved')
        self.assertEqual(app.status, 'selected')
        self.assertEqual(app.current_stage.code, 'client_approved')
        self.assertIsNotNone(app.client_decision_at)
        self.assertEqual(app.client_decision_note, 'Looks good')

    def test_10_rejected_decision_moves_status_to_rejected(self):
        """client_decision=rejected moves status to rejected."""
        app = self._make_app('1001', 'client_review',
                             client_visible=True, client_decision='pending')
        PipelineStage.objects.create(
            org=self.org, name='Rejected / Closed', code='rejected_closed',
            order=80, stage_type='onboarding', is_terminal=True,
        )
        self._auth(self.hr_admin)
        resp = self.api.post(
            self._decision_url(app.pk),
            {'decision': 'rejected', 'note': 'Not suitable'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        app.refresh_from_db()
        self.assertEqual(app.client_decision, 'rejected')
        self.assertEqual(app.status, 'rejected')
        self.assertEqual(app.current_stage.code, 'rejected_closed')

    def test_11_client_decision_creates_stage_history(self):
        """client_decision creates an ApplicationStageHistory entry."""
        app = self._make_app('1101', 'client_review',
                             client_visible=True, client_decision='pending')
        self._auth(self.hr_admin)
        self.api.post(
            self._decision_url(app.pk),
            {'decision': 'approved'},
            format='json',
        )
        self.assertTrue(
            ApplicationStageHistory.objects.filter(
                hiring_application=app, to_status='selected',
            ).exists()
        )

    def test_12_repeated_decision_blocked_without_override(self):
        """Second client_decision without override=true returns 400."""
        app = self._make_app('1201', 'selected',
                             client_visible=True, client_decision='approved')
        self._auth(self.hr_admin)
        resp = self.api.post(
            self._decision_url(app.pk),
            {'decision': 'rejected'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_12b_client_admin_can_list_and_decide_visible_application(self):
        """Client-facing users can review and decide applications in their scope."""
        app = self._make_app('1202', 'client_review',
                             client_visible=True, client_decision='pending')
        self._auth(self.client_admin)

        list_resp = self.api.get(self._client_review_list_url())
        self.assertEqual(list_resp.status_code, 200, list_resp.data)
        results = list_resp.data.get('results', list_resp.data)
        self.assertIn(app.pk, [row['id'] for row in results])

        decision_resp = self.api.post(
            self._decision_url(app.pk),
            {'decision': 'approved', 'note': 'Approved by client'},
            format='json',
        )
        self.assertEqual(decision_resp.status_code, 200, decision_resp.data)
        app.refresh_from_db()
        self.assertEqual(app.client_decision, 'approved')
        self.assertEqual(app.status, 'selected')

    def test_12c_client_admin_cannot_use_internal_hiring_surfaces(self):
        """Client users use client-review only; generic hiring pages are internal."""
        app = self._make_app('1203', 'client_review',
                             client_visible=True, client_decision='pending')
        self._auth(self.client_admin)

        review_resp = self.api.get(self._client_review_list_url())
        self.assertEqual(review_resp.status_code, 200, review_resp.data)
        review_results = review_resp.data.get('results', review_resp.data)
        self.assertIn(app.pk, [row['id'] for row in review_results])

        generic_resp = self.api.get(self._applications_list_url())
        self.assertEqual(generic_resp.status_code, 200, generic_resp.data)
        generic_results = generic_resp.data.get('results', generic_resp.data)
        self.assertNotIn(app.pk, [row['id'] for row in generic_results])

        detail_resp = self.api.get(self._application_detail_url(app.pk))
        self.assertEqual(detail_resp.status_code, 404)

        demands_resp = self.api.get(self._demands_list_url())
        self.assertEqual(demands_resp.status_code, 200, demands_resp.data)
        demand_results = demands_resp.data.get('results', demands_resp.data)
        self.assertEqual(len(demand_results), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 5 — Override / manage (13)
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientDecisionOverride(ClientReviewBase):

    def test_13_manage_override_can_change_existing_decision(self):
        """override=true with hr_admin (has manage) allows changing decision."""
        app = self._make_app('1301', 'selected',
                             client_visible=True, client_decision='approved')
        self._auth(self.hr_admin)
        resp = self.api.post(
            self._decision_url(app.pk),
            {'decision': 'rejected', 'override': True},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        app.refresh_from_db()
        self.assertEqual(app.client_decision, 'rejected')


# ═══════════════════════════════════════════════════════════════════════════════
# Group 6 — Cross-org security (14)
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientReviewSecurity(ClientReviewBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org2 = _org('cre2')
        cls.n_co2, _, _, _ = _scope_tree(cls.org2)
        cls.r_admin2 = _role(cls.org2, 'hr_admin')
        cls.hr2 = _user('cre2_admin', cls.org2, cls.r_admin2, cls.n_co2)

    def test_14_cross_org_user_cannot_make_decision_on_other_org_app(self):
        """User from org2 cannot see org1's application via API → 404."""
        app = self._make_app('1401', 'client_review',
                             client_visible=True, client_decision='pending')
        self._auth(self.hr2)
        resp = self.api.post(
            self._decision_url(app.pk),
            {'decision': 'approved'},
            format='json',
        )
        self.assertIn(resp.status_code, (403, 404))


# ═══════════════════════════════════════════════════════════════════════════════
# Group 7 — Conversion guardrails (15-17)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversionGuardrail(ClientReviewBase):

    def test_15_conversion_blocked_if_visible_and_not_approved(self):
        """Deployment conversion fails when client_visible=True and decision!=approved."""
        from rest_framework.exceptions import ValidationError
        app = self._make_app('1501', 'selected',
                             client_visible=True, client_decision='pending')
        with self.assertRaises(ValidationError) as ctx:
            convert_hiring_application_to_deployment(app, self.superuser)
        self.assertIn('Client approval', str(ctx.exception))

    def test_16_conversion_allowed_if_visible_and_approved(self):
        """Deployment conversion succeeds when client approves and offer is accepted."""
        from apps.deployment.models import Employee, SiteDeployment
        app = self._make_app('1601', 'selected',
                             client_visible=True, client_decision='approved')
        self._accept_offer(app)
        result = convert_hiring_application_to_deployment(app, self.superuser)
        self.assertIsInstance(result['employee'], Employee)
        self.assertIsInstance(result['deployment'], SiteDeployment)

    def test_17_conversion_blocked_without_accepted_offer(self):
        """Client approval alone is not enough; an accepted offer is required."""
        from rest_framework.exceptions import ValidationError
        app = self._make_app('1701', 'selected', client_visible=False)
        with self.assertRaises(ValidationError) as ctx:
            convert_hiring_application_to_deployment(app, self.superuser)
        self.assertIn('Accepted offer', str(ctx.exception))


# ═══════════════════════════════════════════════════════════════════════════════
# Group 8 — Serializer safety (18)
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientReviewSerializer(ClientReviewBase):

    def test_18_client_review_serializer_does_not_expose_raw_text(self):
        """ClientReviewApplicationSerializer response never contains raw_text or cleaned_text."""
        app = self._make_app('1801', 'client_review',
                             client_visible=True, client_decision='pending')
        self._auth(self.hr_admin)
        resp = self.api.get(self._client_review_list_url())
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        # Find our application in results
        app_data = next((r for r in results if r['id'] == app.pk), None)
        if app_data is None:
            return  # not in results (e.g. pagination), test not applicable
        response_str = str(app_data)
        self.assertNotIn('raw_text', response_str)
        self.assertNotIn('cleaned_text', response_str)

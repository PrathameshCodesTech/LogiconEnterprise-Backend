"""
apps/hiring/tests/test_offer_lifecycle_f.py

Phase Hiring-Offer-Backend-F — 18 tests covering:
  Offer creation (1-3),
  Offer release (4-5),
  Accept / Decline (6-7),
  Withdraw / Expire (8-9),
  Immutability guards (10-12),
  Deployment guardrails (13-14),
  Serializer exposure (15),
  Permission enforcement (16-17),
  Cross-org security (18).
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.deployment.services import convert_hiring_application_to_deployment
from apps.hiring.models import ApplicationStageHistory, HiringApplication, Offer, PipelineStage
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


def _application(org, candidate, mrf, mrf_li, site, job_role, stage, app_status='selected',
                 client_visible=False, client_decision=None):
    return HiringApplication.objects.create(
        org=org, candidate=candidate, mrf=mrf,
        mrf_line_item=mrf_li, site=site, job_role=job_role,
        current_stage=stage, status=app_status,
        client_visible=client_visible,
        client_decision=client_decision,
    )


def _future_date(days=30):
    return (date.today() + timedelta(days=days)).isoformat()


# ─── Shared base ─────────────────────────────────────────────────────────────

class OfferBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('off')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        cls.r_admin = _role(cls.org, 'hr_admin')
        cls.r_manager = _role(cls.org, 'hr_manager')
        cls.r_exec = _role(cls.org, 'hr_executive')

        cls.hr_admin = _user('off_admin', cls.org, cls.r_admin, cls.n_co)
        cls.hr_manager = _user('off_manager', cls.org, cls.r_manager, cls.n_co)
        cls.hr_exec = _user('off_exec', cls.org, cls.r_exec, cls.n_co)

        cls.job_role = JobRole.objects.create(org=cls.org, name='Guard OFF', code='guard-off')
        cls.mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=cls.hr_admin,
        )
        cls.mrf_li = MRFLineItem.objects.create(mrf=cls.mrf, job_role=cls.job_role, headcount=5)
        cls.stage = PipelineStage.objects.create(
            org=cls.org, name='Screening OFF', code='screening-off', order=1, stage_type='screening',
        )

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)

    def _offers_url(self):
        return '/api/hiring/offers/'

    def _offer_url(self, offer_id):
        return f'/api/hiring/offers/{offer_id}/'

    def _offer_action_url(self, offer_id, action):
        return f'/api/hiring/offers/{offer_id}/{action}/'

    def _app_url(self, app_id):
        return f'/api/hiring/applications/{app_id}/'

    def _make_cand(self, suffix):
        return _candidate(self.org, f'9900{suffix}', first='Offer', last=f'Test{suffix}')

    def _make_app(self, suffix, app_status='selected', **kwargs):
        cand = self._make_cand(suffix)
        kwargs.setdefault('client_visible', True)
        kwargs.setdefault('client_decision', 'approved')
        return _application(
            self.org, cand, self.mrf, self.mrf_li,
            self.site, self.job_role, self.stage,
            app_status=app_status, **kwargs,
        )

    def _make_non_billable_app(self, suffix, app_status='selected', **kwargs):
        cand = self._make_cand(suffix)
        mrf = ManpowerRequest.objects.create(
            org=self.org, site=self.site, mrf_type='new_hiring',
            billing_type='non_billable', status='approved', requested_by=self.hr_admin,
        )
        mrf_li = MRFLineItem.objects.create(
            mrf=mrf, job_role=self.job_role, headcount=1,
        )
        return _application(
            self.org, cand, mrf, mrf_li,
            self.site, self.job_role, self.stage,
            app_status=app_status, **kwargs,
        )

    def _make_offer(self, app, status='draft', offered_ctc='500000.00'):
        return Offer.objects.create(
            hiring_application=app,
            offered_ctc=Decimal(offered_ctc),
            salary_breakup={},
            status=status,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Group 1 — Offer creation (1-3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferCreation(OfferBase):

    def test_01_create_offer_for_selected_application(self):
        """POST /offers/ creates a draft offer for a selected application."""
        app = self._make_app('0101', 'selected')
        self._auth(self.hr_admin)
        resp = self.api.post(self._offers_url(), {
            'hiring_application': app.pk,
            'offered_ctc': '600000.00',
            'joining_date': _future_date(30),
            'notes': 'Welcome aboard',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['status'], 'draft')
        self.assertEqual(Decimal(resp.data['offered_ctc']), Decimal('600000.00'))

        offer = Offer.objects.get(hiring_application=app)
        self.assertEqual(offer.status, 'draft')

    def test_02_cannot_create_offer_for_non_selected_application(self):
        """POST /offers/ returns 400 if application is not in 'selected' status."""
        app = self._make_app('0102', 'shortlisted')
        self._auth(self.hr_admin)
        resp = self.api.post(self._offers_url(), {
            'hiring_application': app.pk,
            'offered_ctc': '500000.00',
        }, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('selected', str(resp.data))

    def test_03_cannot_create_offer_if_client_visible_and_not_approved(self):
        """POST /offers/ blocked when client_visible=True but client_decision != 'approved'."""
        app = self._make_app('0103', 'selected', client_visible=True, client_decision='pending')
        self._auth(self.hr_admin)
        resp = self.api.post(self._offers_url(), {
            'hiring_application': app.pk,
            'offered_ctc': '550000.00',
        }, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('approval', str(resp.data).lower())

    def test_03b_billable_offer_requires_client_approval_even_if_not_visible(self):
        """Billable client-site hiring cannot bypass client approval by leaving client_visible false."""
        app = self._make_app(
            '0104', 'selected', client_visible=False, client_decision=None,
        )
        self._auth(self.hr_admin)
        resp = self.api.post(self._offers_url(), {
            'hiring_application': app.pk,
            'offered_ctc': '550000.00',
        }, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('approval', str(resp.data).lower())

    def test_03c_non_billable_offer_does_not_require_client_approval(self):
        """Internal non-billable hiring can create an offer without client review."""
        app = self._make_non_billable_app('0105', 'selected')
        self._auth(self.hr_admin)
        resp = self.api.post(self._offers_url(), {
            'hiring_application': app.pk,
            'offered_ctc': '550000.00',
            'joining_date': _future_date(30),
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['status'], 'draft')


# ═══════════════════════════════════════════════════════════════════════════════
# Group 2 — Release (4-5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferRelease(OfferBase):

    def test_04_release_draft_offer_updates_offer_and_application(self):
        """POST /offers/{id}/release/ moves offer to released and app to offer_released."""
        app = self._make_app('0401', 'selected')
        PipelineStage.objects.create(
            org=self.org, name='Offer', code='offer',
            order=50, stage_type='offer',
        )
        offer = self._make_offer(app, status='draft')
        self._auth(self.hr_admin)

        resp = self.api.post(self._offer_action_url(offer.pk, 'release'), {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], 'released')

        offer.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(offer.status, 'released')
        self.assertEqual(app.status, 'offer_released')
        self.assertEqual(app.current_stage.code, 'offer')
        self.assertIsNotNone(offer.released_at)

    def test_04b_non_billable_offer_release_does_not_require_client_approval(self):
        """Internal non-billable hiring can release an offer without client approval."""
        app = self._make_non_billable_app('0402', 'selected')
        PipelineStage.objects.create(
            org=self.org, name='Offer', code='offer',
            order=50, stage_type='offer',
        )
        offer = self._make_offer(app, status='draft')
        self._auth(self.hr_admin)

        resp = self.api.post(self._offer_action_url(offer.pk, 'release'), {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        offer.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(offer.status, 'released')
        self.assertEqual(app.status, 'offer_released')

    def test_05_release_creates_application_stage_history(self):
        """Releasing an offer creates an ApplicationStageHistory record."""
        app = self._make_app('0501', 'selected')
        offer = self._make_offer(app, status='draft')
        self._auth(self.hr_admin)

        self.api.post(self._offer_action_url(offer.pk, 'release'), {'note': 'Sent to candidate'}, format='json')

        self.assertTrue(
            ApplicationStageHistory.objects.filter(
                hiring_application=app,
                to_status='offer_released',
            ).exists()
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Group 3 — Accept / Decline (6-7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferAcceptDecline(OfferBase):

    def test_06_accept_released_offer_updates_offer_and_application(self):
        """POST /offers/{id}/accept/ moves offer to accepted and app to offer_accepted."""
        app = self._make_app('0601', 'selected')
        PipelineStage.objects.create(
            org=self.org, name='Offer Accepted', code='offer_accepted',
            order=60, stage_type='onboarding',
        )
        offer = self._make_offer(app, status='released')
        app.status = 'offer_released'
        app.save(update_fields=['status'])

        self._auth(self.hr_admin)
        resp = self.api.post(self._offer_action_url(offer.pk, 'accept'), {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], 'accepted')

        offer.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(offer.status, 'accepted')
        self.assertEqual(app.status, 'offer_accepted')
        self.assertEqual(app.current_stage.code, 'offer_accepted')
        self.assertIsNotNone(offer.accepted_at)

    def test_07_decline_released_offer_updates_offer_and_application(self):
        """POST /offers/{id}/decline/ moves offer to declined and app to offer_declined."""
        app = self._make_app('0701', 'selected')
        PipelineStage.objects.create(
            org=self.org, name='Rejected / Closed', code='rejected_closed',
            order=80, stage_type='onboarding', is_terminal=True,
        )
        offer = self._make_offer(app, status='released')
        app.status = 'offer_released'
        app.save(update_fields=['status'])

        self._auth(self.hr_admin)
        resp = self.api.post(self._offer_action_url(offer.pk, 'decline'), {'note': 'CTC too low'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], 'declined')

        offer.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(offer.status, 'declined')
        self.assertEqual(app.status, 'offer_declined')
        self.assertEqual(app.current_stage.code, 'rejected_closed')
        self.assertIsNotNone(offer.declined_at)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 4 — Withdraw / Expire (8-9)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferWithdrawExpire(OfferBase):

    def test_08_withdraw_draft_offer_works(self):
        """POST /offers/{id}/withdraw/ on a draft offer sets status to withdrawn."""
        app = self._make_app('0801', 'selected')
        offer = self._make_offer(app, status='draft')

        self._auth(self.hr_admin)
        resp = self.api.post(self._offer_action_url(offer.pk, 'withdraw'), {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], 'withdrawn')

        offer.refresh_from_db()
        self.assertEqual(offer.status, 'withdrawn')

    def test_09_withdraw_released_offer_moves_app_back_to_selected(self):
        """Withdrawing a released offer returns the application to 'selected' status."""
        app = self._make_app('0901', 'offer_released')
        offer = self._make_offer(app, status='released')

        self._auth(self.hr_admin)
        resp = self.api.post(self._offer_action_url(offer.pk, 'withdraw'), {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        offer.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(offer.status, 'withdrawn')
        self.assertEqual(app.status, 'selected')

    def test_09b_expire_released_offer_moves_app_back_to_selected(self):
        """POST /offers/{id}/expire/ on a released offer moves application back to 'selected'."""
        app = self._make_app('0902', 'offer_released')
        offer = self._make_offer(app, status='released')

        self._auth(self.hr_admin)
        resp = self.api.post(self._offer_action_url(offer.pk, 'expire'), {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        offer.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(offer.status, 'expired')
        self.assertEqual(app.status, 'selected')


# ═══════════════════════════════════════════════════════════════════════════════
# Group 5 — Immutability guards (10-12)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferImmutability(OfferBase):

    def test_10_cannot_update_accepted_offer(self):
        """PATCH /offers/{id}/ returns 400 when offer is in an immutable status."""
        app = self._make_app('1001', 'offer_accepted')
        offer = self._make_offer(app, status='accepted')

        self._auth(self.hr_admin)
        resp = self.api.patch(self._offer_url(offer.pk), {'notes': 'Changed'}, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('accepted', str(resp.data))

    def test_11_cannot_accept_draft_offer(self):
        """POST /offers/{id}/accept/ returns 400 when offer is still a draft."""
        app = self._make_app('1101', 'selected')
        offer = self._make_offer(app, status='draft')

        self._auth(self.hr_admin)
        resp = self.api.post(self._offer_action_url(offer.pk, 'accept'), {}, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('released', str(resp.data))

    def test_12_cannot_decline_accepted_offer(self):
        """POST /offers/{id}/decline/ on an already accepted offer returns 400."""
        app = self._make_app('1201', 'offer_accepted')
        offer = self._make_offer(app, status='accepted')

        self._auth(self.hr_admin)
        resp = self.api.post(self._offer_action_url(offer.pk, 'decline'), {}, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('released', str(resp.data))


# ═══════════════════════════════════════════════════════════════════════════════
# Group 6 — Deployment guardrails (13-14)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeploymentOfferGuardrails(OfferBase):

    def _convert_url(self, app_id):
        return f'/api/hiring/applications/{app_id}/convert-to-deployment/'

    def test_13_deployment_blocked_when_offer_exists_but_not_accepted(self):
        """convert-to-deployment is blocked when an offer exists but is not accepted."""
        app = self._make_app('1301', 'offer_released')
        self._make_offer(app, status='released')

        self._auth(self.hr_admin)
        resp = self.api.post(self._convert_url(app.pk), {}, format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('accepted', str(resp.data).lower())

    def test_14_deployment_allowed_when_offer_accepted(self):
        """convert-to-deployment succeeds when offer.status == 'accepted'."""
        app = self._make_app('1401', 'offer_accepted')
        self._make_offer(app, status='accepted')

        self._auth(self.hr_admin)
        resp = self.api.post(self._convert_url(app.pk), {}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn('employee', resp.data)
        self.assertIn('deployment', resp.data)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 7 — Serializer exposure (15)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferSerializerExposure(OfferBase):

    def test_15_application_read_serializer_exposes_offer_status(self):
        """GET /applications/{id}/ includes offer_status, offered_ctc, offer_joining_date."""
        app = self._make_app('1501', 'selected')
        joining = _future_date(45)
        Offer.objects.create(
            hiring_application=app,
            offered_ctc=Decimal('750000.00'),
            joining_date=date.today() + timedelta(days=45),
            status='draft',
        )
        self._auth(self.hr_admin)
        resp = self.api.get(self._app_url(app.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['offer_status'], 'draft')
        self.assertEqual(Decimal(resp.data['offered_ctc']), Decimal('750000.00'))
        self.assertIsNotNone(resp.data['offer_joining_date'])

    def test_15b_application_read_serializer_offer_fields_null_when_no_offer(self):
        """GET /applications/{id}/ returns null offer fields when no offer exists."""
        app = self._make_app('1502', 'selected')
        self._auth(self.hr_admin)
        resp = self.api.get(self._app_url(app.pk))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data['offer_status'])
        self.assertIsNone(resp.data['offered_ctc'])
        self.assertIsNone(resp.data['offer_joining_date'])


# ═══════════════════════════════════════════════════════════════════════════════
# Group 8 — Permission enforcement (16-17)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferPermissions(OfferBase):

    def test_16_unauthenticated_cannot_create_offer(self):
        """Unauthenticated POST /offers/ returns 401."""
        app = self._make_app('1601', 'selected')
        resp = self.api.post(self._offers_url(), {
            'hiring_application': app.pk,
            'offered_ctc': '500000.00',
        }, format='json')
        self.assertEqual(resp.status_code, 401)

    def test_17_hr_executive_without_offer_approve_cap_cannot_release(self):
        """hr_executive can draft offers but cannot release them without offer.approve."""
        app = self._make_app('1701', 'selected')
        offer = self._make_offer(app, status='draft')
        self._auth(self.hr_exec)
        resp = self.api.post(self._offer_action_url(offer.pk, 'release'), {}, format='json')
        self.assertIn(resp.status_code, [403, 404])


# ═══════════════════════════════════════════════════════════════════════════════
# Group 9 — Cross-org security (18)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferCrossOrg(OfferBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org2 = _org('off2')
        cls.n_co2, cls.n_cl2, cls.client2, cls.site2 = _scope_tree(cls.org2)
        cls.r_admin2 = _role(cls.org2, 'hr_admin')
        cls.hr_admin2 = _user('off2_admin', cls.org2, cls.r_admin2, cls.n_co2)

    def test_18_cross_org_offer_creation_blocked(self):
        """An hr_admin from org2 cannot create an offer for an application in org1."""
        app = self._make_app('1801', 'selected')  # belongs to self.org
        self._auth(self.hr_admin2)
        resp = self.api.post(self._offers_url(), {
            'hiring_application': app.pk,
            'offered_ctc': '500000.00',
        }, format='json')
        self.assertIn(resp.status_code, [400, 403, 404])

"""
apps/hiring/tests/test_resume_pool_link_a.py

Phase Resume-Pool-Link-A — 8 API tests covering:
  01. Create hiring application with only mrf_line_item (mrf auto-derived)
  02. Duplicate candidate+line_item blocked with "already linked" message
  03. Unapproved MRF blocked
  04. Cross-org candidate blocked
  05. Cross-org line item blocked (user's candidate in wrong org)
  06. Candidate search by skill (?skill= on /api/talent/candidates/)
  07. Candidate search by min/max experience
  08. candidate-pool endpoint excludes already-linked candidates
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.hiring.models import HiringApplication, PipelineStage
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate, CandidateSkill


# ─── Helpers ─────────────────────────────────────────────────────────────────

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
    return n_co, client, site


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


def _candidate(org, phone, first='Alice', last='Test', lifecycle_status='active',
               experience_years=None):
    return Candidate.objects.create(
        org=org, phone=phone, phone_normalized=phone,
        first_name=first, last_name=last, source='manual',
        lifecycle_status=lifecycle_status,
        total_experience_years=experience_years,
    )


def _mrf(org, site, user, status='approved'):
    return ManpowerRequest.objects.create(
        org=org, site=site, mrf_type='new_hiring',
        billing_type='billable', status=status, requested_by=user,
    )


def _stage(org, code, order=0, stage_type='sourcing'):
    return PipelineStage.objects.create(
        org=org, name=code, code=code, order=order, stage_type=stage_type,
    )


# ─── Base ────────────────────────────────────────────────────────────────────

class PoolLinkBase(TestCase):
    """
    Two orgs (rpl-a, rpl-b) so cross-org checks can be exercised.
    hr_admin on org-a has hiring_application.create; hr_b on org-b does too.
    """

    @classmethod
    def setUpTestData(cls):
        # org-a
        cls.org_a = _org('rpl-a')
        cls.n_co_a, cls.client_a, cls.site_a = _scope_tree(cls.org_a)
        cls.r_hr_a = _role(cls.org_a, 'hr_admin')
        cls.hr_a = _user('rpl_hr_a', cls.org_a, cls.r_hr_a, cls.n_co_a)

        # org-b
        cls.org_b = _org('rpl-b')
        cls.n_co_b, cls.client_b, cls.site_b = _scope_tree(cls.org_b)
        cls.r_hr_b = _role(cls.org_b, 'hr_admin')
        cls.hr_b = _user('rpl_hr_b', cls.org_b, cls.r_hr_b, cls.n_co_b)

        cls.job_role = JobRole.objects.create(
            org=cls.org_a, name='Security Guard RPL', code='guard-rpl',
        )

        cls.approved_mrf = _mrf(cls.org_a, cls.site_a, cls.hr_a, status='approved')
        cls.draft_mrf = _mrf(cls.org_a, cls.site_a, cls.hr_a, status='draft')

        cls.mrf_li = MRFLineItem.objects.create(
            mrf=cls.approved_mrf, job_role=cls.job_role, headcount=3,
        )
        cls.draft_li = MRFLineItem.objects.create(
            mrf=cls.draft_mrf, job_role=cls.job_role, headcount=2,
        )

        # MRF in org-b
        cls.job_role_b = JobRole.objects.create(
            org=cls.org_b, name='Guard RPL-B', code='guard-rpl-b',
        )
        cls.approved_mrf_b = _mrf(cls.org_b, cls.site_b, cls.hr_b, status='approved')
        cls.mrf_li_b = MRFLineItem.objects.create(
            mrf=cls.approved_mrf_b, job_role=cls.job_role_b, headcount=2,
        )

        cls.stage = _stage(cls.org_a, 'sourcing-rpl', order=0)

        cls.cand_a = _candidate(cls.org_a, '9700000001')
        cls.cand_b = _candidate(cls.org_b, '9700000002')

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestResumePoolLinkA(PoolLinkBase):

    # ── test_01 ──────────────────────────────────────────────────────────────

    def test_01_create_app_with_only_mrf_line_item_derives_mrf(self):
        """POST with only mrf_line_item should create app and auto-fill mrf/site/job_role/org."""
        self._auth(self.hr_a)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': self.cand_a.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp.status_code, 201, resp.data)
        data = resp.data
        self.assertEqual(data['org'], self.org_a.pk)
        self.assertEqual(data['mrf'], self.approved_mrf.pk)
        self.assertEqual(data['site'], self.site_a.pk)
        self.assertEqual(data['job_role'], self.job_role.pk)
        self.assertIsNotNone(data['current_stage'])

    # ── test_02 ──────────────────────────────────────────────────────────────

    def test_02_duplicate_candidate_line_item_returns_already_linked_message(self):
        """Second POST for same candidate + line_item returns 400 with 'already linked' message."""
        self._auth(self.hr_a)
        cand = _candidate(self.org_a, '9700000010')
        self.api.post('/api/hiring/applications/', {
            'candidate': cand.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': cand.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp.status_code, 400)
        err_text = str(resp.data)
        self.assertIn('already linked', err_text)

    # ── test_03 ──────────────────────────────────────────────────────────────

    def test_03_unapproved_mrf_returns_400(self):
        """Linking to a line item under a draft MRF is rejected."""
        self._auth(self.hr_a)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': self.cand_a.pk,
            'mrf_line_item': self.draft_li.pk,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('approved', str(resp.data).lower())

    # ── test_04 ──────────────────────────────────────────────────────────────

    def test_04_cross_org_candidate_blocked(self):
        """Candidate from org-b cannot be linked to org-a's MRF line item."""
        self._auth(self.hr_a)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': self.cand_b.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp.status_code, 400)

    # ── test_05 ──────────────────────────────────────────────────────────────

    def test_05_cross_org_user_candidate_blocked(self):
        """hr_b (org-b) cannot create an application with org-a's candidate."""
        self._auth(self.hr_b)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': self.cand_a.pk,
            'mrf_line_item': self.mrf_li_b.pk,
        })
        self.assertEqual(resp.status_code, 400)

    # ── test_06 ──────────────────────────────────────────────────────────────

    def test_06_candidate_search_by_skill(self):
        """GET /api/talent/candidates/?skill=python returns only candidates with that skill."""
        cand_py = _candidate(self.org_a, '9700000020', first='Pythonista')
        CandidateSkill.objects.create(
            candidate=cand_py,
            skill_name='Python',
            normalized_skill_name='python',
            source='manual',
        )
        cand_java = _candidate(self.org_a, '9700000021', first='JavaDev')
        CandidateSkill.objects.create(
            candidate=cand_java,
            skill_name='Java',
            normalized_skill_name='java',
            source='manual',
        )

        self._auth(self.hr_a)
        resp = self.api.get('/api/talent/candidates/?skill=python')
        self.assertEqual(resp.status_code, 200)
        ids = [c['id'] for c in resp.data['results']]
        self.assertIn(cand_py.pk, ids)
        self.assertNotIn(cand_java.pk, ids)

    # ── test_07 ──────────────────────────────────────────────────────────────

    def test_07_candidate_search_by_min_max_experience(self):
        """?min_experience=3&max_experience=7 filters by total_experience_years."""
        cand_junior = _candidate(self.org_a, '9700000030', first='Junior',
                                 experience_years=Decimal('1.5'))
        cand_mid = _candidate(self.org_a, '9700000031', first='Mid',
                              experience_years=Decimal('5.0'))
        cand_senior = _candidate(self.org_a, '9700000032', first='Senior',
                                 experience_years=Decimal('10.0'))

        self._auth(self.hr_a)
        resp = self.api.get('/api/talent/candidates/?min_experience=3&max_experience=7')
        self.assertEqual(resp.status_code, 200)
        ids = [c['id'] for c in resp.data['results']]
        self.assertNotIn(cand_junior.pk, ids)
        self.assertIn(cand_mid.pk, ids)
        self.assertNotIn(cand_senior.pk, ids)

    # ── test_08 ──────────────────────────────────────────────────────────────

    def test_08_candidate_pool_endpoint_excludes_linked_candidates(self):
        """GET /api/hiring/demands/{id}/candidate-pool/ excludes already-linked candidates."""
        cand_linked = _candidate(self.org_a, '9700000040', first='Linked',
                                 lifecycle_status='active')
        cand_free = _candidate(self.org_a, '9700000041', first='Free',
                               lifecycle_status='active')

        HiringApplication.objects.create(
            org=self.org_a,
            candidate=cand_linked,
            mrf=self.approved_mrf,
            mrf_line_item=self.mrf_li,
            site=self.site_a,
            job_role=self.job_role,
            current_stage=self.stage,
        )

        self._auth(self.hr_a)
        resp = self.api.get(f'/api/hiring/demands/{self.mrf_li.pk}/candidate-pool/')
        self.assertEqual(resp.status_code, 200)

        results = resp.data.get('results', resp.data)
        ids = [c['id'] for c in results]
        self.assertNotIn(cand_linked.pk, ids)
        self.assertIn(cand_free.pk, ids)

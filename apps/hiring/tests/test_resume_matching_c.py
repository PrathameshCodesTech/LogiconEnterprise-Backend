"""
apps/hiring/tests/test_resume_matching_c.py

Phase Resume-Matching-Backend-C — 18 tests covering:
  Scoring functions (1-6), rank_candidates service (7-9),
  candidate-pool endpoint ranked (10-13), candidate-pool flat (14-15),
  shortlist-candidate endpoint (16-18).
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.hiring.matching.scoring import (
    score_role, score_skills, score_experience,
    score_location, score_availability, compute_total_score,
)
from apps.hiring.matching.services import rank_candidates
from apps.hiring.models import (
    CandidateMatchResult, HiringApplication, PipelineStage,
)
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate, CandidateSkill, CandidateExperience


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
        city='Mumbai',
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


def _candidate(org, phone, first='Test', last='User', role='', location='',
               exp_years=None, lifecycle='active', availability='available_now'):
    return Candidate.objects.create(
        org=org, phone=phone, phone_normalized=phone,
        first_name=first, last_name=last, source='manual',
        current_role=role,
        current_location=location,
        total_experience_years=Decimal(str(exp_years)) if exp_years is not None else None,
        lifecycle_status=lifecycle,
        availability_status=availability,
    )


def _skill(candidate, name):
    return CandidateSkill.objects.create(
        candidate=candidate,
        skill_name=name,
        normalized_skill_name=name.lower(),
    )


def _experience(candidate, title='', years=None):
    return CandidateExperience.objects.create(
        candidate=candidate,
        job_title=title,
        duration_months=int(years * 12) if years is not None else None,
    )


def _stage(org, code, order=0, stage_type='screening'):
    return PipelineStage.objects.create(
        org=org, name=code, code=code, order=order, stage_type=stage_type,
    )


# ─── Dummy objects for unit testing pure functions ────────────────────────────

class _FakeCandidate:
    def __init__(self, role='', location='', exp_years=None,
                 availability='available_now', notice_period=None,
                 lifecycle='active', has_email=True):
        self.current_role = role
        self.current_location = location
        self.preferred_location = ''
        self.total_experience_years = Decimal(str(exp_years)) if exp_years is not None else None
        self.availability_status = availability
        self.notice_period_days = notice_period
        self.lifecycle_status = lifecycle
        self.email = 'a@b.com' if has_email else None
        self.is_duplicate = False
        self.do_not_contact = False


class _FakeJobRole:
    def __init__(self, name):
        self.name = name


# ═══════════════════════════════════════════════════════════════════════════════
# Group 1 — Scoring unit tests (1-6)
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoringFunctions(TestCase):

    def test_01_score_role_exact_match(self):
        """Candidate current_role contains job_role name → 100."""
        cand = _FakeCandidate(role='Security Guard')
        jr = _FakeJobRole('Security Guard')
        result = score_role(cand, jr, [])
        self.assertEqual(result, 100.0)

    def test_02_score_role_no_match(self):
        """No role match at all → 30."""
        cand = _FakeCandidate(role='Chef')
        jr = _FakeJobRole('Security Guard')
        result = score_role(cand, jr, [])
        self.assertEqual(result, 30.0)

    def test_03_score_skills_all_matched(self):
        """All required skills present → 100."""
        score, matched, missing, extra = score_skills({'python', 'django'}, ['python', 'django'])
        self.assertEqual(score, 100.0)
        self.assertCountEqual(matched, ['python', 'django'])
        self.assertEqual(missing, [])

    def test_04_score_skills_none_required(self):
        """No required skills → neutral 70."""
        score, matched, missing, extra = score_skills({'python'}, [])
        self.assertEqual(score, 70.0)
        self.assertEqual(matched, [])
        self.assertEqual(missing, [])

    def test_05_score_experience_with_min_exp_met(self):
        """Candidate meets min_exp → score above floor."""
        cand = _FakeCandidate(exp_years=3)
        result = score_experience(cand, [], min_exp=Decimal('2'))
        self.assertGreater(result, 10.0)

    def test_06_score_availability_available_now(self):
        """availability_status='available_now' → 100."""
        cand = _FakeCandidate(availability='available_now')
        from apps.hiring.matching.scoring import score_availability
        result = score_availability(cand)
        self.assertEqual(result, 100.0)

    def test_06b_compute_total_score_bounded(self):
        """Total score is between 0 and 100 for any valid inputs."""
        total = compute_total_score(100, 100, 100, 100, 100, 100)
        self.assertAlmostEqual(total, 100.0, places=1)
        total_zero = compute_total_score(0, 0, 0, 0, 0, 0)
        self.assertAlmostEqual(total_zero, 0.0, places=1)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 2 — rank_candidates service (7-9)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRankCandidatesService(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('rmc')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)
        cls.hr = _user('rmc_hr', cls.org, is_superuser=True)
        cls.job_role = JobRole.objects.create(org=cls.org, name='Guard', code='guard-rmc')
        cls.mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=cls.hr,
        )
        cls.demand = MRFLineItem.objects.create(
            mrf=cls.mrf, job_role=cls.job_role, headcount=3,
        )

    def test_07_returns_empty_for_no_candidates(self):
        """rank_candidates with empty queryset returns []."""
        qs = Candidate.objects.none()
        results = rank_candidates(self.demand, qs, {}, save_results=False)
        self.assertEqual(results, [])

    def test_08_sorted_descending_by_score(self):
        """Higher-scoring candidates appear first."""
        c_high = _candidate(
            self.org, '1111111111', role='Guard', location='Mumbai',
            exp_years=4, availability='available_now',
        )
        _skill(c_high, 'security')
        _experience(c_high, 'Guard', years=4)
        c_high.email = 'high@test.com'
        c_high.save()

        c_low = _candidate(self.org, '2222222222', role='Chef', location='Delhi', exp_years=0)

        qs = Candidate.objects.filter(pk__in=[c_high.pk, c_low.pk])
        results = rank_candidates(self.demand, qs, {'skills': 'security'}, save_results=False)
        self.assertEqual(len(results), 2)
        self.assertGreater(results[0]['score'], results[1]['score'])
        self.assertEqual(results[0]['candidate'].pk, c_high.pk)

    def test_09_save_results_creates_match_result_rows(self):
        """save_results=True upserts CandidateMatchResult."""
        c = _candidate(self.org, '3333333333', role='Guard')
        _skill(c, 'patrol')
        qs = Candidate.objects.filter(pk=c.pk)
        rank_candidates(self.demand, qs, {}, save_results=True, user=self.hr)
        self.assertTrue(
            CandidateMatchResult.objects.filter(candidate=c, mrf_line_item=self.demand).exists()
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Group 3 — candidate-pool endpoint ranked (10-13)
# ═══════════════════════════════════════════════════════════════════════════════

class MatchingEndpointBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('mep')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        cls.r_admin = _role(cls.org, 'hr_admin')
        cls.hr = _user('mep_hr', cls.org, cls.r_admin, cls.n_co)
        cls.superuser = _user('mep_super', cls.org, is_superuser=True)

        cls.job_role = JobRole.objects.create(org=cls.org, name='Guard', code='guard-mep')
        cls.mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=cls.hr,
        )
        cls.demand = MRFLineItem.objects.create(
            mrf=cls.mrf, job_role=cls.job_role, headcount=5,
        )
        _stage(cls.org, 'sourcing-mep', order=0, stage_type='sourcing')

        cls.cand_a = _candidate(
            cls.org, '4444444441', role='Guard', location='Mumbai',
            exp_years=3, availability='available_now',
        )
        cls.cand_a.email = 'a@mep.com'
        cls.cand_a.save()
        _skill(cls.cand_a, 'security')
        _experience(cls.cand_a, 'Guard', years=3)

        cls.cand_b = _candidate(cls.org, '4444444442', role='Chef', location='Delhi')
        cls.cand_b.email = 'b@mep.com'
        cls.cand_b.save()

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)

    def _pool_url(self, demand_id=None):
        did = demand_id or self.demand.pk
        return f'/api/hiring/demands/{did}/candidate-pool/'

    def _shortlist_url(self, demand_id=None):
        did = demand_id or self.demand.pk
        return f'/api/hiring/demands/{did}/shortlist-candidate/'


class TestCandidatePoolRanked(MatchingEndpointBase):

    def test_10_ranked_response_has_score_fields(self):
        """ranked=true (default) returns score, match_status, score_breakdown."""
        self._auth(self.superuser)
        resp = self.api.get(self._pool_url())
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertIn('score', first)
        self.assertIn('match_status', first)
        self.assertIn('score_breakdown', first)
        self.assertIn('matched_skills', first)
        self.assertIn('missing_skills', first)
        self.assertIn('candidate', first)

    def test_11_min_score_filter(self):
        """?min_score=90 excludes low-scoring candidates."""
        self._auth(self.superuser)
        resp = self.api.get(self._pool_url() + '?min_score=90')
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        for r in results:
            self.assertGreaterEqual(r['score'], 90.0)

    def test_12_save_results_false_does_not_persist(self):
        """?save_results=false skips CandidateMatchResult creation."""
        CandidateMatchResult.objects.filter(mrf_line_item=self.demand).delete()
        self._auth(self.superuser)
        self.api.get(self._pool_url() + '?save_results=false')
        # No new rows should be saved
        self.assertFalse(
            CandidateMatchResult.objects.filter(
                candidate=self.cand_b, mrf_line_item=self.demand,
            ).exists()
        )

    def test_13_already_linked_excluded(self):
        """Candidates already linked to this demand are excluded."""
        stage = PipelineStage.objects.filter(org=self.org).first()
        linked = _candidate(self.org, '5555555551', lifecycle='active')
        linked.email = 'linked@mep.com'
        linked.save()
        HiringApplication.objects.create(
            org=self.org, candidate=linked, mrf=self.mrf,
            mrf_line_item=self.demand, site=self.site, job_role=self.job_role,
            current_stage=stage, status='shortlisted',
        )
        self._auth(self.superuser)
        resp = self.api.get(self._pool_url())
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        candidate_ids = [r['candidate']['id'] for r in results]
        self.assertNotIn(linked.pk, candidate_ids)


class TestCandidatePoolFlat(MatchingEndpointBase):

    def test_14_ranked_false_returns_flat_list(self):
        """?ranked=false returns plain CandidateSerializer objects (no score field)."""
        self._auth(self.superuser)
        resp = self.api.get(self._pool_url() + '?ranked=false')
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        if results:
            self.assertNotIn('score', results[0])
            self.assertIn('id', results[0])

    def test_15_ranked_false_location_filter(self):
        """?ranked=false&location=Mumbai returns only Mumbai candidates."""
        self._auth(self.superuser)
        resp = self.api.get(self._pool_url() + '?ranked=false&location=Mumbai')
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get('results', resp.data)
        ids = [r['id'] for r in results]
        self.assertIn(self.cand_a.pk, ids)
        self.assertNotIn(self.cand_b.pk, ids)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 4 — shortlist-candidate endpoint (16-18)
# ═══════════════════════════════════════════════════════════════════════════════

class TestShortlistCandidate(MatchingEndpointBase):

    def test_16_shortlist_creates_application(self):
        """POST shortlist-candidate creates HiringApplication with status=shortlisted."""
        new_cand = _candidate(self.org, '6666666661', lifecycle='active')
        new_cand.email = 'newcand@mep.com'
        new_cand.save()

        self._auth(self.superuser)
        resp = self.api.post(self._shortlist_url(), {'candidate': new_cand.pk}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['status'], 'shortlisted')
        self.assertEqual(resp.data['candidate'], new_cand.pk)
        self.assertTrue(
            HiringApplication.objects.filter(
                candidate=new_cand, mrf_line_item=self.demand,
            ).exists()
        )

    def test_17_shortlist_duplicate_rejected(self):
        """Shortlisting same candidate twice returns 400."""
        dup_cand = _candidate(self.org, '7777777771', lifecycle='active')
        dup_cand.email = 'dup@mep.com'
        dup_cand.save()

        self._auth(self.superuser)
        self.api.post(self._shortlist_url(), {'candidate': dup_cand.pk}, format='json')
        resp2 = self.api.post(self._shortlist_url(), {'candidate': dup_cand.pk}, format='json')
        self.assertEqual(resp2.status_code, 400)

    def test_18_shortlist_blacklisted_rejected(self):
        """Shortlisting a blacklisted candidate returns 400."""
        bl_cand = _candidate(self.org, '8888888881', lifecycle='active')
        bl_cand.is_blacklisted = True
        bl_cand.save()

        self._auth(self.superuser)
        resp = self.api.post(self._shortlist_url(), {'candidate': bl_cand.pk}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('blacklisted', str(resp.data).lower())

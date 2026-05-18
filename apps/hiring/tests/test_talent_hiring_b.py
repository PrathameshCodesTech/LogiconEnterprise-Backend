"""
apps/hiring/tests/test_talent_hiring_b.py

Phase Talent-Hiring-B — 30 API tests covering:
  Candidate CRUD (1-5), Resume upload (6-10), Pipeline stages (11),
  HiringApplication create (12-18), move-stage (19-22),
  Hiring demand (23-25), Match results (26-28), Regression (29-30).
"""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.hiring.models import (
    ApplicationStageHistory, CandidateMatchResult,
    HiringApplication, PipelineStage,
)
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate, Resume


# ─── Shared helpers ───────────────────────────────────────────────────────────

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


def _candidate(org, phone='9876543210', first='Alice', last='Test'):
    return Candidate.objects.create(
        org=org, phone=phone, phone_normalized=phone,
        first_name=first, last_name=last, source='manual',
    )


def _mrf(org, site, user, status='approved'):
    return ManpowerRequest.objects.create(
        org=org, site=site, mrf_type='new_hiring',
        billing_type='billable', status=status, requested_by=user,
    )


def _stage(org, code, order=0, stage_type='screening', is_terminal=False):
    return PipelineStage.objects.create(
        org=org, name=code, code=code,
        order=order, stage_type=stage_type, is_terminal=is_terminal,
    )


# ─── Base test class ──────────────────────────────────────────────────────────

class HiringBBase(TestCase):
    """
    Shared setup for Phase Talent-Hiring-B API tests.
    Creates one org, scope tree, hr_admin + hr_executive + field_supervisor users,
    job role, approved MRF, and default pipeline stages.
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('thb')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        cls.r_hr_admin = _role(cls.org, 'hr_admin')
        cls.r_hr_exec = _role(cls.org, 'hr_executive')
        # site_supervisor has no candidate.read, resume.upload, or hiring_application.create
        cls.r_no_cand = _role(cls.org, 'site_supervisor')

        cls.hr_admin = _user('thb_hr_admin', cls.org, cls.r_hr_admin, cls.n_co)
        cls.hr_exec = _user('thb_hr_exec', cls.org, cls.r_hr_exec, cls.n_co)
        cls.no_perm = _user('thb_no_perm', cls.org, cls.r_no_cand, cls.n_co)
        cls.superuser = _user('thb_super', cls.org, is_superuser=True)

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard THB', code='guard-thb',
        )
        cls.approved_mrf = _mrf(cls.org, cls.site, cls.hr_admin, status='approved')
        cls.draft_mrf = _mrf(cls.org, cls.site, cls.hr_admin, status='draft')
        cls.mrf_li = MRFLineItem.objects.create(
            mrf=cls.approved_mrf, job_role=cls.job_role, headcount=5,
        )

        cls.stage_sourcing = _stage(cls.org, 'sourcing-thb', order=0, stage_type='sourcing')
        cls.stage_screening = _stage(cls.org, 'screening-thb', order=1, stage_type='screening')
        cls.stage_offer = _stage(
            cls.org, 'offer-thb', order=9, stage_type='offer', is_terminal=True,
        )

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)


# ─── Tests 1–5: Candidate CRUD ────────────────────────────────────────────────

class TestCandidateAPI(HiringBBase):

    # test_01
    def test_01_unauthenticated_candidate_list_returns_401(self):
        resp = self.api.get('/api/talent/candidates/')
        self.assertEqual(resp.status_code, 401)

    # test_02
    def test_02_no_candidate_read_capability_returns_403(self):
        # field_supervisor has no candidate.read
        self._auth(self.no_perm)
        resp = self.api.get('/api/talent/candidates/')
        self.assertEqual(resp.status_code, 403)

    # test_03
    def test_03_create_candidate_assigns_actor_org(self):
        self._auth(self.hr_exec)
        resp = self.api.post('/api/talent/candidates/', {
            'first_name': 'Bob',
            'last_name': 'Builder',
            'phone': '9800000001',
            'source': 'manual',
        })
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['org'], self.org.pk)
        self.assertEqual(resp.data['phone_normalized'], '9800000001')

    # test_04
    def test_04_duplicate_phone_returns_400(self):
        self._auth(self.hr_exec)
        self.api.post('/api/talent/candidates/', {
            'first_name': 'Dup',
            'last_name': 'One',
            'phone': '9800000002',
            'source': 'manual',
        })
        resp = self.api.post('/api/talent/candidates/', {
            'first_name': 'Dup',
            'last_name': 'Two',
            'phone': '9800000002',
            'source': 'manual',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('phone', resp.data)

    # test_05
    def test_05_patch_lifecycle_status(self):
        self._auth(self.hr_exec)
        create_resp = self.api.post('/api/talent/candidates/', {
            'first_name': 'Patch',
            'last_name': 'Candidate',
            'phone': '9800000003',
            'source': 'manual',
        })
        self.assertEqual(create_resp.status_code, 201)
        cid = create_resp.data['id']

        patch_resp = self.api.patch(f'/api/talent/candidates/{cid}/', {
            'lifecycle_status': 'inactive',
        })
        self.assertEqual(patch_resp.status_code, 200, patch_resp.data)
        self.assertEqual(patch_resp.data['lifecycle_status'], 'inactive')


# ─── Tests 6–10: Resume upload ────────────────────────────────────────────────

class TestResumeAPI(HiringBBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.candidate = _candidate(cls.org, phone='9800000010')

    # test_06
    def test_06_resume_upload_sets_status_and_captures_metadata(self):
        self._auth(self.hr_exec)
        f = SimpleUploadedFile('cv.pdf', b'%PDF-1.4', content_type='application/pdf')
        resp = self.api.post('/api/talent/resumes/', {
            'candidate': self.candidate.pk,
            'file': f,
            'source_type': 'manual_upload',
        }, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['status'], 'uploaded')
        self.assertEqual(resp.data['original_filename'], 'cv.pdf')
        self.assertEqual(resp.data['content_type'], 'application/pdf')
        self.assertGreater(resp.data['size_bytes'], 0)

    # test_07
    def test_07_no_resume_upload_capability_returns_403(self):
        # field_supervisor has no resume.upload
        self._auth(self.no_perm)
        f = SimpleUploadedFile('cv.pdf', b'%PDF-1.4', content_type='application/pdf')
        resp = self.api.post('/api/talent/resumes/', {
            'candidate': self.candidate.pk,
            'file': f,
            'source_type': 'manual_upload',
        }, format='multipart')
        self.assertEqual(resp.status_code, 403)

    # test_08
    def test_08_patch_manual_review_reason(self):
        self._auth(self.hr_exec)
        f = SimpleUploadedFile('cv2.pdf', b'%PDF-1.4', content_type='application/pdf')
        create_resp = self.api.post('/api/talent/resumes/', {
            'candidate': self.candidate.pk,
            'file': f,
            'source_type': 'manual_upload',
        }, format='multipart')
        self.assertEqual(create_resp.status_code, 201)
        rid = create_resp.data['id']

        self._auth(self.hr_admin)
        patch_resp = self.api.patch(f'/api/talent/resumes/{rid}/', {
            'manual_review_reason': 'Scanned PDF — needs OCR verification',
        })
        self.assertEqual(patch_resp.status_code, 200, patch_resp.data)
        self.assertEqual(
            patch_resp.data['manual_review_reason'],
            'Scanned PDF — needs OCR verification',
        )

    # test_09
    def test_09_cross_org_candidate_resume_upload_returns_400(self):
        # Create a candidate in a different org
        other_org = _org('thb-other')
        other_candidate = _candidate(other_org, phone='9800000099')

        self._auth(self.hr_exec)
        f = SimpleUploadedFile('cv3.pdf', b'%PDF-1.4', content_type='application/pdf')
        resp = self.api.post('/api/talent/resumes/', {
            'candidate': other_candidate.pk,
            'file': f,
            'source_type': 'manual_upload',
        }, format='multipart')
        self.assertEqual(resp.status_code, 400)

    # test_10
    def test_10_resume_list_requires_authentication(self):
        resp = self.api.get('/api/talent/resumes/')
        self.assertEqual(resp.status_code, 401)


# ─── Test 11: Pipeline stages ─────────────────────────────────────────────────

class TestPipelineStageAPI(HiringBBase):

    # test_11
    def test_11_pipeline_stage_list_org_scoped_and_ordered(self):
        self._auth(self.hr_exec)
        resp = self.api.get('/api/hiring/pipeline-stages/')
        self.assertEqual(resp.status_code, 200, resp.data)
        results = resp.data['results']
        # Only stages belonging to this org are returned
        org_ids = {r['org'] for r in results}
        self.assertEqual(org_ids, {self.org.pk})
        # Stages are in ascending order
        orders = [r['order'] for r in results]
        self.assertEqual(orders, sorted(orders))
        # No inactive stages
        for r in results:
            self.assertTrue(r['is_active'])


# ─── Tests 12–18: HiringApplication create ────────────────────────────────────

class TestHiringApplicationCreate(HiringBBase):

    # test_12
    def test_12_create_application_derives_org_site_job_role(self):
        candidate = _candidate(self.org, phone='9800000020')
        self._auth(self.hr_exec)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': candidate.pk,
            'mrf': self.approved_mrf.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['org'], self.org.pk)
        self.assertEqual(resp.data['site'], self.site.pk)
        self.assertEqual(resp.data['job_role'], self.job_role.pk)

    # test_13
    def test_13_unapproved_mrf_returns_400(self):
        candidate = _candidate(self.org, phone='9800000021')
        draft_li = MRFLineItem.objects.create(
            mrf=self.draft_mrf, job_role=self.job_role, headcount=1,
        )
        self._auth(self.hr_exec)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': candidate.pk,
            'mrf': self.draft_mrf.pk,
            'mrf_line_item': draft_li.pk,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('mrf', resp.data)

    # test_14
    def test_14_wrong_line_item_returns_400(self):
        # Create a second MRF and use its line item with the first MRF
        other_mrf = _mrf(self.org, self.site, self.hr_admin, status='approved')
        other_li = MRFLineItem.objects.create(
            mrf=other_mrf, job_role=self.job_role, headcount=1,
        )
        candidate = _candidate(self.org, phone='9800000022')
        self._auth(self.hr_exec)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': candidate.pk,
            'mrf': self.approved_mrf.pk,
            'mrf_line_item': other_li.pk,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('mrf_line_item', resp.data)

    # test_15
    def test_15_duplicate_application_returns_400(self):
        candidate = _candidate(self.org, phone='9800000023')
        self._auth(self.hr_exec)
        # First application
        resp1 = self.api.post('/api/hiring/applications/', {
            'candidate': candidate.pk,
            'mrf': self.approved_mrf.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp1.status_code, 201)
        # Duplicate
        resp2 = self.api.post('/api/hiring/applications/', {
            'candidate': candidate.pk,
            'mrf': self.approved_mrf.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp2.status_code, 400)

    # test_16
    def test_16_blacklisted_candidate_returns_400(self):
        blacklisted = _candidate(self.org, phone='9800000024')
        blacklisted.is_blacklisted = True
        blacklisted.blacklist_reason = 'Policy violation'
        blacklisted.save()

        self._auth(self.hr_exec)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': blacklisted.pk,
            'mrf': self.approved_mrf.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('candidate', resp.data)

    # test_17
    def test_17_default_pipeline_stage_assigned(self):
        candidate = _candidate(self.org, phone='9800000025')
        self._auth(self.hr_exec)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': candidate.pk,
            'mrf': self.approved_mrf.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp.status_code, 201, resp.data)
        # Default stage is the one with lowest order
        self.assertIsNotNone(resp.data['current_stage'])
        app = HiringApplication.objects.get(pk=resp.data['id'])
        self.assertEqual(app.current_stage.order, 0)

    # test_18
    def test_18_initial_stage_history_created(self):
        candidate = _candidate(self.org, phone='9800000026')
        self._auth(self.hr_exec)
        resp = self.api.post('/api/hiring/applications/', {
            'candidate': candidate.pk,
            'mrf': self.approved_mrf.pk,
            'mrf_line_item': self.mrf_li.pk,
        })
        self.assertEqual(resp.status_code, 201)
        app_id = resp.data['id']
        history_count = ApplicationStageHistory.objects.filter(
            hiring_application_id=app_id
        ).count()
        self.assertEqual(history_count, 1)
        history = ApplicationStageHistory.objects.get(hiring_application_id=app_id)
        self.assertIsNone(history.from_stage)
        self.assertEqual(history.to_status, 'draft')


# ─── Tests 19–22: move-stage action ──────────────────────────────────────────

class TestHiringApplicationMoveStage(HiringBBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.candidate = _candidate(cls.org, phone='9800000030')
        cls.app = HiringApplication.objects.create(
            org=cls.org,
            candidate=cls.candidate,
            mrf=cls.approved_mrf,
            mrf_line_item=cls.mrf_li,
            site=cls.site,
            job_role=cls.job_role,
            current_stage=cls.stage_sourcing,
            status='draft',
        )

    # test_19
    def test_19_move_stage_updates_stage_status_and_creates_history(self):
        self._auth(self.hr_exec)
        resp = self.api.post(
            f'/api/hiring/applications/{self.app.pk}/move-stage/',
            {
                'stage_id': self.stage_screening.pk,
                'status': 'shortlisted',
                'comment': 'Moved to screening',
            },
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.app.refresh_from_db()
        self.assertEqual(self.app.current_stage_id, self.stage_screening.pk)
        self.assertEqual(self.app.status, 'shortlisted')

        history = ApplicationStageHistory.objects.filter(
            hiring_application=self.app,
            to_stage=self.stage_screening,
        ).last()
        self.assertIsNotNone(history)
        self.assertEqual(history.to_status, 'shortlisted')
        self.assertEqual(history.comment, 'Moved to screening')

    # test_20
    def test_20_move_stage_missing_stage_and_status_returns_400(self):
        self._auth(self.hr_exec)
        resp = self.api.post(
            f'/api/hiring/applications/{self.app.pk}/move-stage/',
            {'comment': 'No target provided'},
        )
        self.assertEqual(resp.status_code, 400)

    # test_21
    def test_21_cross_org_stage_returns_400(self):
        other_org = _org('thb-other2')
        other_stage = _stage(other_org, 'other-stage')
        self._auth(self.hr_exec)
        resp = self.api.post(
            f'/api/hiring/applications/{self.app.pk}/move-stage/',
            {'stage_id': other_stage.pk},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('stage_id', resp.data)

    # test_22
    def test_22_terminal_stage_exit_without_manage_returns_403(self):
        # First put application into the terminal stage directly
        self.app.current_stage = self.stage_offer
        self.app.status = 'offer_released'
        self.app.save(update_fields=['current_stage', 'status'])

        # hr_executive does NOT have hiring_application.manage
        self._auth(self.hr_exec)
        resp = self.api.post(
            f'/api/hiring/applications/{self.app.pk}/move-stage/',
            {'stage_id': self.stage_sourcing.pk, 'status': 'draft'},
        )
        self.assertEqual(resp.status_code, 403)

        # hr_admin DOES have hiring_application.manage
        self._auth(self.hr_admin)
        resp = self.api.post(
            f'/api/hiring/applications/{self.app.pk}/move-stage/',
            {'stage_id': self.stage_sourcing.pk, 'status': 'draft'},
        )
        self.assertEqual(resp.status_code, 200)

        # Reset for other tests
        self.app.refresh_from_db()


# ─── Tests 23–25: Hiring demand ───────────────────────────────────────────────

class TestHiringDemandAPI(HiringBBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Create an application so counts are non-zero
        cls.demand_candidate = _candidate(cls.org, phone='9800000040')
        cls.app_for_demand = HiringApplication.objects.create(
            org=cls.org,
            candidate=cls.demand_candidate,
            mrf=cls.approved_mrf,
            mrf_line_item=cls.mrf_li,
            site=cls.site,
            job_role=cls.job_role,
            status='shortlisted',
        )

    # test_23
    def test_23_demand_endpoint_returns_only_approved_mrfs(self):
        self._auth(self.hr_admin)
        resp = self.api.get('/api/hiring/demands/')
        self.assertEqual(resp.status_code, 200, resp.data)
        results = resp.data['results']
        # All returned items should have an approved MRF
        for item in results:
            mrf = ManpowerRequest.objects.get(pk=item['mrf_id'])
            self.assertEqual(mrf.status, 'approved')

    # test_24
    def test_24_demand_application_counts_by_status(self):
        self._auth(self.hr_admin)
        resp = self.api.get(f'/api/hiring/demands/?mrf={self.approved_mrf.pk}')
        self.assertEqual(resp.status_code, 200)
        results = resp.data['results']
        self.assertGreater(len(results), 0)
        item = next((r for r in results if r['id'] == self.mrf_li.pk), None)
        self.assertIsNotNone(item)
        self.assertGreaterEqual(item['application_count'], 1)
        self.assertGreaterEqual(item['shortlisted_count'], 1)
        self.assertGreaterEqual(item['open_count'], 0)

    # test_25
    def test_25_demand_scope_isolation(self):
        # Create a second org with its own MRF — user from first org should not see it
        other_org = _org('thb-iso')
        _, _, _, other_site = _scope_tree(other_org)
        other_user = _user('thb_iso_su', other_org, is_superuser=True)
        other_mrf = _mrf(other_org, other_site, other_user, status='approved')
        other_jr = JobRole.objects.create(org=other_org, name='Guard ISO', code='guard-iso')
        MRFLineItem.objects.create(mrf=other_mrf, job_role=other_jr, headcount=2)

        self._auth(self.hr_admin)
        resp = self.api.get('/api/hiring/demands/')
        self.assertEqual(resp.status_code, 200)
        result_mrf_ids = {r['mrf_id'] for r in resp.data['results']}
        self.assertNotIn(other_mrf.pk, result_mrf_ids)


# ─── Tests 26–28: Match results ───────────────────────────────────────────────

class TestMatchResultAPI(HiringBBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.match_candidate = _candidate(cls.org, phone='9800000050')
        cls.match_result = CandidateMatchResult.objects.create(
            org=cls.org,
            candidate=cls.match_candidate,
            mrf_line_item=cls.mrf_li,
            final_score=Decimal('85.00'),
            skill_score=Decimal('90.00'),
            role_score=Decimal('80.00'),
            matched_skills=['Python', 'Django'],
            missing_skills=['React'],
            match_source='ai',
            is_auto_match=True,
        )

    # test_26
    def test_26_match_result_returns_score_breakdown(self):
        self._auth(self.hr_admin)
        resp = self.api.get(f'/api/hiring/match-results/{self.match_result.pk}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(str(resp.data['final_score']), '85.00')
        self.assertEqual(str(resp.data['skill_score']), '90.00')
        self.assertEqual(resp.data['matched_skills'], ['Python', 'Django'])
        self.assertEqual(resp.data['missing_skills'], ['React'])

    # test_27
    def test_27_match_result_filter_by_candidate(self):
        self._auth(self.hr_admin)
        resp = self.api.get(
            f'/api/hiring/match-results/?candidate={self.match_candidate.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(self.match_result.pk, ids)

    # test_28
    def test_28_match_result_scope_isolation(self):
        # Other org result should not be visible to hr_admin of this org
        other_org = _org('thb-mr-iso')
        _, _, _, other_site = _scope_tree(other_org)
        other_su = _user('thb_mr_su', other_org, is_superuser=True)
        other_mrf = _mrf(other_org, other_site, other_su, status='approved')
        other_jr = JobRole.objects.create(org=other_org, name='Guard MR', code='guard-mr-iso')
        other_li = MRFLineItem.objects.create(mrf=other_mrf, job_role=other_jr, headcount=1)
        other_c = _candidate(other_org, phone='9800000051')
        other_mr = CandidateMatchResult.objects.create(
            org=other_org, candidate=other_c, mrf_line_item=other_li,
            final_score=Decimal('70.00'),
        )

        self._auth(self.hr_admin)
        resp = self.api.get('/api/hiring/match-results/')
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertNotIn(other_mr.pk, ids)


# ─── Tests 29–30: Regression ──────────────────────────────────────────────────

class TestRegression(HiringBBase):

    # test_29
    def test_29_hiring_application_list_still_works(self):
        self._auth(self.hr_exec)
        resp = self.api.get('/api/hiring/applications/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('results', resp.data)

    # test_30
    def test_30_interview_list_still_works(self):
        self._auth(self.hr_exec)
        resp = self.api.get('/api/hiring/interviews/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('results', resp.data)

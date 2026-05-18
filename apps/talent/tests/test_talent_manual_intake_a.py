"""
apps/talent/tests/test_talent_manual_intake_a.py

Phase Talent-Manual-Intake-A — 18 tests for POST /api/talent/manual-resume-intake/
and the candidate ?skill= filter.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import CANDIDATE_CREATE, HIRING_APP_CREATE
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.hiring.models import ApplicationStageHistory, HiringApplication, PipelineStage
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate, CandidateSkill, Resume


# ─── Helpers ──────────────────────────────────────────────────────────────────

URL = '/api/talent/manual-resume-intake/'


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


def _role(org, code, caps=None):
    r, _ = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})
    bootstrap_role_permissions(r, caps=caps)
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


def _pdf(name='cv.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4', content_type='application/pdf')


def _base_payload(**overrides):
    data = {
        'first_name': 'Test',
        'last_name': 'Candidate',
        'phone': '9900000001',
        'resume_file': _pdf(),
    }
    data.update(overrides)
    return data


# ─── Base setup ───────────────────────────────────────────────────────────────

class IntakeBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('mri')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        # hr_executive has candidate.create + hiring_application.create
        cls.r_hr_exec = _role(cls.org, 'hr_executive')
        # hr_admin has all hiring capabilities
        cls.r_hr_admin = _role(cls.org, 'hr_admin')
        # site_supervisor has no candidate.create
        cls.r_no_cand = _role(cls.org, 'site_supervisor')
        # custom role: candidate.create only — no hiring_application.create
        cls.r_cand_only = _role(cls.org, 'cand-only-mri', caps=[CANDIDATE_CREATE])

        cls.hr_exec = _user('mri_hr_exec', cls.org, cls.r_hr_exec, cls.n_co)
        cls.hr_admin = _user('mri_hr_admin', cls.org, cls.r_hr_admin, cls.n_co)
        cls.no_cand = _user('mri_no_cand', cls.org, cls.r_no_cand, cls.n_co)
        cls.cand_only = _user('mri_cand_only', cls.org, cls.r_cand_only, cls.n_co)
        cls.superuser = _user('mri_super', cls.org, is_superuser=True)

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard MRI', code='guard-mri',
        )
        cls.approved_mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site,
            mrf_type='new_hiring', billing_type='billable',
            status='approved', requested_by=cls.hr_exec,
        )
        cls.draft_mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site,
            mrf_type='new_hiring', billing_type='billable',
            status='draft', requested_by=cls.hr_exec,
        )
        cls.mrf_li = MRFLineItem.objects.create(
            mrf=cls.approved_mrf, job_role=cls.job_role, headcount=5,
        )
        cls.draft_li = MRFLineItem.objects.create(
            mrf=cls.draft_mrf, job_role=cls.job_role, headcount=2,
        )
        cls.stage = PipelineStage.objects.create(
            org=cls.org, name='Sourcing MRI', code='sourcing-mri',
            order=0, stage_type='sourcing',
        )

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)

    def _post(self, payload, format='multipart'):
        return self.api.post(URL, payload, format=format)


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestManualIntakePermissions(IntakeBase):

    # test_01
    def test_01_unauthenticated_returns_401(self):
        resp = self._post(_base_payload())
        self.assertEqual(resp.status_code, 401)

    # test_02
    def test_02_no_candidate_create_returns_403(self):
        self._auth(self.no_cand)
        resp = self._post(_base_payload())
        self.assertEqual(resp.status_code, 403)

    # test_17
    def test_17_cand_create_only_with_mrf_fields_returns_403(self):
        # User has candidate.create but NOT hiring_application.create
        self._auth(self.cand_only)
        payload = _base_payload(
            phone='9900000017',
            mrf_line_item=self.mrf_li.pk,
        )
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 403)


class TestManualIntakeCreateCandidate(IntakeBase):

    # test_03
    def test_03_creates_candidate_and_resume(self):
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(phone='9900000003'))
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn('candidate', resp.data)
        self.assertIn('resume', resp.data)
        self.assertEqual(resp.data['candidate']['first_name'], 'Test')
        self.assertEqual(resp.data['candidate']['org'], self.org.pk)
        self.assertTrue(
            Candidate.objects.filter(org=self.org, phone_normalized='9900000003').exists()
        )

    # test_04
    def test_04_existing_phone_updates_candidate_not_duplicate(self):
        self._auth(self.hr_exec)
        # First intake — create
        self._post(_base_payload(phone='9900000004', current_role='Guard'))
        count_before = Candidate.objects.filter(org=self.org, phone_normalized='9900000004').count()
        self.assertEqual(count_before, 1)

        # Second intake — same phone, different role
        self._post(_base_payload(phone='9900000004', current_role='Supervisor'))
        count_after = Candidate.objects.filter(org=self.org, phone_normalized='9900000004').count()
        self.assertEqual(count_after, 1)  # still one candidate

        candidate = Candidate.objects.get(org=self.org, phone_normalized='9900000004')
        self.assertEqual(candidate.current_role, 'Supervisor')  # updated

    # test_05
    def test_05_blank_fields_do_not_overwrite_existing_nonempty(self):
        self._auth(self.hr_exec)
        # Create with current_role
        self._post(_base_payload(phone='9900000005', current_role='Guard'))
        candidate = Candidate.objects.get(org=self.org, phone_normalized='9900000005')
        self.assertEqual(candidate.current_role, 'Guard')

        # Second intake — same phone, current_role omitted (blank)
        payload = _base_payload(phone='9900000005')  # no current_role
        self._post(payload)

        candidate.refresh_from_db()
        self.assertEqual(candidate.current_role, 'Guard')  # preserved


class TestManualIntakeSkills(IntakeBase):

    # test_06
    def test_06_comma_separated_skills_create_candidateskill_rows(self):
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(
            phone='9900000006',
            skills='housekeeping, cleaning, floor care',
        ))
        self.assertEqual(resp.status_code, 201, resp.data)
        candidate = Candidate.objects.get(org=self.org, phone_normalized='9900000006')
        skill_names = set(candidate.skills.values_list('normalized_skill_name', flat=True))
        self.assertIn('housekeeping', skill_names)
        self.assertIn('cleaning', skill_names)
        self.assertIn('floor care', skill_names)
        self.assertEqual(len(resp.data['skills']), 3)

    # test_07
    def test_07_repeated_skill_does_not_duplicate(self):
        self._auth(self.hr_exec)
        # First intake
        self._post(_base_payload(phone='9900000007', skills='cooking'))
        # Second intake same phone, same skill
        self._post(_base_payload(phone='9900000007', skills='cooking'))
        candidate = Candidate.objects.get(org=self.org, phone_normalized='9900000007')
        count = candidate.skills.filter(normalized_skill_name='cooking').count()
        self.assertEqual(count, 1)


class TestManualIntakeResume(IntakeBase):

    # test_08
    def test_08_resume_status_is_uploaded(self):
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(phone='9900000008'))
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['resume']['status'], 'uploaded')

    # test_09
    def test_09_resume_source_type_is_recruiter_upload(self):
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(phone='9900000009'))
        self.assertEqual(resp.status_code, 201)
        self.assertIn(resp.data['resume']['source_type'], ('recruiter_upload', 'manual_upload'))

    # test_10
    def test_10_file_metadata_saved(self):
        self._auth(self.hr_exec)
        f = SimpleUploadedFile('test_cv.pdf', b'%PDF-1.4 binary', content_type='application/pdf')
        payload = _base_payload(phone='9900000010', resume_file=f)
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['resume']['original_filename'], 'test_cv.pdf')
        self.assertEqual(resp.data['resume']['content_type'], 'application/pdf')
        self.assertGreater(resp.data['resume']['size_bytes'], 0)


class TestManualIntakeHiringApplication(IntakeBase):

    # test_11
    def test_11_mrf_line_item_creates_hiring_application(self):
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(
            phone='9900000011',
            mrf=self.approved_mrf.pk,
            mrf_line_item=self.mrf_li.pk,
        ))
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNotNone(resp.data['hiring_application'])
        self.assertEqual(resp.data['hiring_application']['mrf'], self.approved_mrf.pk)
        self.assertTrue(
            HiringApplication.objects.filter(
                candidate__phone_normalized='9900000011',
                mrf_line_item=self.mrf_li,
            ).exists()
        )

    # test_12
    def test_12_mrf_derived_from_line_item(self):
        # Send mrf_line_item but NOT mrf — service derives mrf from line item
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(
            phone='9900000012',
            mrf_line_item=self.mrf_li.pk,
            # no mrf field
        ))
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNotNone(resp.data['hiring_application'])
        app = HiringApplication.objects.get(candidate__phone_normalized='9900000012')
        self.assertEqual(app.mrf_id, self.approved_mrf.pk)

    # test_13
    def test_13_unapproved_mrf_returns_400(self):
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(
            phone='9900000013',
            mrf_line_item=self.draft_li.pk,
        ))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('mrf', resp.data)

    # test_14
    def test_14_duplicate_candidate_line_item_returns_400(self):
        self._auth(self.hr_exec)
        # First application
        self._post(_base_payload(
            phone='9900000014',
            mrf_line_item=self.mrf_li.pk,
        ))
        # Second application same candidate + line item
        resp = self._post(_base_payload(
            phone='9900000014',
            mrf_line_item=self.mrf_li.pk,
        ))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('mrf_line_item', resp.data)

    # test_15
    def test_15_cross_org_mrf_returns_400(self):
        # Create an MRF in a completely different org
        other_org = _org('mri-xorg')
        _, _, _, other_site = _scope_tree(other_org)
        other_su = _user('mri_xorg_su', other_org, is_superuser=True)
        other_mrf = ManpowerRequest.objects.create(
            org=other_org, site=other_site,
            mrf_type='new_hiring', billing_type='billable',
            status='approved', requested_by=other_su,
        )
        other_jr = JobRole.objects.create(org=other_org, name='Guard XOrg', code='guard-xorg')
        other_li = MRFLineItem.objects.create(
            mrf=other_mrf, job_role=other_jr, headcount=1,
        )

        self._auth(self.hr_exec)
        resp = self._post(_base_payload(
            phone='9900000015',
            mrf_line_item=other_li.pk,
        ))
        self.assertEqual(resp.status_code, 400)

    # test_16
    def test_16_initial_stage_history_created(self):
        self._auth(self.hr_exec)
        resp = self._post(_base_payload(
            phone='9900000016',
            mrf_line_item=self.mrf_li.pk,
        ))
        self.assertEqual(resp.status_code, 201)
        app_id = resp.data['hiring_application']['id']
        history_qs = ApplicationStageHistory.objects.filter(hiring_application_id=app_id)
        self.assertEqual(history_qs.count(), 1)
        history = history_qs.first()
        self.assertIsNone(history.from_stage)
        self.assertEqual(history.from_status, '')
        self.assertEqual(history.to_status, 'draft')


# ─── Test 18: ?skill= filter on candidate list ────────────────────────────────

class TestCandidateSkillFilter(IntakeBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Create a candidate with a known skill
        cls.skilled_candidate = Candidate.objects.create(
            org=cls.org,
            phone='9900000018',
            phone_normalized='9900000018',
            first_name='Skilled',
            last_name='Worker',
            source='manual',
        )
        CandidateSkill.objects.create(
            candidate=cls.skilled_candidate,
            skill_name='Housekeeping',
            normalized_skill_name='housekeeping',
            source='manual',
        )
        cls.unskilled_candidate = Candidate.objects.create(
            org=cls.org,
            phone='9900000019',
            phone_normalized='9900000019',
            first_name='Unskilled',
            last_name='Worker',
            source='manual',
        )

    # test_18
    def test_18_skill_filter_returns_only_matching_candidates(self):
        self._auth(self.hr_exec)
        resp = self.api.get('/api/talent/candidates/?skill=housekeeping')
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(self.skilled_candidate.pk, ids)
        self.assertNotIn(self.unskilled_candidate.pk, ids)
